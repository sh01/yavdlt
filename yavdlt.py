#!/usr/bin/env python3
# Yet Another Video Download Tool: Download information from youtube
# Copyright (C) 2009,2010,2013 Sebastian Hagen
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
if (sys.version_info[0] < 3):
   # No point in going any further; we'd just fail with a more cryptic error message a few lines later.
   raise Exception("This is a python 3 script; it's not compatible with older interpreters.")

import collections
from collections import deque, OrderedDict
import html.parser
import http.cookiejar
import logging
import os
import os.path
from urllib.error import URLError
import urllib.request
import re
import xml.dom.minidom

# ---------------------------------------------------------------- General helper functions

xml_unescape = html.parser.HTMLParser().unescape

def escape_decode(s):
   from codecs import escape_decode
   return escape_decode(s.encode('utf-8'))[0].decode('utf-8')

def get_http_encoding(req, default_encoding):
   ct = req.getheader('Content-Type')
   if (ct is None):
      return default_encoding
   
   ct_map = {}
   for elem in ct.split(';'):
      if not ('=' in elem):
         continue
      (key, val) = elem.strip().split('=', 1)
      ct_map[key] = val
   
   try:
      rv = ct_map['charset']
   except KeyError:
      rv = default_encoding
   return rv

def req2str(req, default_encoding='utf-8'):
   data = req.read()
   return data.decode(get_http_encoding(req, default_encoding))

# ---------------------------------------------------------------- ASS sub building code
def make_ass_color(r,g,b,a):
   for val in (r,g,b,a):
      if (val > 255):
         raise ValueError('Value {0} from args {1} is invalid; expected something in range [0,255].'.format(val, (r,g,b,a)))
   
   return (a << 24) + (r << 16) + (g << 8) + b

# ASSStyle helper function; defined here because of python 3.x class scope quirks.
def _fe_make(a,b,c=None,d=lambda b:b, *, __FE=collections.namedtuple('FieldEntry', ('name', 'ass_name', 'default_val', 'fvc'))):
   return __FE(a,b,c,d)

class ASSStyle:
   # field entry type

   # Field value output converters
   _fvc_bool = (lambda b: '{0:d}'.format(-1*b))
   _fvc_int = '{0:d}'.format
   _fvc_float = '{0:f}'.format
   _fvc_perc = (lambda p: '{0:.2f}'.format(p*100))
   
   # Field entries
   fields = [_fe_make(*x) for x in (
      ('name', 'Name'),
      ('fontname', 'Fontname', None),
      ('fontsize', 'Fontsize', 20, _fvc_int),
      ('color1', 'PrimaryColour', make_ass_color(255,255,255,0), _fvc_int),
      ('color2', 'SecondaryColour', make_ass_color(223,223,223,0), _fvc_int),
      ('color3', 'OutlineColour', make_ass_color(0,0,0,0), _fvc_int),
      ('color_bg', 'BackColour', make_ass_color(0,0,0,0), _fvc_int),
      ('fs_bold', 'Bold', False, _fvc_bool),
      ('fs_italic', 'Italic', False, _fvc_bool),
      ('fs_underline', 'Underline', False, _fvc_bool),
      ('fs_strikeout', 'Strikeout', False, _fvc_bool),
      ('scale_x', 'ScaleX', 1, _fvc_perc),
      ('scale_y', 'ScaleY', 1, _fvc_perc),
      ('spacing', 'Spacing', 0, _fvc_int),
      ('angle', 'Angle', 0, _fvc_float),
      ('borderstyle', 'Borderstyle', 1, _fvc_int),
      ('outline', 'Outline', 2, _fvc_int),
      ('shadow', 'Shadow', 0, _fvc_int),
      ('alignment', 'Alignment', 2, _fvc_int),
      ('margin_l', 'MarginL', 0, _fvc_int),
      ('margin_r', 'MarginR', 0, _fvc_int),
      ('margin_v', 'MarginV', 10, _fvc_int),
      ('encoding', 'Encoding', 1, _fvc_int)
   )]
   
   ASS_FIELD_NAMES = tuple(x.ass_name for x in fields)
   field_map = dict((x.name,x) for x in fields)
   
   def __init__(self, name, **kwargs):
      self.name = name
      for (key, val) in kwargs.items():
         if not (key in self.field_map):
            raise ValueErrror('Unknown argument {0!a}={1!a}.'.format(key, val))
         setattr(self, key, val)
      
      for f in self.field_map.values():
         if hasattr(self, f.name):
            continue
         setattr(self, f.name, f.default_val)
   
   def _get_values(self):
      for field in self.fields:
         val = getattr(self, field.name)
         if (val is None):
            yield ''
         else:
            yield field.fvc(val)
   
   def fmt_as_ass_line(self):
      return 'Style: {0}'.format(','.join(self._get_values()))
      
   def get_values(self):
      return tuple(getattr(self, f.name) for f in self.fields if (f.name != 'name'))
      
del(_fe_make)

