#!/usr/bin/env python3
# Yet Another Video Download Tool: Download information from youtube
# Copyright (C) 2009,2010  Sebastian Hagen
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
import http.client
import logging
import os
import os.path
import urllib.request
import re
import xml.dom.minidom

from io import BytesIO

# ---------------------------------------------------------------- General helper functions

xml_unescape = html.parser.HTMLParser().unescape

def escape_decode(s):
   from codecs import escape_decode
   return escape_decode(s.encode('utf-8'))[0].decode('utf-8')


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
   
   def mkv_add_track(self, mkvb):
      from mcio_codecs import CODEC_ID_ASS
      cpd = self._get_header() + self._get_header2(ASSSubtitle.ASS_FIELD_NAMES)
      mkvb.add_track(self._iter_subs_mkv(mkvb.tcs), mkvb.TRACKTYPE_SUB, CODEC_ID_ASS, cpd, False, track_name=self.name, track_lang=self.lc)


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
               h,m,s = strval.split(':')
               nval = int(h)*3600+int(m)*60+float(s)
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
      color1 = int(self.fgColor, 16)
      
      #color3 = int(self.borderColor, 16)
      #if not (color3 is None):
         #color3 |= round(float(1-self._get_num('borderAlpha'))*255) << 24
      
      #color_bg = int(self.bgColor, 16)
      #if not (color_bg is None):
         #color_bg |= round(float(1-self._get_num('bgAlpha'))*255) << 24
      
      return dict(color1=color1)


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


