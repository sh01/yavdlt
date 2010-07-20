#!/usr/bin/env python3
# yt_getter: Download information from youtube
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
import html.parser
import http.client
import logging
import os.path
import urllib.request
import re
import xml.dom.minidom

from io import BytesIO

xml_unescape = html.parser.HTMLParser().unescape

YTAnnotationRRBase = collections.namedtuple('YTAnnotationRR', ('t','x','y','w','h','d'))
YTAnnotationBase = collections.namedtuple('YTAnnotationBase', ('id','author','type','content', 'style','r1','r2'))


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


def _second2ssa_ts(seconds):
   hours = int(seconds//3600)
   seconds %= 3600
   minutes = int(seconds//60)
   seconds %= 60
   return '{0:d}:{1:02d}:{2:05.2f}'.format(hours, minutes, seconds)
   

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
      
      return cls(**kwargs)
   
   def fmt_ssa(self):
      return 'Dialogue: 0,{0},{1},Default,,0000,0000,0000,,{2}'.format(
         _second2ssa_ts(self.r1.t),
         _second2ssa_ts(self.r2.t),
         self.content.replace('\n', '\\N')
      )
   
   def __cmp__(self, other):
      if (self.r1 < other.r1): return -1
      if (self.r1 > other.r1): return 1
      if (self.r2 < other.r2): return -1
      if (self.r2 > other.r2): return 1
      if (id(self) < id(other)): return -1
      if (id(self) > id(other)): return 1
      return 0
   
   def is_sublike(self):
      return not (
         (self.content is None) or
         (self.r1 is None) or
         (self.r2 is None) or
         (self.r1.t is None) or
         (self.r2.t is None)
      )
   
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


def parse_ytanno(f):
   import xml.dom.minidom
   domtree = xml.dom.minidom.parse(f)
   anno_nodes = domtree.getElementsByTagName('annotation')
   annotations = [YTAnnotation.build_from_xmlnode(n) for n in anno_nodes]
   annotations.sort()
   return annotations


def dump_ytannos_ssa(annotations, file_out):
   # Include UTF-8 BOM; according to user reports some players care about this
   file_out.write(b'\xef\xbb\xbf')
   file_out.write(b'[Script Info]\r\n')
   file_out.write(b'ScriptType: v4.00+\r\n')
   file_out.write(b'[V4+ Styles]\r\n')
   file_out.write(b'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\r\n')
   file_out.write(b'Style: Default,,20,&H00FFFFFF,&HFFFFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1\r\n')
   file_out.write(b'[Events]\r\n')
   file_out.write(b'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n')
   for annotation in annotations:
      if (not annotation.is_sublike()):
         continue
      file_out.write(annotation.fmt_ssa().encode('utf-8'))
      file_out.write(b'\r\n')


class YTimedTextList:
   logger = logging.getLogger('YTimedTextList')
   log = logger.log
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
         tt_entries = YTimedTextEntry.parse_block(content)
         rv.append((name, lc, tt_entries))
         
      return rv


class YTimedTextEntry:
   def __init__(self, ts_start, dur, text):
      self.ts_start = ts_start
      self.dur = dur
      self.text = text
   
   @classmethod
   def parse_block(cls, content):
      dom = xml.dom.minidom.parseString(content)
      rv = []
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
         rv.append(cls(
            float(node.attributes['start'].value),
            dur,
            text
         ))
      return tuple(rv)
   
   def __cmp__(self, other):
      if (self.ts_start < other.ts_start): return -1
      if (self.ts_start > other.ts_start): return 1
      if (self.dur < other.dur): return -1
      if (self.dur > other.dur): return 1
      if (id(self) < id(other)): return -1
      if (id(self) > id(other)): return 1
      return 0
   
   def is_sublike(self):
      return True
   
   def fmt_ssa(self):
      return 'Dialogue: 0,{0},{1},Default,,0000,0000,0000,,{2}'.format(
         _second2ssa_ts(self.ts_start),
         _second2ssa_ts(self.ts_start+self.dur),
         self.text.replace('\n', '\\N')
      )

class YTError(Exception):
   pass

class YTLoginRequired(YTError):
   pass

class YTDefaultFmt:
   def __str__(self):
      return 'default'

FMT_DEFAULT = YTDefaultFmt()

class YTVideoRef:
   re_tok = re.compile('&t=(?P<field_t>[^"&]+)&')
   re_title = re.compile('<link rel="alternate" +type="application/json\+oembed" +href="[^"]*" +title="(?P<text>.*?)" */>')
   re_err = re.compile('<div[^>]* class="yt-alert-content"[^>]*>(?P<text>.*?)</div>', re.DOTALL)
   re_err_age = re.compile('<div id="verify-age-details">(?P<text>.*?)</div>', re.DOTALL)
   re_fmt_url_map_markup = re.compile(r'\? "(?P<umm>.*?fmt_url_map=.*?>)"')
   re_fmt_url_map = re.compile('fmt_url_map=(?P<ums>[^"&]+)&')
   
   URL_FMT_WATCH = 'http://www.youtube.com/watch?v={0}&fmt={1}'
   URL_FMT_GETVIDEO = 'http://www.youtube.com/get_video?video_id={0}&t={1}{2}'
   URL_FMT_GETVIDEOINFO = 'http://youtube.com/get_video_info?video_id={0}'
   
   logger = logging.getLogger('YTVideoRef')
   log = logger.log
   
   MIME_EXT_MAP = {
      'video/mp4': 'mp4',
      'video/x-flv': 'flv',
      'video/webm': 'webm'
   }

   def __init__(self, vid, format_pref_list):
      self.vid = vid
      self.tok = None
      self._mime_type = None
      self.fmt_url_map = {}
      self.force_fmt_url_map_use = False
      self.got_video_info = False
      self.title = None
      self.fpl = format_pref_list
   
   def mangle_yt_url(self, url):
      """This function will be called to preprocess any and all YT urls.
      
      This default implementation simply returns its first argument. If you
      want to perform URL mangling, override or overwrite this method for the
      relevant YTVideoRef instance(s) with a callable that implements the
      mapping you desire."""
      return url
   
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
      self.fmt_url_map_update(ums)
      
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
         
         err_text = m_err.groupdict()['text'].strip()
         #err_text = err_text.replace('<br/>', '')
         #err_text = xml_unescape(err_text)
         raise error_cls('YT refuses to deliver token: {0!a}.'.format(err_text))
        
      tok = m.groupdict()['field_t']
      
      m = self.re_title.search(content)
      if (m is None):
         self.log(30, 'Unable to extract video title; this probably indicates a yt_getter bug.')
         self.title = '--untitled--'
      else:
         self.title = m.groupdict()['text']

      self.log(20, 'Acquired token {0}.'.format(tok))
      self.tok = tok
      
      self.fmt_url_map_update_markup(content)
   
   def choose_fn(self, ext=None):
      title = self.title
      mtitle = ''
      for c in title:
         if (c.isalnum() or (c in '-')):
            mtitle += c
         elif (c in ' _'):
            mtitle += '_'
      
      if (ext is None):
         ext = self.MIME_EXT_MAP.get(self._mime_type,'bin')
      
      return 'yt_{0}_{1}.{2}'.format(self.vid, mtitle, ext)
   
   def fetch_data(self):
      self.fetch_video()
      self.fetch_annotations()
      self.fetch_tt()
   
   def fetch_video(self):
      from fcntl import fcntl, F_SETFL
      from select import select
      from os import O_NONBLOCK
      
      if (self.tok is None):
         self.get_token_blocking()
      
      url = self.pick_video()
      if (url is None):
         raise YTError('Unable to pick video fmt; bailing out.')
      
      fn_out = self.choose_fn()
      try:
         f = open(fn_out, 'r+b')
      except IOError:
         f = open(fn_out, 'w+b')
      
      f.seek(0,2)
      flen = f.tell()
      if (flen > self._content_length):
         raise YTError('Existing local file longer than remote version; not attempting to retrieve.')
      elif (flen == self._content_length):
         self.log(20, 'Local file {0!r} appears to be complete already.'.format(fn_out, url))
         return
         
      self.log(20, 'Fetching data from {0!r}.'.format(url))
      
      plen = 128
      off_start = max(flen - plen, 0)
      req_headers = {}
      if (off_start > 0):
         f.seek(off_start)
         prefix_data = f.read()
         if (len(prefix_data) != plen):
            raise YTError('Target file appears ot have changed size from under us; bailing out.')
         req_headers['Range'] = 'bytes={0}-'.format(off_start)
      else:
         prefix_data = None
      
      req = urllib.request.Request(url, headers=req_headers)
      res = urllib.request.urlopen(req)
      
      if (off_start and (res.code != 206)):
         raise YTError('Download resume failed; got unexpected HTTP response code {0}.'.format(res.code))
      
      cl = self._content_length
      
      try:
         cl_r = int(res.headers.get('content-length'))
      except (KeyError, ValueError, TypeError):
         pass
      else:
         if (cl_r != cl-off_start):
            raise YTError('Content length mismatch.')
      
      self.log(20, 'Total length is {0} bytes.'.format(cl))
      
      cl_g = off_start
      if (prefix_data):
         data = res.read(len(prefix_data))
         if (len(data) != len(prefix_data)):
            raise YTError("Download resume failed; premature content body cutoff.")
         if (data != prefix_data):
            raise YTError("Download resume failed; mismatch with existing tail data.")
      
         cl_g += len(prefix_data)
         self.log(15, 'Beginning of remote file matches existing data; resuming download.')
      
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
      f.close()
   
   def fetch_annotations(self):
      url = self.url_get_annots()
      self.log(20, 'Fetching annotations from {0!a}.'.format(url))
      req = urllib.request.urlopen(url)
      content = req.read()
      self.log(20, 'Parsing annotation data.')
      annotations = parse_ytanno(BytesIO(content))
      if (len(annotations) < 1):
         self.log(20, 'There are no annotations for this video.')
         return
      
      fn_out = self.choose_fn('ssa')
      self.log(20, 'Received {0:d} annotations; writing to {1!a}.'.format(len(annotations), fn_out))
      f = open(fn_out, 'wb')
      dump_ytannos_ssa(annotations, f)
      f.close()
   
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

      for ((name, lc, ttel)) in tdata:
         lc = lc.replace('/', '').replace('\x00','')
         name = name.replace('/', '').replace('\x00','')
         fn = self.choose_fn('{0}_{1}.ssa'.format(lc, name))
         self.log(20, 'Writing timedtext data for name {0!a}, lc {1} to {2}.'.format(name, lc, fn))
         if (isinstance(fn, str)):
            fn = fn.encode('utf-8')
         f = open(fn, 'wb')
         dump_ytannos_ssa(ttel, f)
         f.close()
   
   def fmt_url_map_fetch_update(self, fmt):
      url = self.URL_FMT_WATCH.format(self.vid, fmt)
      url = self.mangle_yt_url(url)
      content = urllib.request.urlopen(url).read()
      self.fmt_url_map_update_markup(content)
   
   def fmt_url_map_update(self, ums):
      ums_split = ums.split(',')
      
      for umsf in ums_split:
         (fmt_str, url) = umsf.split('|',1)
         fmt = int(fmt_str)
         
         if not (fmt in self.fmt_url_map):
            self.log(20, 'Caching direct url for new format {0:d}.'.format(fmt))
         self.fmt_url_map[fmt] = url
   
   def fmt_url_map_update_markup(self, markup):
      from urllib.parse import unquote
      
      m = self.re_fmt_url_map_markup.search(markup)
      if (m is None):
        return
      umm = m.groupdict()['umm']
      umm_unescaped = umm.decode('string_escape')
      m2 = self.re_fmt_url_map.search(umm_unescaped)
      
      if (m2 is None):
         return
      ums_raw = m2.groupdict()['ums']
      ums = unquote(ums_raw)
      self.fmt_url_map_update(ums)
   
   def pick_video(self):
      from urllib.parse import splittype, splithost
      
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
               self._content_length = content_length
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
   

def arg2vidset(s, fallback=True):
   import logging
   log = logging.getLogger('arg2vidset').log
   res = (
      re.compile('^(?P<vid>[A-Za-z0-9_-]{11})$'),
      re.compile('^http://www.youtube(?:-nocookie)?\.[^/]+/watch?.*v=(?P<vid>[A-Za-z0-9_-]{11})($|[^A-Za-z0-9_-])'),
      re.compile('^http://www.youtube(?:-nocookie)?\.[^/]+/v/(?P<vid>[A-Za-z0-9_-]{11})($|[^A-Za-z0-9_-])')
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


_re_embedded_split = re.compile('<object')
_re_embedded_url1 = re.compile('<param name="movie" value="(?P<yt_url>http://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"')
_re_embedded_url2 = re.compile('<embed src="(?P<yt_url>http://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"')

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
         urls.append(m1.groupdict()['yt_url'])
      if not (m2 is None):
         urls.append(m2.groupdict()['yt_url'])
   
   #urls = [xml_unescape(u) for u in urls]
   return set(urls)

class Config:
   # internal stuff
   logger = logging.getLogger('config')
   log = logger.log
   
   _dt_map = dict(
      v='fetch_video',
      a='fetch_annotations',
      t='fetch_tt'
   )
   config_fn_default = '~/.yavdlt/config'
   
   # config scope
   FMT_DEFAULT = FMT_DEFAULT
   
   # config / cmdline var defaults
   loglevel = 15
   data_type = ''.join(_dt_map.keys())
   config_fn = None
   list_url_manglers = False
   
   def __init__(self):
      self._url_manglers = {}
      self._fmt_preflists = {}
      self._default_fpl = (22, 35, 34, 18, 5, FMT_DEFAULT)
      self._args = None
      
      self.fpl = None
      self.dtype = 'avt'
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
         import base64
         return ''.join((baseurl, '/index.php?q=', base64.encodestring(url).replace('\n','')))
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
      oa('--url-mangler', '-u', dest='url_mangler', metavar='SERVICENAME', help='Fetch metadata pages through specified HTTP gateway')
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
         raise Exception('Unknown url mangler {0!a}.'.format(self.url_mangler)) from exc
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
   
   for vid in vids:
      log(20, 'Fetching data for video with id {0!a}.'.format(vid))
      ref = YTVideoRef(vid, fpl)
      
      if not (um is None):
         ref.mangle_yt_url = um
         ref.force_fmt_url_map_use = True
      
      try:
         ref.get_token_blocking()
      except YTError:
         log(30, 'Failed to retrieve video {0!a}:'.format(vid), exc_info=True)
         vids_failed.append(vid)
         continue         
      
      for c in conf.dtype:
         getattr(ref,conf._dt_map[c])()
   
   if (vids_failed):
      log(30, 'Failed to retrieve videos: {0}.'.format(vids_failed))
   log(20, 'All done.')

if (__name__ == '__main__'):
   main()