def _second2ass_ts(seconds):
   hours = int(seconds//3600)
   seconds %= 3600
   minutes = int(seconds//60)
   seconds %= 60
   return '{0:d}:{1:02d}:{2:05.2f}'.format(hours, minutes, seconds)


class ASSSubtitle:
   layer = 0
   name = None
   margin_l = 0
   margin_r = 0
   margin_v = 0
   
   def __init__(self, start, dur, text, style):
      self.start = start
      self.dur = dur
      self.text = text
      self.style = style
   
   @classmethod
   def new(cls, style_maker, *args, **kwargs):
      return cls(*args, style=style_maker(), **kwargs)
   
   def __cmp__(self, other):
      if (self.start < other.start): return -1
      if (self.start > other.start): return 1
      if (self.dur < other.dur): return -1
      if (self.dur > other.dur): return 1
      if (self.name is not None is not other.name):
         if (self.name < other.name): return -1
         if (self.name > other.name): return 1
      
      if (self.text < other.text): return -1
      if (self.text > other.text): return 1
      if (id(self) < id(other)): return -1
      if (id(self) > id(other)): return 1
      return 0

   def __eq__(self, other):
      return (self.__cmp__(other) == 0)
   def __ne__(self, other):
      return (self.__cmp__(other) != 0)
   def __lt__(self, other):
      return (self.__cmp__(other) < 0)
   def __gt__(self, other):
      return (self.__cmp__(other) > 0)
   def __le__(self, other):
      return (self.__cmp__(other) <= 0)
   def __ge__(self, other):
      return (self.__cmp__(other) >= 0)

   def get_body(self):
      return self.text.replace('\n','\\N')
   
   def _get_name(self):
      if (self.name is None):
         return ''
      return self.name.replace('\n','_').replace(',','_').replace('\x00','_')
   
   ASS_FIELD_NAMES = ('Layer', 'Start', 'End', 'Style', 'Name', 'MarginL', 'MarginR', 'MarginV', 'Effect', 'Text')
   def get_line_ass_standalone(self):
      """Return line for this sub as it would appear in a standalone ASS file."""
      return 'Dialogue: ' + ','.join(str(x) for x in (self.layer,_second2ass_ts(self.start),
         _second2ass_ts(self.start+self.dur), self.style.name, self._get_name(), self.margin_l, self.margin_r, self.margin_v, '',
         self.get_body()))
   
   def get_line_ass_mkv(self, ro):
      """Return line for this sub as it would appear in a data block in a MKV file."""
      return ','.join(str(x) for x in (ro, self.layer, self.style.name, self._get_name(), self.margin_l,
         self.margin_r, self.margin_v, '', self.get_body()))
      

class ASSSubSet:
   style_cls = ASSStyle   
   def __init__(self, name=None, lc=None):
      self.subs = []
      self.styles = OrderedDict()
      self._style_i = 0
      self.name = name
      if (lc is None):
         lc = 'und'
      self.lc = lc
   
   def contains_nonempty_subs(self):
      for sub in self.subs:
         if (len(sub.get_body()) > 0):
            return True
      return False
   
   def _get_style_name(self):
      rv = 'Style{0:d}'.format(self._style_i)
      self._style_i += 1
      return rv
   
   def make_style(self, *args, **kwargs):
      style = self.style_cls(None, *args, **kwargs)
      key = style.get_values()
      try:
         rv = self.styles[key]
      except KeyError:
         rv = style
         rv.name = self._get_style_name()
         self.styles[key] = rv
      return rv
      
   def add_subs_from_yt_tt(self, content):
      dom = xml.dom.minidom.parseString(content)
      subs = deque()
      for node in dom.getElementsByTagName('text'):
         if (node.firstChild is None):
            text = ''
         else:
            text = xml_unescape(node.firstChild.nodeValue)
         
         try:
            dur = float(node.attributes['dur'].value)
         except KeyError:
            # This is very rare, and I have no idea what the actual meaning
            # of this construct is. Defaulting to 0 until we get a better
            # understanding of this case.
            dur = 0.0
         subs.append(ASSSubtitle.new(
            self.make_style,
            float(node.attributes['start'].value),
            dur,
            text
         ))
      for sub in subs:
         self.subs.append(sub)

   def add_subs_from_yt_annotations(self, annotations, **kwargs):
      for anno in annotations:
         sub = anno.get_sub(self.make_style, **kwargs)
         if not (sub is None):
            self.subs.append(sub)

   def _get_header(self):
      return b'\r\n'.join((
         b'\xef\xbb\xbf[Script Info]', # Include UTF-8 BOM; according to user reports some players care about this
         b'ScriptType: v4.00+',
         b'\r\n[V4+ Styles]',
         'Format: {0}'.format(', '.join(self.style_cls.ASS_FIELD_NAMES)).encode('utf-8')
       ) + tuple(style.fmt_as_ass_line().encode('utf-8') for style in self.styles.values()) + (b'',))
   
   def _get_header2(self, fn):
      return b''.join((b'\r\n[Events]\r\nFormat: ', ', '.join(fn).encode('utf-8'), b'\r\n\r\n'))
   
   def write_to_file(self, f):
      self.subs.sort()
      f.write(self._get_header())
      f.write(self._get_header2(ASSSubtitle.ASS_FIELD_NAMES))
      for sub in self.subs:
         f.write(sub.get_line_ass_standalone().encode('utf-8'))
         f.write(b'\r\n')
   
   def _iter_subs_mkv(self, tcs):
      from mcio_base import DataRefBytes
      self.subs.sort()
      cf = 10**9/tcs
      i = 1
      for sub in self.subs:
         data_r = DataRefBytes(sub.get_line_ass_mkv(i).encode('utf-8'))
         yield (int(sub.start*cf), int(sub.dur*cf), data_r, True)
         i += 1
   
   def mkv_add_track(self, mkvb, flag_default=False):
      from mcio_codecs import CODEC_ID_ASS
      cpd = self._get_header() + self._get_header2(ASSSubtitle.ASS_FIELD_NAMES)
      mkvb.add_track(self._iter_subs_mkv(mkvb.tcs), mkvb.TRACKTYPE_SUB, CODEC_ID_ASS, cpd, False, track_name=self.name, track_lang=self.lc, flag_default=flag_default)


YTAnnotationRRBase = collections.namedtuple('YTAnnotationRRBase', ('t','x','y','w','h','d'))
class YTAnnotationRR(YTAnnotationRRBase):
   @classmethod
   def build_from_xmlnode(cls, node):
      kwargs = {}
      for name in cls._fields:
         try:
            strval = node.attributes[name].nodeValue
         except KeyError:
            kwargs[name] = None
            continue
         
         if (name == 't'):
            if (strval == 'never'):
               nval = None
            else:
               s = strval
               t = 0
               f = 1
               for i in range(3):
                 if (not s):
                   break
                 if (':' in s):
                   (s,tail) = s.rsplit(':',1)
                 else:
                   tail = s
                   s = ''
                 t += float(tail)*f
                 f *= 60

               nval = t
         else:
            nval = float(strval)
         kwargs[name] = nval
      return cls(**kwargs)

YTAnnotationAppearanceBase = collections.namedtuple('YTAnnotationAppearance', ('fgColor', 'bgColor', 'borderColor',
   'borderWidth', 'bgAlpha', 'borderAlpha', 'gloss', 'highlightFontColor', 'highlightWidth'))

class YTAnnotationAppearence(YTAnnotationAppearanceBase):
   @classmethod
   def build_from_xmlnode(cls, node):
      kwargs = {}
      for name in cls._fields:
         try:
            kwargs[name] = node.attributes[name].nodeValue
         except KeyError:
            kwargs[name] = None
            continue
      
      return cls(**kwargs)
   
   def _get_num(self, name, base=None):
      val = getattr(self, name)
      if (val is None):
         return 0
      return val
   
   def get_style(self):
      rv = {}
      if not (self.fgColor is None):
         rv['color1'] = int(self.fgColor, 16)
      
      #color3 = int(self.borderColor, 16)
      #if not (color3 is None):
         #color3 |= round(float(1-self._get_num('borderAlpha'))*255) << 24
      
      #color_bg = int(self.bgColor, 16)
      #if not (color_bg is None):
         #color_bg |= round(float(1-self._get_num('bgAlpha'))*255) << 24
      
      return rv


YTAnnotationBase = collections.namedtuple('YTAnnotationBase', ('id','author','type','content', 'style', 'r1', 'r2', 'appearance', 'yt_spam_score', 'yt_spam_flag', 'urls'))
class YTAnnotation(YTAnnotationBase):
   @classmethod
   def build_from_xmlnode(cls, node):
      kwargs = {}
      attrs = dict(node.attributes)
      for name in cls._fields:
         if (name in attrs):
            kwargs[name] = attrs[name].nodeValue
         else:
            kwargs[name] = None
      
      regions = []
      for tag in ('rectRegion','anchoredRegion'):
         regions += node.getElementsByTagName(tag)
      
      if (len(regions) >= 1):
         kwargs['r1'] = YTAnnotationRR.build_from_xmlnode(regions[0])
      
      if (len(regions) >= 2):
         kwargs['r2'] = YTAnnotationRR.build_from_xmlnode(regions[1])
      
      if (kwargs['type'] == 'text'):
         tns = node.getElementsByTagName('TEXT')
         if (tns):
            fc = tns[0].firstChild
            if (fc is None):
               text = ''
            else:
               text = fc.nodeValue
            kwargs['content'] = text
         else:
            kwargs['content'] = None
      else:
         kwargs['content'] = None
      
      atags = node.getElementsByTagName('appearance')
      if (atags):
         kwargs['appearance'] = YTAnnotationAppearence.build_from_xmlnode(atags[0])
      else:
         kwargs['appearance'] = None
      
      kwargs['urls'] = urls = []
      for actt in node.getElementsByTagName('action'):
         for urlt in actt.getElementsByTagName('url'):
            try:
               urls.append(urlt.attributes['value'].value)
            except KeyError:
               pass
      
      kwargs['yt_spam_score'] = None
      kwargs['yt_spam_flag'] = False
      for mdn in node.getElementsByTagName('metadata'):
         if ('yt_spam_score' in mdn.attributes):
            kwargs['yt_spam_score'] = float(mdn.attributes['yt_spam_score'].value)
         if ('yt_spam_flag' in mdn.attributes):
            kwargs['yt_spam_flag'] = (mdn.attributes['yt_spam_flag'].value == 'true')
      
      return cls(**kwargs)
   
   def is_spam(self):
      return (self.yt_spam_flag)
   
   def _get_style_kwargs(self):
      if (self.appearance):
         return self.appearance.get_style()
      
      return dict()
   
   def get_sub(self, style_maker, filter_spam=False):
      if ((self.content is None) or
         (self.r1 is None) or
         (self.r2 is None) or
         (self.r1.t is None) or
         (self.r2.t is None)):
         # Non-sublike annotation
         return None
      
      if (filter_spam and self.is_spam()):
         # We can do without this.
         return None
      
      style = style_maker(**self._get_style_kwargs())
      
      rv = ASSSubtitle(self.r1.t, self.r2.t-self.r1.t, self.content, style)
      if not (self.author is None):
         rv.name = self.author
      return rv

def parse_ytanno(f):
   import xml.dom.minidom
   domtree = xml.dom.minidom.parse(f)
   anno_nodes = domtree.getElementsByTagName('annotation')
   annotations = [YTAnnotation.build_from_xmlnode(n) for n in anno_nodes]
   annotations.sort()
   return annotations

# ---------------------------------------------------------------- Youtube interface code
class YTError(Exception):
   pass

class YTLoginRequired(YTError):
   pass

class YTDefaultFmt:
   def __str__(self):
      return 'default'

FMT_DEFAULT = YTDefaultFmt()

DATATYPE_VIDEO = 1
DATATYPE_TIMEDTEXT = 4
DATATYPE_ANNOTATIONS = 8


class YTimedTextList:
   logger = logging.getLogger('YTimedTextList')
   log = logger.log
   
   # ISO code translation table is based on <http://www.loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt>.
   # Having it here is pretty ugly, but atm I don't have any other uses for it, and moving it into a seperate package and then
   # having yavdlt depend on it would be additional pain without an upside.
   ISO_693_1to2 = dict([('aa', 'aar'), ('ab', 'abk'), ('af', 'afr'), ('ak', 'aka'), ('sq', 'alb'), ('am', 'amh'), ('ar', 'ara'), ('an', 'arg'), ('hy', 'arm'), ('as', 'asm'), ('av', 'ava'), ('ae', 'ave'), ('ay', 'aym'), ('az', 'aze'), ('ba', 'bak'), ('bm', 'bam'), ('eu', 'baq'), ('be', 'bel'), ('bn', 'ben'), ('bh', 'bih'), ('bi', 'bis'), ('bs', 'bos'), ('br', 'bre'), ('bg', 'bul'), ('my', 'bur'), ('ca', 'cat'), ('ch', 'cha'), ('ce', 'che'), ('zh', 'chi'), ('cu', 'chu'), ('cv', 'chv'), ('kw', 'cor'), ('co', 'cos'), ('cr', 'cre'), ('cs', 'cze'), ('da', 'dan'), ('dv', 'div'), ('nl', 'dut'), ('dz', 'dzo'), ('en', 'eng'), ('eo', 'epo'), ('et', 'est'), ('ee', 'ewe'), ('fo', 'fao'), ('fj', 'fij'), ('fi', 'fin'), ('fr', 'fre'), ('fy', 'fry'), ('ff', 'ful'), ('ka', 'geo'), ('de', 'ger'), ('gd', 'gla'), ('ga', 'gle'), ('gl', 'glg'), ('gv', 'glv'), ('el', 'gre'), ('gn', 'grn'), ('gu', 'guj'), ('ht', 'hat'), ('ha', 'hau'), ('he', 'heb'), ('hz', 'her'), ('hi', 'hin'), ('ho', 'hmo'), ('hr', 'hrv'), ('hu', 'hun'), ('ig', 'ibo'), ('is', 'ice'), ('io', 'ido'), ('ii', 'iii'), ('iu', 'iku'), ('ie', 'ile'), ('ia', 'ina'), ('id', 'ind'), ('ik', 'ipk'), ('it', 'ita'), ('jv', 'jav'), ('ja', 'jpn'), ('kl', 'kal'), ('kn', 'kan'), ('ks', 'kas'), ('kr', 'kau'), ('kk', 'kaz'), ('km', 'khm'), ('ki', 'kik'), ('rw', 'kin'), ('ky', 'kir'), ('kv', 'kom'), ('kg', 'kon'), ('ko', 'kor'), ('kj', 'kua'), ('ku', 'kur'), ('lo', 'lao'), ('la', 'lat'), ('lv', 'lav'), ('li', 'lim'), ('ln', 'lin'), ('lt', 'lit'), ('lb', 'ltz'), ('lu', 'lub'), ('lg', 'lug'), ('mk', 'mac'), ('mh', 'mah'), ('ml', 'mal'), ('mi', 'mao'), ('mr', 'mar'), ('ms', 'may'), ('mg', 'mlg'), ('mt', 'mlt'), ('mn', 'mon'), ('na', 'nau'), ('nv', 'nav'), ('nr', 'nbl'), ('nd', 'nde'), ('ng', 'ndo'), ('ne', 'nep'), ('nn', 'nno'), ('nb', 'nob'), ('no', 'nor'), ('ny', 'nya'), ('oc', 'oci'), ('oj', 'oji'), ('or', 'ori'), ('om', 'orm'), ('os', 'oss'), ('pa', 'pan'), ('fa', 'per'), ('pi', 'pli'), ('pl', 'pol'), ('pt', 'por'), ('ps', 'pus'), ('qu', 'que'), ('rm', 'roh'), ('ro', 'rum'), ('rn', 'run'), ('ru', 'rus'), ('sg', 'sag'), ('sa', 'san'), ('si', 'sin'), ('sk', 'slo'), ('sl', 'slv'), ('se', 'sme'), ('sm', 'smo'), ('sn', 'sna'), ('sd', 'snd'), ('so', 'som'), ('st', 'sot'), ('es', 'spa'), ('sc', 'srd'), ('sr', 'srp'), ('ss', 'ssw'), ('su', 'sun'), ('sw', 'swa'), ('sv', 'swe'), ('ty', 'tah'), ('ta', 'tam'), ('tt', 'tat'), ('te', 'tel'), ('tg', 'tgk'), ('tl', 'tgl'), ('th', 'tha'), ('bo', 'tib'), ('ti', 'tir'), ('to', 'ton'), ('tn', 'tsn'), ('ts', 'tso'), ('tk', 'tuk'), ('tr', 'tur'), ('tw', 'twi'), ('ug', 'uig'), ('uk', 'ukr'), ('ur', 'urd'), ('uz', 'uzb'), ('ve', 'ven'), ('vi', 'vie'), ('vo', 'vol'), ('cy', 'wel'), ('wa', 'wln'), ('wo', 'wol'), ('xh', 'xho'), ('yi', 'yid'), ('yo', 'yor'), ('za', 'zha'), ('zu', 'zul')])
   
   # Deprecated langcode map is based on <http://www.iana.org/assignments/language-subtag-registry>.
   DEP_LC_MAP = dict([('in', 'id'), ('iw', 'he'), ('ji', 'yi'), ('jw', 'jv'), ('mo', 'ro')])
   
   def __init__(self, vid, tdata):
      self.vid = vid
      self.tdata = tdata
   
   @classmethod
   def build_from_markup(cls, vid, text):
      dom = xml.dom.minidom.parseString(text)
      tdata = []
      for track in dom.firstChild.childNodes:
         tdata.append((track.attributes['name'].value, track.attributes['lang_code'].value))
      
      return cls(vid, tuple(tdata))
   
   def get_url(self, name, lc):
      if (isinstance(name, bytes)):
         name = name.decode('ascii')
      return 'http://video.google.com/timedtext?hl=en&v={0}&type=track&name={1}&lang={2}'.format(self.vid,
         urllib.parse.quote(name), urllib.parse.quote(lc))
   
   def fetch_all_blocking(self, url_mangler):
      rv = []
      for (name, lc) in self.tdata:
         url = url_mangler(self.get_url(name, lc))
         self.log(20, 'Fetching timedtext data from {0!a} and processing.'.format(url))
         req = urllib.request.urlopen(url)
         content = req.read()
         
         if (name == ''):
            name = None
         
         lc_orig = lc
         if (lc == ''):
            lc2 = None
         else:
            # Remove subtags; we only care about the top-level code here.
            (lc, *__junk) = lc.split('-',1)
            
            if ((lc in self.DEP_LC_MAP) and not (lc in self.ISO_693_1to2)):
               # YT has been known to use deprecated language codes from time to time; map them to their preferred values
               # here.
               lc = self.DEP_LC_MAP[lc]
            
            try:
               lc2 = self.ISO_693_1to2[lc]
            except KeyError:
               self.log(30, 'Unknown presumed ISO 693-1 lang code {0!a} (from {1!a}); marking as unknown.'.format(lc, lc_orig))
               lc2 = None
         
         ss = ASSSubSet(name, lc2)
         ss.add_subs_from_yt_tt(content)
         if (ss.contains_nonempty_subs()):
            rv.append(ss)
         else:
            self.log(20, 'Subset with lc {0!a} and name {1!a} contained no non-empty subs; discarding.'.format(lc_orig, name))
         
      return rv


def _split_yt_dictstring(dstr):
   from urllib.parse import splitvalue, unquote, unquote_plus

   def uqv(d):
      (key, val) = d
      return (key, unquote_plus(val))

   def uqv(d):
      (key, val) = d
      return (key, unquote_plus(val))
      
   return dict(uqv(splitvalue(cfrag)) for cfrag in dstr.split('&'))


class YTVideoRef:
   re_title = re.compile(b'<meta name="title" content="(?P<text>[^"]*?)">')
   re_err = re.compile(b'<div[^>]* id="error-box"[^>]*>.*?<div[^>]* class="yt-alert-content"[^>]*>(?P<text>.*?)</div>', re.DOTALL)
   re_err_age = re.compile(b'<div id="verify-age-details">(?P<text>.*?)</div>', re.DOTALL)
   re_fmt_playerconfig = re.compile("""ytplayer.config = (?P<text>[^ ].*});""")
   re_fmt_url_map_markup = re.compile(r'\? "(?P<umm>.*?fmt_url_map=.*?>)"')
   re_fmt_url_html5 = re.compile('videoPlayer.setAvailableFormat\("(?P<url>[^"]+)", "(?P<mime_type>video/[^"/ \t;]*);[^"]*", "[^"]*", "(?P<fmt>[0-9]+)"\);')
   re_fmt_stream_map = re.compile('url_encoded_fmt_stream_map=(?P<ms>[^"&]+)&')
   
   URL_FMT_WATCH = 'http://www.youtube.com/watch?v={0}&has_verified=1'
   URL_FMT_GETVIDEOINFO = 'http://www.youtube.com/get_video_info?video_id={0}'
   
   logger = logging.getLogger('YTVideoRef')
   log = logger.log
   
   MT_EXT_MAP = {
      'video/mp4': 'mp4',
      'video/3gpp': 'mp4',
      'video/x-flv': 'flv',
      'video/webm': 'webm'
   }
   
   MT_PARSERMODULE_MAP = {
      'video/mp4': 'mcde_mp4',
      'video/3gpp': 'mcde_mp4',
      'video/x-flv': 'mcde_flv',
      'video/webm': 'mcio_matroska'
   }
   
   _track_type_map = {
      'a': ('TRACKTYPE_AUDIO', 'audio'),
      'v': ('TRACKTYPE_VIDEO', 'video'),  
   }
   
   def __init__(self, vid, format_pref_list, dl_path_tmp, dl_path_final, make_mkv, try_html5=False, drop_tt='', uhl=()):
      self._tried_md_fetch = False
      self.vid = vid
      self._mime_type = None
      self.fmt_stream_map = {}
      self.got_video_info = False
      self.title = None
      self.fpl = format_pref_list
      self.dlp_tmp = dl_path_tmp
      self.dlp_final = dl_path_final
      self._content_direct_url = None
      self._fmt = None
      self.make_mkv = make_mkv
      self._cookiejar = http.cookiejar.CookieJar()
      self._cookiejar.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
      self._try_html5 = try_html5
      
      uhl = list(uhl)
      uhl.append(urllib.request.HTTPCookieProcessor(self._cookiejar))
      
      self._url_opener = urllib.request.build_opener(*uhl)
      for tt in drop_tt:
         if not (tt in self._track_type_map):
            raise ValueError('Unknown track type {!r}.'.format(tt))
      self.drop_tt = ''.join(sorted(set(drop_tt)))
   
   @staticmethod
   def _make_html5_optin_cookie():
      from http.cookiejar import Cookie
      return Cookie(version=0, name='PREF', value='f2=40000000', port=None, port_specified=False, domain='.youtube.com',
         domain_specified=True, domain_initial_dot=True, path='/', path_specified=True, secure=False, expires=None,
         discard=True, comment=None, comment_url=None, rest={}, rfc2109=False)
   
   def urlopen(self, url, *args, html5=False, mangle=True, **kwargs):
      """Open specified url, performing mangling if necessary, and return urllib response object."""
      if (mangle):
         url = self.mangle_yt_url(url)
      req = urllib.request.Request(url, *args, **kwargs)
      if (html5):
         cj = http.cookiejar.CookieJar()
         cj.set_cookie(self._make_html5_optin_cookie())
         cj.add_cookie_header(req)
         
      rv = self._url_opener.open(req)
      return rv
   
   def mangle_yt_url(self, url):
      """This function will be called to preprocess any and all YT urls.
      
      This default implementation simply returns its first argument. If you
      want to perform URL mangling, override or overwrite this method for the
      relevant YTVideoRef instance(s) with a callable that implements the
      mapping you desire."""
      return url
   
   def _got_video_urls(self):
      return bool(self.fpl)
   
   def _get_stream_urls(self):
      return self.fmt_stream_map
   
   def url_get_annots(self):
      # Stored in 'iv_storage_server' PLAYER_CONFIG variable on watch page.
      return 'http://www.youtube.com/annotations_invideo?legacy=1&video_id={}'.format(self.vid)
   
   def get_metadata_blocking(self):
      self.log(20, 'Acquiring YT metadata.')
      self._tried_md_fetch = True
      need_watchpage = self._try_html5
      
      try:
         self._get_metadata_getvideoinfo()
      except YTError:
         self.log(20, 'Video info retrieval failed; falling back to retrieval of metadata from watch page.')
         need_watchpage = True
      
      if (need_watchpage):
         self._get_metadata_watch(html5=self._try_html5)
   
   def _get_metadata_getvideoinfo(self):
      url = self.URL_FMT_GETVIDEOINFO.format(self.vid)
      content = self.urlopen(url).read().decode('ascii')
      
      vi = _split_yt_dictstring(content)
      #import pprint; pprint.pprint(vi)
      
      if (vi['status'] != 'ok'):
         self.log(20, 'YT Refuses to deliver video info: {0!a}'.format(vi))
         raise YTError('YT Refuses to deliver video info: {0!a}'.format(vi))
      
      self.title = vi['title']
      self._process_vi_dict(vi)
      
      self.got_video_info = True
   
   def _process_vi_dict(self, vi):
      ums = vi['url_encoded_fmt_stream_map']
      return self.fmt_map_update(ums)
   
   def _get_metadata_watch(self, html5):
      url = self.URL_FMT_WATCH.format(self.vid)
      
      try:
         uo = self.urlopen(url, html5=html5)
      except urllib.error.HTTPError as exc:
         self.log(30, 'HTTP request for {!a} returned {}.'.format(url, exc.code))
         # TODO: Attempt to parse error message from 404 body (exc.read())?
         raise YTError('YT refuses to deliver video urls (404).') from exc
      else:
         content = uo.read()
      
      fmt_url_count = self.fmt_maps_update_markup(content)
      if (fmt_url_count == 0):
         m_err = self.re_err.search(content)
         if (m_err is None):
            m_err = self.re_err_age.search(content)
            if (m_err is None):
               raise YTError("YT markup failed to match expectations; couldn't extract video urls.")
            error_cls = YTLoginRequired
         else:
            error_cls = YTError
         
         err_text = m_err.groupdict()['text'].strip().decode('utf-8')
         err_text = err_text.replace('<br/>', '')
         err_text = xml_unescape(err_text)
         raise error_cls('YT refuses to deliver video urls: {0!a}.'.format(err_text))
      
      m = self.re_title.search(content)
      if (m is None):
         self.log(30, 'Unable to extract video title; this probably indicates a yavdlt bug.')
         self.title = '--untitled--'
      else:
         self.title = xml_unescape(m.groupdict()['text'].decode('utf-8'))
         self.log(20, 'Acquired video title {0!a}.'.format(self.title))
   
   def _choose_tmp_fn(self, ext=None):
      return os.path.join(self.dlp_tmp, self._choose_fn(ext) + '.tmp')
   
   def _choose_final_fn(self, ext=None):
      if ((ext is None) and self.make_mkv):
         if (self.drop_tt):
            ext = '[-{}].mkv'.format(self.drop_tt)
         else:
            ext = 'mkv'
      
      return os.path.join(self.dlp_final, self._choose_fn(ext))
   
   def _move_video(self, fn_tmp):
      import shutil
      fn_final = self._choose_final_fn()
      self.log(20, 'Moving finished movie file to {0!a}.'.format(fn_final))
      shutil.move(fn_tmp, fn_final)
      return fn_final
   
   def _choose_fn(self, ext=None):
      title = self.title
      mtitle = ''
      if isinstance(title, bytes):
         title = title.decode('utf-8')
      
      for c in title:
         if (c.isalnum() or (c in '-') or ((ord(c) > 127) and c.isprintable())):
            mtitle += c
         elif (c in ' _'):
            mtitle += '_'
      
      if (ext is None):
         ext = self.MT_EXT_MAP.get(self._mime_type,'bin')
      
      return 'yt_{0}.[{1}][{2}].{3}'.format(mtitle, self.vid, self._fmt, ext)
   
   def fetch_data(self, dtm):
      # Need to determine preferred format first.
      if (not self._tried_md_fetch):
         self.get_metadata_blocking()
      
      if (self._pick_video() is None):
         # No working formats, forget all this then.
         raise YTError('Unable to pick video fmt; bailing out.')
      
      if (self.make_mkv and os.path.exists(self._choose_final_fn())):
         # MKV files are only written once we have retrieved all the data for this video; so if one for this video exists
         # already, we can safely skip it.
         # TODO: What about updated remote A/V/S data? Are changes to AV data even allowed by YT?
         self.log(20, 'Local final file {0!r} exists already; skipping this download.'.format(self._choose_final_fn()))
         return
      
      if (dtm & DATATYPE_VIDEO):
         if (os.path.exists(self._choose_final_fn())):
            # We might still need new subs, however, so only cancel AV data download here.
            self.log(20, 'Local final file {0!r} exists already; skipping this download.'.format(self._choose_final_fn()))
         else:
            vf = self.fetch_video()
            if (self.make_mkv):
               vf.seek(0)
               modname = self.MT_PARSERMODULE_MAP[self._mime_type]
               pmod = __import__(modname)
               mkvb = pmod.make_mkvb_from_file(vf)
               mkvb.sort_tracks()
            else:
               self._move_video(vf.name)
      
      elif (self.make_mkv):
         # Sub only MKV files; kinda a weird case, but let's support it anyway.
         from mcio_matroska import MatroskaBuilder
         mkvb = MatroskaBuilder(1000000, None)
      
      if (self.make_mkv):
         mkvb.set_writingapp('Yet Another Video DownLoad Tool (unversioned)')
         file_title = 'Youtube video {0!a}({1:d}): {2}'.format(self.vid, self._fmt, self.title)
         mkvb.set_segment_title(file_title)
         if (dtm & DATATYPE_VIDEO):
            mkvb.set_track_name(0, file_title)
      
      if (dtm & DATATYPE_ANNOTATIONS):
         (annotations, sts_raw, sts_nospam) = self.fetch_annotations()
         if (sts_raw is None):
            pass
         elif (len(sts_raw.subs) == 0):
            self.log(20, 'Received {0:d} annotations, but none appear sublike. :('.format(len(annotations)))
         else:
            if (self.make_mkv):
               self.log(20, 'Received {0:d}(/{1:d}) ({2:d} nospam) sublike annotations; muxing into MKV.'.format(len(sts_raw.subs), len(annotations), len(sts_nospam.subs)))
               sts_raw.mkv_add_track(mkvb)
               sts_nospam.mkv_add_track(mkvb)
            else:
               fn_out = self._choose_final_fn('ass')
               self.log(20, 'Received {0:d}(/{1:d}) sublike annotations; writing to {2!a}.'.format(len(sts_raw.subs), len(annotations), fn_out))
               f = open(fn_out, 'wb')
               sts_raw.write_to_file(f)
               f.close()
               if (sts_nospam.subs):
                  fn_out = self._choose_final_fn('nospam.ass')
                  self.log(20, 'Received {0:d}(/{1:d}) nospam sublike annotations; writing to {2!a}.'.format(len(sts_nospam.subs), len(annotations), fn_out))
                  f = open(fn_out, 'wb')
                  sts_nospam.write_to_file(f)
                  f.close()


      if (dtm & DATATYPE_TIMEDTEXT):
         ttd = self.fetch_tt()
         if (ttd):
            if (self.make_mkv):
               self.log(20, 'Muxing TimedText data into MKV.')
               for sts in ttd:
                  sts.mkv_add_track(mkvb)
               
            else:
               self._dump_ttd(ttd)
      
      if (self.make_mkv):
         if (self.drop_tt):
            import mcio_matroska
            for tt in self.drop_tt:
               (tt_attr, tt_name_hr) = self._track_type_map[tt]
               tt_mkv = getattr(mcio_matroska, tt_attr)
               tracks = mkvb.get_tracks_by_type(tt_mkv)
               self.log(20, 'Dropping {:d} {} track(s) from file as requested.'.format(len(tracks), tt_name_hr))
               for track in tracks:
                  mkvb.drop_track(track.get_track_id())
            mkvb.sort_tracks()
         
         fn_out = self._choose_tmp_fn('mkv')
         self.log(20, 'Writing MKV data to file.')
         f_out = open(fn_out, 'w+b')
         mkvb.write_to_file(f_out)
         f_out.close()
         self._move_video(fn_out)
         # MKV write cycle is finished; remove the raw video file.
         os.unlink(vf.name)
   
   def fetch_video(self):
      from fcntl import fcntl, F_SETFL
      from select import select
      from os import O_NONBLOCK
      
      if (not self._tried_md_fetch):
         self.get_metadata_blocking()
      
      url = self._pick_video()
      if (url is None):
         raise YTError('Unable to pick video fmt; bailing out.')
      
      fn_out = self._choose_tmp_fn()
      try:
         f = open(fn_out, 'r+b')
      except IOError:
         f = open(fn_out, 'w+b')
      
      f.seek(0,2)
      flen = f.tell()
      if (flen > self._content_length):
         raise YTError('Existing local file longer than remote version; not attempting to retrieve.')
      elif (flen == self._content_length):
         self.log(20, 'Local temporary file {0!r} appears to be complete already.'.format(fn_out))
         return f
         
      self.log(20, 'Fetching data from {0!r}.'.format(url))
      
      plen = 128
      off_start = max(flen - plen, 0)
      req_headers = {}
      if (off_start > 0):
         f.seek(off_start)
         prefix_data = f.read()
         if (len(prefix_data) != plen):
            raise YTError('Target file appears to have changed size from under us; bailing out.')
         req_headers['Range'] = 'bytes={0}-'.format(off_start)
      else:
         prefix_data = None
      
      res = self.urlopen(url, headers=req_headers, mangle=False)
      
      cl = self._content_length
      
      try:
         cl_r = int(res.headers.get('content-length'))
      except (KeyError, ValueError, TypeError):
         cl_r = None
      
      if (off_start):
         if (res.code == 200):
            self.log(20, 'Resume failed due to lack of server-side support; will have to redownload the entire file. :(')
            off_start = 0
            prefix_data = None
         elif (res.code != 206):
            raise YTError('Download resume failed; got unexpected HTTP response code {0}.'.format(res.code))
      
      if (cl_r):
         if (cl_r != cl-off_start):
            raise YTError('Content length mismatch.')
      
      if (off_start == 0):
         f.seek(0)
         f.truncate()
      
      self.log(20, 'Total length is {0} bytes.'.format(cl))
      
      cl_g = off_start
      if (prefix_data):
         data = res.read(len(prefix_data))
         if (len(data) != len(prefix_data)):
            raise YTError("Download resume failed; premature content body cutoff.")
         if (data != prefix_data):
            raise YTError("Download resume failed; mismatch with existing tail data.")
      
         cl_g += len(prefix_data)
         self.log(15, 'End of local file matches remote data; resuming download.')
      
      while (True):
         data_read = res.read(1024*1024)
         if (len(data_read) == 0):
            break
         cl_g += len(data_read)
         self.log(15, 'Progress: {0} ({1:.2%})'.format(cl_g, float(cl_g)/cl))
         f.write(data_read)
      
      if (cl_g != self._content_length):
         raise YTError("Prematurely lost DL connection; expected {0} bytes, got {1}.".format(self._content_length, cl_g))
      
      f.truncate()
      return f
   
   def fetch_annotations(self):
      from io import StringIO
      
      url = self.url_get_annots()
      if (url is None):
         self.log(10, 'Skipping annotation retrieval (no URL).')
         return (None, None, None)
      self.log(20, 'Fetching annotations from {0!a}.'.format(url))
      req = self.urlopen(url)
      
      content = req2str(req)
      self.log(20, 'Parsing annotation data.')
      
      annotations = parse_ytanno(StringIO(content))
      if (len(annotations) < 1):
         self.log(20, 'There are no annotations for this video.')
         return (None, None, None)
      
      annotations.sort()
      sts_raw = ASSSubSet('annotations (unfiltered)')
      sts_raw.add_subs_from_yt_annotations(annotations)
      sts_nospam = ASSSubSet('annotations (spam filtered)')
      sts_nospam.add_subs_from_yt_annotations(annotations, filter_spam=True)
      return (annotations, sts_raw, sts_nospam)
   
   def fetch_tt(self):
      url = 'http://video.google.com/timedtext?v={0}&type=list'.format(self.vid)
      self.log(20, 'Checking for timedtext data.')
      req = self.urlopen(url)
      content = req.read()
      if (content == b''):
         self.log(20, 'No timedtext data found.')
         return
      
      ttl = YTimedTextList.build_from_markup(self.vid, content)
      tdata = ttl.fetch_all_blocking(self.mangle_yt_url)
      
      if (len(tdata) < 1):
         self.log(20, 'No timedtext streams found.')
      
      return(tdata)
   
   def _dump_ttd(self, ttdata):
      for subset in ttdata:
         lc = subset.lc
         if (lc is None):
            lc = ''
         lc = lc.replace('/', '').replace('\x00','')
         name = subset.name.replace('/', '').replace('\x00','')
         fn = self._choose_final_fn('{0}_{1}.ass'.format(lc, name))
         self.log(20, 'Writing timedtext data for name {0!a}, lc {1} to {2!a}.'.format(subset.name, subset.lc, fn))
         if (isinstance(fn, str)):
            fn = fn.encode('utf-8')
         f = open(fn, 'wb')
         subset.write_to_file(f)
         f.close()
   
   def fmt_url_map_fetch_update(self, fmt):
      url = self.URL_FMT_WATCH.format(self.vid, fmt)
      content = self.urlopen(url).read()
      self.fmt_maps_update_markup(content)
   
   def fmt_map_update(self, ums, log=True):
      from urllib.parse import unquote_plus
      _map = self.fmt_stream_map
      ums_split = ums.split(',')
      
      rv = 0
      
      for umsf in ums_split:
         umsf_data = _split_yt_dictstring(umsf)
         
         try:
            url = umsf_data['url']
            fmt = int(umsf_data['itag'], 10)
         except (KeyError, ValueError):
            self.log(30, 'Stream URL spec {!r} has unknown format, ignoring.'.format(umsf_data))
            continue
                  
         if (not (fmt in _map)):
            if (log):
               self.log(20, 'Caching direct url for new format {0:d}.'.format(fmt))
            _map[fmt] = url
         rv += 1
      return rv
   
   def fmt_maps_update_markup(self, markup):
      from urllib.parse import unquote
      import json
      
      if (isinstance(markup, bytes)):
         markup = markup.decode('utf-8','surrogateescape')
      
      rv = 0
      for line in markup.split('\n'):
         # Flash content parsing.
         m = self.re_fmt_playerconfig.search(line)
         if (m is None):
            continue
         
         player_config_text = m.groupdict()['text']
         try:
            pca = json.loads(player_config_text)['args']
            rv += self._process_vi_dict(pca)
         except (ValueError, KeyError) as exc:
            self.log(30, 'Unable to decode PLAYER_CONFIG dict {!r}:'.format(player_config_text), exc_info=True)
            continue
         
         # HTML5 content extraction.
         try:
            html5_fmt_data = pca['html5_fmt_map']
         except KeyError:
            if (self._try_html5):
               loglevel = 30
            else:
               loglevel = 20
            self.log(loglevel, 'No html5 data present.'.format(line))
            continue
         
         for fmt_dict in html5_fmt_data:
            try:
               fmt = int(fmt_dict['itag'])
               url = fmt_dict['url']
            except (KeyError, ValueError):
               self.log(30, 'Failed to process html5 fmt dict: {!r}.'.format(fmt_dict))
               continue
            
            try:
               quality = fmt_dict['quality']
            except KeyError:
               quality = None
            
            try:
               type_ = fmt_dict['type']
            except KeyError:
               type_ = None
            
            if not (fmt in self.fmt_url_map):
               self.log(20, 'Caching direct url for new format {0:d} ({1}) parsed from html5-type markup.'.format(fmt, type_))
               self.fmt_url_map[fmt] = url
               rv += 1
      return rv
   
   def _pick_video(self, cache_ok=True):
      from urllib.parse import splittype, splithost
      from urllib.error import HTTPError
      
      if (cache_ok and self._content_direct_url):
         return self._content_direct_url
      
      if (not self._tried_md_fetch):
         self.get_metadata_blocking()
      
      for fmt in self.fpl:
         if (fmt == FMT_DEFAULT):
            continue
         try:
            url_r = self.fmt_stream_map[fmt]
         except KeyError:
            self.log(20, 'No url for fmt {0} available.'.format(fmt))
            continue
         
         url = self.mangle_yt_url(url_r)
         
         try:
            response = self.urlopen(url)
         except URLError as exc:
            self.log(20, 'Tried to get video in fmt {0} and failed (urlopen exc {1!a}.)'.format(fmt, exc))
            continue
         
         rc = response.getcode()
         url = response.geturl()
         
         if (rc == 200):
            mime_type = response.getheader('content-type', None)
            content_length = response.getheader('content-length', None)
            if not (content_length is None):
               content_length = int(content_length)
               self.log(20, 'Fmt {0} is good ... using that.'.format(fmt))
               self._mime_type = mime_type
               self._fmt = fmt
               self._content_length = content_length
               self._content_direct_url = url
               return url
         
         self.log(20, 'Tried to get video in fmt {0} and failed (http response {1!a}).'.format(fmt, rc))
         
      else:
         self.log(38, 'None of the attempted formats worked out.')
         return None


class YTPlayListRef:
   logger = logging.getLogger('YTPlaylistRef')
   log = logger.log
   
   # TODO: Use start-index=[0-9]+ parameter to retrieve arbitrary long lists in full.
   pl_base_url = 'http://gdata.youtube.com/feeds/api/playlists/{0}?v=2&max-results=50'
   def __init__(self, plid):
      self.plid = plid
      self.vids = []
   
   def fetch_pl(self):
      """Fetch playlist and parse out vids."""
      pl_url = self.pl_base_url.format(self.plid)
      self.log(20, 'Retrieving playlist from {0!a}.'.format(pl_url))
      req = urllib.request.urlopen(pl_url)
      pl_markup = req.read()
      self.log(20, 'Parsing playlist data.')
      pl_dom = xml.dom.minidom.parseString(pl_markup)
      link_nodes = pl_dom.getElementsByTagName('link')
      
      vids_set = set()
      vids_l = []
      
      for node in link_nodes:
         try:
            tt = node.attributes['type'].value
         except KeyError:
            continue
         
         if (tt != 'text/html'):
            continue
         
         try:
            node_url = node.attributes['href'].value
         except KeyError:
            continue
         
         try:
            node_vids = spec2vidset(node_url, fallback=False)
         except ValueError:
            continue
         
         for vid in node_vids:
            if (vid in vids_set):
               continue
            vids_set.add(vid)
            vids_l.append(vid)
      
      self.log(20, 'Got {0:d} playlist entries: {1!a}'.format(len(vids_l), vids_l))
      self.vids = vids_l
   

class YTVideoInfo:
  def __init__(self, vid, upload_ts):
    self.vid = vid
    self.upload_ts = upload_ts

  def __eq__(self, other):
    return (self.vid == other.vid)

  def __ne__(self, other):
    return not (self == other)

  def __hash__(self):
    return hash(self.vid)

  def __repr__(self):
    return '{}{}'.format(type(self).__name__, (self.vid, self.upload_ts))


class YTUserRef:
   logger = logging.getLogger('YTUserRef')
   log = logger.log
   
   user_base_url = 'http://gdata.youtube.com/feeds/base/users/{}/uploads?alt=rss&v=2&max-results=50&start-index={}'
   results_per_page = 50
   def __init__(self, user_id):
      self.user_id = user_id
      self.vis = []

   @staticmethod
   def _pd2ts(pd):
     import datetime, time
     dt = datetime.datetime.strptime(pd, '%a, %d %b %Y %H:%M:%S %z')
     return int(time.mktime(dt.utctimetuple()))

   def get_vids(self):
     rv = []
     for vi in self.vis:
        rv.append(vi.vid)
     return rv

   def __fetch_page(self, idx):
      # Entry indexing is 1-based, for some reason.
      u_url = self.user_base_url.format(self.user_id, 1+idx*self.results_per_page)
      self.log(20, 'Retrieving user video list from {!a}.'.format(u_url))
      req = urllib.request.urlopen(u_url)
      u_markup = req.read()
      self.log(20, 'Parsing video feed data.')
      u_dom = xml.dom.minidom.parseString(u_markup)
      item_nodes = u_dom.getElementsByTagName('item')

      vis_set = set()
      vis_l = []

      for n_i in item_nodes:
        (n_pd,) = n_i.getElementsByTagName('pubDate')
        pts_text = n_pd.childNodes[0].nodeValue
        pts = self._pd2ts(pts_text)

        n_ls = n_i.getElementsByTagName('link')

        if (len(n_ls) > 1):
          self.log(30, 'Found more than one link element for item {!a}; ignoring all but the first.'.format(n_i))
        elif (len(n_ls) < 1):
          self.log(30, 'Found no link elements for item {!a}; ignoring.'.format(n_i))
          continue

        n_l = n_ls[0]
        watch_url = n_l.childNodes[0].nodeValue
        node_vids = spec2vidset(watch_url, fallback=False)

        for vid in node_vids:
           vi = YTVideoInfo(vid, pts)
           if (vi in vis_set):
              continue
           vis_set.add(vi)
           self.vis.append(vi)

      return len(item_nodes)

   def fetch_vids(self):
      """Fetch video list and parse out vids."""
      idx = 0
      while (True):
         c = self.__fetch_page(idx)
         if (c != self.results_per_page):
            break
         idx += 1

      self.log(20, 'Got {:d} user video list entries: {!a}'.format(len(self.vis), self.vis))
      #raise


# ---------------------------------------------------------------- Cmdline / config interpretation code
def spec2vidset(s, fallback=True):
   import logging
   log = logging.getLogger('spec2vidset').log
   res = (
      re.compile('^(?P<vid>[A-Za-z0-9_-]{11})$'),
      re.compile('^https?://(?:www.)?youtube(?:-nocookie)?\.[^\./]+\.?/+watch?.*v=(?P<vid>[A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])'),
      re.compile('^https?://(?:www.)?youtube(?:-nocookie)?\.[^\./]+\.?/+v/+(?P<vid>[A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])'),
      re.compile('^https?://youtu.be/(?P<vid>[A-Za-z0-9_-]{11})($|[^A-Za-z0-9_-])'),
      re.compile('^https?://(?:www.)?youtube.[^\./]+\.?/embed/(?P<vid>[A-Za-z0-9_-]{11})')
   )
   
   for rx in res:
      m = rx.search(s)
      if (m is None):
         continue
      return set((m.groupdict()['vid'],))
   else:
      if (fallback):
         log(20, "{0!a} doesn't look like a direct video spec ... treating as url to embedding document." .format(s))
         rv = set()
         for url in get_embedded_yturls(s):
            rv.update(spec2vidset(url, fallback=False))
         return rv
   
   raise ValueError('Unable to get video id from string {0!a}.'.format(s))


_re_embedded_split = re.compile(b'<object|<iframe')
_re_embedded_urls = [
  re.compile(b'<param name="movie" value="(?P<yt_url>https?://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"'),
  re.compile(b'<embed src="(?P<yt_url>https?://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"'),
  re.compile(b'src="(?P<yt_url>https?://[^"/]*youtube(?:-nocookie)?\.[^"/]+/embed/[^"]+)"[^<>]*>')
]


def get_embedded_yturls(url):
   import logging
   log = logging.getLogger('embed_fetch').log
   
   log(20, 'Fetching embedding document {0!a}'.format(url))
   req = urllib.request.urlopen(url)
   log(20, 'Extracting urls for embedded yt videos.')
   
   html = req.read()
   html_fragments = _re_embedded_split.split(html)
   urls = []
   for fragment in html_fragments:
      for _re in _re_embedded_urls:
         m = _re.search(fragment)
         if not (m is None):
            urls.append(m.groupdict()['yt_url'].decode('ascii'))
   
   #urls = [xml_unescape(u) for u in urls]
   return set(urls)


class Config:
   # internal stuff
   logger = logging.getLogger('config')
   log = logger.log
   
   _dt_map = dict(
      v=DATATYPE_VIDEO,
      a=DATATYPE_ANNOTATIONS,
      t=DATATYPE_TIMEDTEXT
   )
   config_fn_default = '~/.yavdlt/config'
   
   # config scope
   FMT_DEFAULT = FMT_DEFAULT
   
   # config / cmdline var defaults
   loglevel = 15
   dtype = ''.join(_dt_map.keys())
   config_fn = None
   list_url_manglers = False
   dl_path_temp = '.'
   dl_path_final = '.'
   try_html5 = False
   drop_tt = ''
   
   def __init__(self):
      self._urllib_handler_lists = {}
      self._url_manglers = {}
      self._fmt_preflists = {}
      self._default_fpl = (22, 35, 34, 18, 5, FMT_DEFAULT)
      self._args = None
      
      self.make_mkv = False
      self.fpl = None
      self.fmt = None
      self.url_mangler = None
      self.urllib_handler_list = None
      self.playlists = []
      self.users = []
      self.playlist = None
      self.user = None

   def url_mapper_reg(self, name):
      def r(val):
         self._url_manglers[name] = val
         return val
      return r
   
   def urllib_handler_list_reg(self, name):
      def r(val):
         self._urllib_handler_lists[name] = val
         return val
      return r
   
   def add_format_preflist(self, name, pl, *, default=False):
      if (default):
         self._default_fpl = pl
      self._fmt_preflists[name] = pl
   
   def make_urlmangler_phpproxy_base64(self, name, baseurl):
      @self.url_mapper_reg(name)
      def url_mangle(url):
         from base64 import encodebytes
         return ''.join((baseurl, '/index.php?q=', encodebytes(url.encode('utf-8','surrogateescape')).replace(b'\n',b'').decode('ascii'), '&hl=e8'))
      return url_mangle

   def _read_config_file(self):
      from os.path import expanduser, expandvars
      if (self.config_fn is None):
         fn = expandvars(expanduser(self.config_fn_default))
         if not (os.path.exists(fn)):
            self.log(20, "Config file {0!a} doesn't exist; using builtin default values.".format(fn))
            return
      else:
         fn = expandvars(expanduser(self.config_fn))
      
      config_lns = self.__dict__
      config_gns = {}
      for name in dir(self):
         if name.startswith('_'):
            continue
         if (name in config_lns):
            continue
         config_gns[name] = getattr(self, name)
      
      f = open(fn, 'rb')
      code = compile(f.read(), fn, 'exec')
      exec(code, config_gns, config_lns)
   
   def _determine_settings(self):
      (opts, args) = self._read_opts()
      # update the config fn and loglevel first
      self._apply_optvals(opts)
      self._read_config_file()
      # override config file settings with info from cmdline.
      self._apply_optvals(opts)
      self._args = args
   
   def _apply_optvals(self, opts):
      for (name, val) in opts.__dict__.items():
         if not (val is None):
            setattr(self, name, val)
      logging.getLogger().setLevel(self.loglevel)
   
   def _read_opts(self):
      import optparse
      
      op = optparse.OptionParser(usage="%prog [options] <yt video id>*")
      oa = op.add_option
      oa('-c', '--config', dest='config_fn', help='Config file to use.', metavar='FILENAME')
      oa('-d', '--data-type', dest='dtype', help='Data types to download')
      oa('--fmt', type=int, dest='fmt', help="YT format number to use.")
      oa('--fpl', help='Pick format preference list.')
      oa('--playlist', help='DEPRECATED: Parse (additional) video ids from specified playlist', metavar='PLAYLIST_ID')
      oa('--user', help='DEPRECATED: Parse (additional) video ids from specified (space-separated) user video lists')
      oa('--list-url-manglers', dest='list_url_manglers', action='store_true', help='Print lists of known URL manglers and exit')
      oa('--url-mangler', '-u', dest='url_mangler', metavar='UMNAME', help='Fetch metadata pages through specified HTTP gateway')
      oa('--urllib-handler-list', '-H', dest='urllib_handler_list', metavar='UHLNAME', help='Use specified urllib handler list for HTTP fetches.')
      oa('--mkv', '-m', dest='make_mkv', action='store_true', help='Mux downloaded data (AV+Subs) into MKV file.')
      oa('--nomkv', dest='make_mkv', action='store_false', help="Don't mux downloaded data (AV+Subs) into MKV file.")
      oa('--html5', dest='try_html5', action='store_true', help="Opt into html5 experiment for watch page retrieval (required for webm downloads)")
      oa('--nohtml5', dest='try_html5', action='store_false', help="Opt into html5 experiment for watch page retrieval (this is required for webm downloads, but will disable parsing of flv urls from watch pages.)")
      oa('-k', '--drop-track-types', dest='drop_tt', action='store', metavar='TTSPEC', help="Track types to drop ('v': video; 'a': audio).")
      oa('-q', '--quiet', dest='loglevel', action='store_const', const=30, help='Limit output to errors.')
      
      rv = op.parse_args()
      op.destroy()
      return rv
   
   def _get_um(self):
      if (self.url_mangler is None):
         return None
         
      if (hasattr(self.url_mangler, '__call__')):
         return self.url_mangler
      
      try:
         rv = self._url_manglers[self.url_mangler]
      except KeyError as exc:
         raise Exception('Unknown url mangler {0!a}; available ums are {1}.'.format(self.url_mangler,
            list(self._url_manglers.keys()))) from exc
      return rv
   
   def _get_uhl(self):
      if (self.urllib_handler_list is None):
         return ()
      
      try:
         rv = self._urllib_handler_lists[self.urllib_handler_list]
      except KeyError as exc:
         raise Exception('Unknown UHL {0!a}; available UHLs are {1}.'.format(self.urllib_handler_list,
            list(self._urllib_handler_lists.keys()))) from exc
      return rv
   
   def _get_vids(self):
      vids_set = set()
      rv = []
   
      def update_vids(s):
         for vid in s:
            if (vid in vids_set):
               continue
            vids_set.add(vid)
            rv.append(vid)

      arg_handlers = {}
      def reg_ah(type_str):
        def reg_ah2(f):
          arg_handlers[type_str] = f
          return f
        return reg_ah2

      @reg_ah('pl')
      def handle_pl(pl):
        self.playlists.append(pl)
      @reg_ah('user')
      def handle_u(u):
        self.users.append(u)
      @reg_ah('v')
      def handle_vid(vid):
        update_vids((vid,))

      for arg in self._args:
         if not (':' in arg):
           update_vids(spec2vidset(arg))
           continue
         (t, spec) = arg.split(':', 1)
         try:
           h = arg_handlers[t]
         except KeyError:
           raise ValueError('Unknown spec type {!a} in arg {!a}.'.format(t, arg))
         h(spec)
   
      if (self.playlist):
         self.playlists.append(self.playlists)
         self.log(30, '--playlist is deprecated; use pl:<id> args instead.')

      for playlist in self.playlists:
         plr = YTPlayListRef(playlist)
         plr.fetch_pl()
         update_vids(plr.vids)

      if not (self.user is None):
         self.log(30, '--user is deprecated; use user:<id> args instead.')
         self.users.extend(self.user.split())

      for user in self.users:
         ur = YTUserRef(user)
         ur.fetch_vids()
         update_vids(ur.get_vids())

      return rv
   
   def _get_fpl(self):
      if (not self.fmt is None):
         return (self.fmt,)
      
      if not (self.fpl is None):
         try:
            rv = self._fmt_preflists[self.fpl]
         except KeyError as exc:
            raise Exception('Unknown preflist {0}; available preflists are {1}.'.format(self.fpl, list(self._fmt_preflists.keys())))
         return rv
      
      return self._default_fpl
   
   def _get_dtypemask(self):
      rv = 0
      for c in self.dtype:
         rv |= self._dt_map[c]
      return rv


def main():
   import optparse
   import os.path
   import sys
   
   logger = logging.getLogger()
   log = logger.log
   
   logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
      stream=sys.stderr, level=logging.DEBUG)
   
   conf = Config()
   conf._determine_settings()
   
   if (conf.list_url_manglers):
      print(list(conf._url_manglers.keys()))
      return
   
   log(20, 'Settings determined.')
   
   for c in conf.dtype:
      if not (c in conf._dt_map):
         raise ValueError('Unknown data type {0!a}.'.format(c))
   
   um = conf._get_um()
   uhl = conf._get_uhl()
   vids = conf._get_vids()
   
   log(20, 'Final vid set: {0}'.format(vids))
   vids_failed = []
   
   fpl = conf._get_fpl()

   dtypemask = conf._get_dtypemask()

   for vid in vids:
      log(20, 'Fetching data for video with id {0!a}.'.format(vid))
      ref = YTVideoRef(vid, fpl, conf.dl_path_temp, conf.dl_path_final, conf.make_mkv, conf.try_html5, conf.drop_tt, uhl)
      
      if not (um is None):
         ref.mangle_yt_url = um
         ref.force_fmt_url_map_use = True
      
      try:
         ref.fetch_data(dtypemask)
      except YTError:
         log(30, 'Failed to retrieve video {0!a}:'.format(vid), exc_info=True)
         vids_failed.append(vid)
         continue
   
   if (vids_failed):
      log(30, 'Failed to retrieve videos: {0}.'.format(vids_failed))
   log(20, 'All done.')

if (__name__ == '__main__'):
   main()