class YTVideoRef:
   re_tok = re.compile(b'&t=(?P<field_t>[^"&]+)&')
   re_title = re.compile(b'<link rel="alternate" +type="application/json\+oembed" +href="[^"]*" +title="(?P<text>.*?)" */>')
   re_err = re.compile(b'<div[^>]* id="error-box"[^>]*>.*?<div[^>]* class="yt-alert-content"[^>]*>(?P<text>.*?)</div>', re.DOTALL)
   re_err_age = re.compile(b'<div id="verify-age-details">(?P<text>.*?)</div>', re.DOTALL)
   re_fmt_url_map_markup = re.compile(r'\? "(?P<umm>.*?fmt_url_map=.*?>)"')
   re_fmt_url_map = re.compile('fmt_url_map=(?P<ms>[^"&]+)&')
   re_fmt_stream_map = re.compile('fmt_stream_map=(?P<ms>[^"&]+)&')
   
   URL_FMT_WATCH = 'http://www.youtube.com/watch?v={0}&fmt={1}&has_verified=1'
   URL_FMT_GETVIDEO = 'http://www.youtube.com/get_video?video_id={0}&t={1}{2}'
   URL_FMT_GETVIDEOINFO = 'http://youtube.com/get_video_info?video_id={0}'
   
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

   def __init__(self, vid, format_pref_list, dl_path_tmp, dl_path_final, make_mkv):
      self.vid = vid
      self.tok = None
      self._mime_type = None
      self.fmt_url_map = {}
      self._fmt_stream_map = {}
      self.force_fmt_url_map_use = False
      self.got_video_info = False
      self.title = None
      self.fpl = format_pref_list
      self.dlp_tmp = dl_path_tmp
      self.dlp_final = dl_path_final
      self._content_direct_url = None
      self._fmt = None
      self.make_mkv = make_mkv
   
   def mangle_yt_url(self, url):
      """This function will be called to preprocess any and all YT urls.
      
      This default implementation simply returns its first argument. If you
      want to perform URL mangling, override or overwrite this method for the
      relevant YTVideoRef instance(s) with a callable that implements the
      mapping you desire."""
      return url
   
   def _get_stream_urls(self):
      rv = {}
      for (key, val) in self._fmt_stream_map.items():
         val_s = val.split('|')
         if ((len(val_s) == 3) and (val_s[0].startswith('http://'))):
            rv[key] = val_s[0]
         elif ((len(val_s) == 2) and (val_s[1].startswith('rtmpe://'))):
            rv[key] = val_s[1]
         else:
            raise ValueError('Unknown stream url format {0!a}.'.format(val))
      return rv
   
   def url_get_annots(self):
      return self.mangle_yt_url('http://www.google.com/reviews/y/read2?video_id={0}'.format(self.vid))
   
   def get_token_blocking(self):
      self.log(20, 'Acquiring YT metadata.')
      try:
         self.get_token_getvideoinfo()
      except YTError:
         self.log(20, 'Video info retrieval failed; falling back to retrieval of metadata from watch page.')
         self.get_token_watch()
   
   def get_token_getvideoinfo(self):
      from urllib.parse import splitvalue, unquote, unquote_plus
      
      url = self.URL_FMT_GETVIDEOINFO.format(self.vid)
      url = self.mangle_yt_url(url)
      content = urllib.request.urlopen(url).read().decode('ascii')
      def uqv(d):
         (key, val) = d
         return (key, unquote_plus(val))
      
      vi = dict(uqv(splitvalue(cfrag)) for cfrag in content.split('&'))
      
      if (vi['status'] != 'ok'):
         self.log(20, 'YT Refuses to deliver video info: {0!a}'.format(vi))
         raise YTError('YT Refuses to deliver video info: {0!a}'.format(vi))
      
      self.tok = vi['token']
      self.title = vi['title']
      
      ums = vi['fmt_url_map']
      self.fmt_map_update(ums, self.fmt_url_map)
      sms = vi['fmt_stream_map']
      self.fmt_map_update(sms, self._fmt_stream_map, False)
      
      self.got_video_info = True
   
   def get_token_watch(self):
      fmt = self.fpl[0]
      
      if (fmt is FMT_DEFAULT):
         fmt = ''
      
      url = self.URL_FMT_WATCH.format(self.vid, fmt)
      url = self.mangle_yt_url(url)
      
      content = urllib.request.urlopen(url).read()
      
      m = self.re_tok.search(content)
      if (m is None):
         m_err = self.re_err.search(content)
         if (m_err is None):
            m_err = self.re_err_age.search(content)
            if (m_err is None):
               raise YTError("YT markup failed to match expectations; can't extract video token.")
            error_cls = YTLoginRequired
         else:
            error_cls = YTError
         
         err_text = m_err.groupdict()['text'].strip().decode('utf-8')
         err_text = err_text.replace('<br/>', '')
         err_text = xml_unescape(err_text)
         raise error_cls('YT refuses to deliver token: {0!a}.'.format(err_text))
        
      tok = m.groupdict()['field_t']
      
      m = self.re_title.search(content)
      if (m is None):
         self.log(30, 'Unable to extract video title; this probably indicates a yavdlt bug.')
         self.title = '--untitled--'
      else:
         self.title = m.groupdict()['text'].decode('utf-8')

      self.log(20, 'Acquired token {0}.'.format(tok))
      self.tok = tok
      
      self.fmt_maps_update_markup(content)
   
   def _choose_tmp_fn(self, ext=None):
      return os.path.join(self.dlp_tmp, self._choose_fn(ext) + '.tmp')
   
   def _choose_final_fn(self, ext=None):
      if ((ext is None) and self.make_mkv):
         ext = 'mkv'
      
      return os.path.join(self.dlp_final, self._choose_fn(ext))
   
   def _move_video(self, fn_tmp):
      fn_final = self._choose_final_fn()
      self.log(20, 'Moving finished movie file to {0!a}.'.format(fn_final))
      os.rename(fn_tmp, fn_final)
      return fn_final
   
   def _choose_fn(self, ext=None):
      title = self.title
      mtitle = ''
      if isinstance(title, bytes):
         title = title.decode('utf-8')
      
      for c in title:
         if (c.isalnum() or (c in '-')):
            mtitle += c
         elif (c in ' _'):
            mtitle += '_'
      
      if (ext is None):
         ext = self.MT_EXT_MAP.get(self._mime_type,'bin')
      
      return 'yt_{0}.[{1}][{2}].{3}'.format(mtitle, self.vid, self._fmt, ext)
   
   def fetch_data(self, dtm):
      # Need to determine preferred format first.
      if (self.pick_video() is None):
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
         if not (ttd is None):
            if (self.make_mkv):
               self.log(20, 'Muxing TimedText data into MKV.')
               for sts in ttd:
                  sts.mkv_add_track(mkvb)
               
            else:
               self._dump_ttd(ttd)
         
      if (self.make_mkv):
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
      
      if (self.tok is None):
         self.get_token_blocking()
      
      url = self.pick_video()
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
      
      req = urllib.request.Request(url, headers=req_headers)
      res = urllib.request.urlopen(req)
      
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
         self.log(15, 'End local file matches remote data; resuming download.')
      
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
      url = self.url_get_annots()
      self.log(20, 'Fetching annotations from {0!a}.'.format(url))
      req = urllib.request.urlopen(url)
      content = req.read()
      self.log(20, 'Parsing annotation data.')
      annotations = parse_ytanno(BytesIO(content))
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
      url = self.mangle_yt_url('http://video.google.com/timedtext?v={0}&type=list'.format(self.vid))
      self.log(20, 'Checking for timedtext data.')
      req = urllib.request.urlopen(url)
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
         fn = self._choose_fn('{0}_{1}.ass'.format(lc, name))
         self.log(20, 'Writing timedtext data for name {0!a}, lc {1} to {2!a}.'.format(subset.name, subset.lc, fn))
         if (isinstance(fn, str)):
            fn = fn.encode('utf-8')
         f = open(fn, 'wb')
         subset.write_to_file(f)
         f.close()
   
   def fmt_url_map_fetch_update(self, fmt):
      url = self.URL_FMT_WATCH.format(self.vid, fmt)
      url = self.mangle_yt_url(url)
      content = urllib.request.urlopen(url).read()
      self.fmt_maps_update_markup(content)
   
   def fmt_map_update(self, ums, _map, log=True):
      ums_split = ums.split(',')
      
      for umsf in ums_split:
         (fmt_str, url) = umsf.split('|',1)
         fmt = int(fmt_str)
         
         if (log and (not (fmt in self.fmt_url_map))):
            self.log(20, 'Caching direct url for new format {0:d}.'.format(fmt))
         _map[fmt] = url
   
   def fmt_maps_update_markup(self, markup):
      from urllib.parse import unquote
      if (isinstance(markup, bytes)):
         markup = markup.decode('utf-8','surrogateescape')
      
      m = self.re_fmt_url_map_markup.search(markup)
      if (m is None):
        return
      umm = m.groupdict()['umm']
      
      umm_unescaped = escape_decode(umm)
      
      for (re, _map, log) in ((self.re_fmt_url_map, self.fmt_url_map, True), (self.re_fmt_stream_map, self._fmt_stream_map, False)):
         m2 = re.search(umm_unescaped)
         if (m2 is None):
            continue
         ms_raw = m2.groupdict()['ms']
         ms = unquote(ms_raw)
         self.fmt_map_update(ms, _map, log)
   
   def pick_video(self, cache_ok=True):
      from urllib.parse import splittype, splithost
      
      if (cache_ok and self._content_direct_url):
         return self._content_direct_url
      
      for fmt in self.fpl:
         url = self.get_video_url(fmt)
         rc = 301
         
         while (301 <= rc <= 303):
            (type_, dp) = splittype(url)
            (host, path) = splithost(dp)
            conn = http.client.HTTPConnection(host)
            conn.request('HEAD',path)
            try:
               response = conn.getresponse()
            except http.client.BadStatusLine:
               # Happens for some responses ... don't know why, don't really
               # care.
               rc = None
               break
            rc = response.status
            if (301 <= rc <= 303):
               url = response.getheader('location')
         
         if (rc == 200):
            mime_type = response.getheader('content-type', None)
            content_length = int(response.getheader('content-length', None))
            if not (content_length is None):
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
   
   def get_video_url(self, fmt):
      if (self.force_fmt_url_map_use and (not (fmt in self.fmt_url_map))):
         self.fmt_url_map_fetch_update(fmt)
      
      if (fmt in self.fmt_url_map):
         self.log(20, 'Using cached direct video url.')
         return self.mangle_yt_url(self.fmt_url_map[fmt])
      
      if (self.tok is None):
         raise ValueError('Need to get token first.')
      
      if (fmt is FMT_DEFAULT):
         fmtstr = ''
      else:
         fmtstr = '&fmt={0:d}'.format(fmt)
      
      rv = self.mangle_yt_url(self.URL_FMT_GETVIDEO.format(self.vid, self.tok, fmtstr))
      return rv


class YTPlayListRef:
   logger = logging.getLogger('YTPlaylistRef')
   log = logger.log
   
   pl_base_url = 'http://gdata.youtube.com/feeds/api/playlists/{0}?v=2'
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
            node_vids = arg2vidset(node_url, fallback=False)
         except ValueError:
            continue
         
         for vid in node_vids:
            if (vid in vids_set):
               continue
            vids_set.add(vid)
            vids_l.append(vid)
      
      self.log(20, 'Got {0:d} playlist entries: {1!a}'.format(len(vids_l), vids_l))
      self.vids = vids_l
   

# ---------------------------------------------------------------- Cmdline / config interpretation code
def arg2vidset(s, fallback=True):
   import logging
   log = logging.getLogger('arg2vidset').log
   res = (
      re.compile('^(?P<vid>[A-Za-z0-9_-]{11})$'),
      re.compile('^http://(?:www.)?youtube(?:-nocookie)?\.[^\./]+\.?/+watch?.*v=(?P<vid>[A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])'),
      re.compile('^http://(?:www.)?youtube(?:-nocookie)?\.[^\./]+\.?/+v/+(?P<vid>[A-Za-z0-9_-]{11})(?:$|[^A-Za-z0-9_-])'),
      re.compile('^http://youtu.be/(?P<vid>[A-Za-z0-9_-]{11})($|[^A-Za-z0-9_-])')
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
            rv.update(arg2vidset(url, fallback=False))
         return rv
   
   raise ValueError('Unable to get video id from string {0!a}.'.format(s))


_re_embedded_split = re.compile(b'<object')
_re_embedded_url1 = re.compile(b'<param name="movie" value="(?P<yt_url>http://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"')
_re_embedded_url2 = re.compile(b'<embed src="(?P<yt_url>http://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"')

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
      m1 = _re_embedded_url1.search(fragment)
      m2 = _re_embedded_url2.search(fragment)
      if not (m1 is None):
         urls.append(m1.groupdict()['yt_url'].decode('ascii'))
      if not (m2 is None):
         urls.append(m2.groupdict()['yt_url'].decode('ascii'))
   
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
   
   def __init__(self):
      self._url_manglers = {}
      self._fmt_preflists = {}
      self._default_fpl = (22, 35, 34, 18, 5, FMT_DEFAULT)
      self._args = None
      
      self.make_mkv = False
      self.fpl = None
      self.fmt = None
      self.url_mangler = None
      self.playlist = None

   def url_mapper_reg(self, name):
      def r(val):
         self._url_manglers[name] = val
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
      oa('--playlist', help='Parse (additional) video ids from specified playlist', metavar='PLAYLIST_ID')
      oa('--list-url-manglers', dest='list_url_manglers', action='store_true', help='Print lists of known URL manglers and exit')
      oa('--url-mangler', '-u', dest='url_mangler', metavar='UMNAME', help='Fetch metadata pages through specified HTTP gateway')
      oa('--mkv', '-m', dest='make_mkv', action='store_true', help='Mux downloaded data (AV+Subs) into MKV file.')
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
   
   def _get_vids(self):
      vids_set = set()
      rv = []
   
      def update_vids(s):
         for vid in s:
            if (vid in vids_set):
               continue
            vids_set.add(vid)
            rv.append(vid)
   
      for vid_str in self._args:
         update_vids(arg2vidset(vid_str))
   
      if (self.playlist):
         plr = YTPlayListRef(self.playlist)
         plr.fetch_pl()
         update_vids(plr.vids)
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
   vids = conf._get_vids()
   
   log(20, 'Final vid set: {0}'.format(vids))
   vids_failed = []
   
   fpl = conf._get_fpl()

   dtypemask = conf._get_dtypemask()

   for vid in vids:
      log(20, 'Fetching data for video with id {0!a}.'.format(vid))
      ref = YTVideoRef(vid, fpl, conf.dl_path_temp, conf.dl_path_final, conf.make_mkv)
      
      if not (um is None):
         ref.mangle_yt_url = um
         ref.force_fmt_url_map_use = True
      
      try:
         ref.get_token_blocking()
         ref.fetch_data(dtypemask)
      except YTError:
         log(30, 'Failed to retrieve video {0!a}:'.format(vid), exc_info=True)
         stream_urls = list(ref._get_stream_urls().values())
         if ((not ref.fmt_url_map) and stream_urls):
            log(20, "We did manage to retrieve some stream urls we don't know how to use: {0!a}.".format(stream_urls))
         vids_failed.append(vid)
         continue
   
   if (vids_failed):
      log(30, 'Failed to retrieve videos: {0}.'.format(vids_failed))
   log(20, 'All done.')

if (__name__ == '__main__'):
   main()
