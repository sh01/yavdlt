#!/usr/bin/env python
# yt_getter: Download information from youtube
# Copyright (C) 2009  Sebastian Hagen
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


import collections
import httplib
import logging
import urllib2
import re
import xml.dom.minidom

from cStringIO import StringIO

def xml_unescape(s):
   import htmllib
   p = htmllib.HTMLParser(None)
   p.save_bgn()
   p.feed(s)
   return p.save_end()


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
   hours = (seconds//3600)
   seconds %= 3600
   minutes = (seconds//60)
   seconds %= 60
   return '%d:%02d:%05.2f' % (hours, minutes, seconds)
   

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
         fc = node.getElementsByTagName('TEXT')[0].firstChild
         if (fc is None):
            text = ''
         else:
            text = fc.nodeValue
         kwargs['content'] = text
      else:
         kwargs['content'] = None
      
      return cls(**kwargs)
   
   def fmt_ssa(self):
      return u'Dialogue: 0,%s,%s,Default,,0000,0000,0000,,%s' % (
         _second2ssa_ts(self.r1.t),
         _second2ssa_ts(self.r2.t), 
         self.content
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
         (self.type != 'text') or
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


def print_ytannos_hr(annotations):
   for annotation in annotations:
      if (not annotation.is_sublike()):
         continue
      print '%8.2f %8.2f   %r' % (annotation.r1.t, annotation.r2.t, annotation.content)


def dump_ytannos_ssa(annotations, file_out):
   file_out.write('[Script Info]\r\n')
   file_out.write('ScriptType: v4.00+\r\n')
   file_out.write('[V4+ Styles]\r\n')
   file_out.write('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\r\n')
   file_out.write('Style: Default,,20,&H00FFFFFF,&HFFFFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1\r\n')
   file_out.write('[Events]\r\n')
   file_out.write('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n')
   for annotation in annotations:
      if (not annotation.is_sublike()):
         continue
      file_out.write(annotation.fmt_ssa().encode('utf-8'))
      file_out.write('\r\n')


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
      return 'http://video.google.com/timedtext?hl=en&v=%s&type=track&name=%s&lang=%s' % (self.vid, name, lc)
   
   def fetch_all_blocking(self):
      rv = []
      for (name, lc) in self.tdata:
         url = self.get_url(name, lc)
         self.log(20, 'Fetching timedtext data from %r and processing.' % (url))
         req = urllib2.urlopen(url)
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
         rv.append(cls(
            float(node.attributes['start'].value),
            float(node.attributes['dur'].value),
            xml_unescape(node.firstChild.nodeValue)
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
      return u'Dialogue: 0,%s,%s,Default,,0000,0000,0000,,%s' % (
         _second2ssa_ts(self.ts_start),
         _second2ssa_ts(self.ts_start+self.dur),
         self.text
      )

class YTError(StandardError):
   pass

class YTDefaultFmt:
   def __str__(self):
      return 'default'

class YTVideoRef:
   re_tok = re.compile('"t": "(?P<field_t>[^"]*)"')
   re_title = re.compile('<h1[^>]*>(?P<text>[^<]*)</h1[^>]*>')
   re_err = re.compile('<div id="error-box"[^>]*>(?P<text>[^<]+)</div>')
   re_fmt_url_map = re.compile('"fmt_url_map": *"(?P<ums>[^"]+)"')
   
   FMT_DEFAULT = YTDefaultFmt()
   URL_FMT_WATCH = 'http://www.youtube.com/watch?v=%s&fmt=%s'
   URL_FMT_GETVIDEO = 'http://www.youtube.com/get_video?video_id=%s&t=%s%s'
   
   logger = logging.getLogger('YTVideoRef')
   log = logger.log
   fmt_exts = {
       5: 'flv',
       6: 'flv',
      17: 'mp4',
      18: 'mp4',
      22: 'mp4',
      34: 'flv',
      35: 'flv',
      FMT_DEFAULT: 'flv'
   }
   
   fmts = (
      18, # mp4/h264, SQ
      22, # mp4/h264, HQ
      35, # flv/h264, SQ
      34, # flv/h264, LQ
       6, # flv/sor, SQ
       5, # flv/sor, LQ
    FMT_DEFAULT # some flv thing
    )
   
   def __init__(self, vid, fmt=None):
      self.vid = vid
      self.tok = None
      self.fmt = fmt
      self._fmt = None
      self.fmt_url_map = {}
      self.force_fmt_url_map_use = False
      self.title = None
   
   def url_get_annots(self):
      return 'http://www.google.com/reviews/y/read2?video_id=%s' % (self.vid,)
   
   def get_token_blocking(self):
      self.log(20, 'Acquiring YT video token.')
      if (self.fmt):
         fmt = self.fmt
      else:
         fmt = self.fmts[0]
      
      if (fmt is self.FMT_DEFAULT):
         fmt = ''
      
      url = self.URL_FMT_WATCH % (self.vid, fmt)
      content = urllib2.urlopen(url).read()
      
      m = self.re_tok.search(content)
      if (m is None):
         m_err = self.re_err.search(content)
         if (m_err is None):
            raise StandardError("YT markup failed to match expectations; can't extract video token.")
         
         err_text = xml_unescape(m_err.groupdict()['text'].strip())
         raise YTError('YT refuses to deliver token: %r' % (err_text,))
        
      tok = m.groupdict()['field_t']
      
      m = self.re_title.search(content)
      if (m is None):
         self.title = '--untitled--'
      else:
         self.title = m.groupdict()['text']

      self.log(20, 'Acquired token %r.' % (tok,))
      self.tok = tok
      
      self.fmt_url_map_update(content)
   
   def choose_fn(self, ext=None):
      title = self.title
      mtitle = ''
      for c in title:
         if (c.isalnum() or (c in '-')):
            mtitle += c
         elif (c in ' _'):
            mtitle += '_'
      
      if (ext is None):
         ext = self.fmt_exts.get(self._fmt,'bin')
      
      return 'yt_%s_%s.%s' % (self.vid, mtitle, ext)
   
   def fetch_data(self):
      self.fetch_video()
      self.fetch_annotations()
      self.fetch_tt()
   
   def fetch_video(self):
      if (self.tok is None):
         self.get_token_blocking()
      
      url = self.pick_video()
      if (url is None):
         raise StandardError('Unable to pick video fmt; bailing out.')
      fn_out = self.choose_fn()
      self.log(20, 'Fetching data from %r.' % (url,))
      
      req = urllib2.urlopen(url)
      content = req.read()
      self.log(20, 'Got %d bytes of video data.' % (len(content,)))
      self.log(20, 'Writing video data to %r.' % (fn_out,))
      f = file(fn_out, 'wb')
      f.write(content)
      f.close()
   
   def fetch_annotations(self):
      url = 'http://www.google.com/reviews/y/read2?video_id=%s' % (self.vid,)
      self.log(20, 'Fetching annotations from %r.' % (url,))
      req = urllib2.urlopen(url)
      content = req.read()
      self.log(20, 'Parsing annotation data.')
      annotations = parse_ytanno(StringIO(content))
      if (len(annotations) < 1):
         self.log(20, 'There are no annotations for this video.')
         return
      
      fn_out = self.choose_fn('ssa')
      self.log(20, 'Received %d annotations; writing to %r.' % (len(annotations), fn_out))
      f = file(fn_out, 'wb')
      dump_ytannos_ssa(annotations, f)
      f.close()
   
   def fetch_tt(self):
      url = 'http://video.google.com/timedtext?v=%s&type=list' % (self.vid,)
      self.log(20, 'Checking for timedtext data.')
      req = urllib2.urlopen(url)
      content = req.read()
      if (content == ''):
         self.log(20, 'No timedtext data found.')
         return
      
      ttl = YTimedTextList.build_from_markup(self.vid, content)
      tdata = ttl.fetch_all_blocking()
      
      if (len(tdata) < 1):
         self.log(20, 'No timedtext streams found.')

      for ((name, lc, ttel)) in tdata:
         lc = lc.replace('/', '').replace('\x00','')
         name = name.replace('/', '').replace('\x00','')
         fn = self.choose_fn('%s_%s.ssa' % (lc, name))
         self.log(20, 'Writing timedtext data for name %r, lc %r to %r.' % (name, lc, fn))
         f = file(fn, 'wb')
         dump_ytannos_ssa(ttel, f)
         f.close()
   
   def fmt_url_map_fetch_update(self, fmt):
      url = self.URL_FMT_WATCH % (self.vid, fmt)
      content = urllib2.urlopen(url).read()
      self.fmt_url_map_update(content)

   def fmt_url_map_update(self, markup):
      from urllib import unquote
      
      m = self.re_fmt_url_map.search(markup)
      if (m is None):
         return
      ums_raw = m.groupdict()['ums']
      ums = unquote(ums_raw)
      ums_split = ums.split(',')
      
      for umsf in ums_split:
         (fmt_str, url) = umsf.split('|',1)
         fmt = int(fmt_str)
         
         if not (fmt in self.fmt_url_map):
            self.log(20, 'Caching direct url for new format %d.' % (fmt,))
         self.fmt_url_map[fmt] = url
   
   def pick_video(self):
      if (self.fmt):
         fmts = (self.fmt,)
      else:
         fmts = self.fmts
      
      for fmt in fmts:
         url = self.get_video_url(fmt)
         rc = 301
         
         while (301 <= rc <= 303):
            (type_, dp) = urllib2.splittype(url)
            (host, path) = urllib2.splithost(dp)
            conn = httplib.HTTPConnection(host)
            conn.request('HEAD',path)
            try:
               response = conn.getresponse()
            except httplib.BadStatusLine:
               # Happens for some responses ... don't know why, don't really
               # care.
               rc = None
               break
            rc = response.status
            if (301 <= rc <= 303):
               url = response.getheader('location')
         
         if (rc == 200):
            self.log(20, 'Fmt %s is good ... using that.' % (fmt,))
            self._fmt = fmt
            return url
         
         self.log(20, 'Tried to get video in fmt %s and failed (http response %r).' % (fmt, rc))
         
      else:
         self.log(38, 'None of the attempted formats worked out.')
         return None
   
   def get_video_url(self, fmt):
      if (self.tok is None):
         raise ValueError('Need to get token first.')
      
      if (self.force_fmt_url_map_use and (not (fmt in self.fmt_url_map))):
         self.fmt_url_map_fetch_update(fmt)
      
      if (fmt in self.fmt_url_map):
         self.log(20, 'Using cached direct video url.')
         return self.fmt_url_map[fmt]
      
      if (fmt is self.FMT_DEFAULT):
         fmtstr = ''
      else:
         fmtstr = '&fmt=%d' % (fmt,)
      return self.URL_FMT_GETVIDEO % (self.vid, self.tok, fmtstr)


class YTPlayListRef:
   logger = logging.getLogger('YTPlaylistRef')
   log = logger.log
   
   pl_base_url = 'http://gdata.youtube.com/feeds/api/playlists/%s?v=2'
   def __init__(self, plid):
      self.plid = plid
      self.vids = []
   
   def fetch_pl(self):
      """Fetch playlist and parse out vids."""
      pl_url = self.pl_base_url % self.plid
      self.log(20, 'Retrieving playlist from %r.' % (pl_url,))
      req = urllib2.urlopen(pl_url)
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
      
      self.log(20, 'Got %d playlist entries: %r' % (len(vids_l), vids_l))
      self.vids = vids_l
   

def arg2vidset(s, fallback=True):
   import logging
   log = logging.getLogger('arg2vidset').log
   res = (
      re.compile('http://www.youtube(?:-nocookie)?\.[^/]+/watch?.*v=(?P<vid>[^&#]+)'),
      re.compile('http://www.youtube(?:-nocookie)?\.[^/]+/v/(?P<vid>[^&#]+)')
   )
   
   if (len(s) == 11):
      return [s]
   for rx in res:
      m = rx.search(s)
      if (m is None):
         continue
      return set((m.groupdict()['vid'],))
   else:
      if (fallback):
         log(20, "%r doesn't look like a direct video spec ... treating as url to embedding document." % (s,))
         rv = set()
         for url in get_embedded_yturls(s):
            rv.update(arg2vidset(url, fallback=False))
         return rv
   
   raise ValueError('Unable to get video id from string %r.' % (s,))


_re_embedded_split = re.compile('<object')
_re_embedded_url1 = re.compile('<param name="movie" value="(?P<yt_url>http://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"')
_re_embedded_url2 = re.compile('<embed src="(?P<yt_url>http://[^"/]*youtube(?:-nocookie)?\.[^"/]+/v/[^"]+)"')

def get_embedded_yturls(url):
   import logging
   log = logging.getLogger('embed_fetch').log
   
   log(20, 'Fetching embedding document %r' % (url,))
   req = urllib2.urlopen(url)
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


def url_mangle_sixxs_46gw(url):
   from urllib import splithost, splittype
   (utype, urest) = splittype(url)
   (uhost, upath) = splithost(urest)
   uhost += '.sixxs.org'
   rv = '%s://%s%s' % (utype, uhost, upath)
   return rv


if (__name__ == '__main__'):
   import optparse
   import os.path
   import sys
   
   logger = logging.getLogger()
   log = logger.log
   
   logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
      stream=sys.stderr, level=logging.DEBUG)
   
   dt_map = dict(
      v='fetch_video',
      a='fetch_annotations',
      t='fetch_tt'
   )
   
   op = optparse.OptionParser(usage="%prog [options] <yt video id>*")
   op.add_option('-d', '--data-type', dest='dtype', default=''.join(dt_map.keys()), help='Data types to download')
   op.add_option('--clobber', default=False, action='store_true', help='Refetch videos and overwrite existing video files')
   op.add_option('--fmt', default=None, help="YT format number to use.")
   op.add_option('--playlist', default=None, help='Parse (additional) video ids from specified playlist', metavar='PLAYLIST_ID')
   op.add_option('--watch-ipv6-sixxs', dest='watch_sixxs', default=False, action='store_true', help='Fetch watch pages through sixxs ipv6-to-ipv4 gateway.')
   
   (opts, args) = op.parse_args()
   log(50, 'Init.')
   
   fmt = opts.fmt
   if not (fmt is None):
      fmt = int(fmt)
   
   for c in opts.dtype:
      if not (c in dt_map):
         raise ValueError('Unknown data type %r.' % (c,))
   
   vids_set = set()
   vids = []
   
   def update_vids(s):
      for vid in s:
         if (vid in vids_set):
            continue
         vids_set.add(vid)
         vids.append(vid)
   
   for vid_str in args:
      update_vids(arg2vidset(vid_str))
   
   if (opts.playlist):
      plr = YTPlayListRef(opts.playlist)
      plr.fetch_pl()
      update_vids(plr.vids)
   
   log(20, 'Final vid set: {0}'.format(vids))
   vids_failed = []
   
   for vid in vids:
      log(20, 'Fetching data for video with id %r.' % (vid,))
      ref = YTVideoRef(vid, fmt)
      
      if (opts.watch_sixxs):
         ref.URL_FMT_WATCH = url_mangle_sixxs_46gw(ref.URL_FMT_WATCH)
         ref.force_fmt_url_map_use = True
      
      try:
         ref.get_token_blocking()
      except YTError:
         log(30, 'Failed to retrieve video %r:' % (vid,), exc_info=True)
         vids_failed.append(vid)
         continue
      
      fns = [ref.choose_fn(ext) for ext in ref.fmt_exts.values()]
      for fn in fns:
         if (os.path.exists(fn)):
            vf_exists = True
            break
      else:
         vf_exists = False
      
      if (vf_exists and (not opts.clobber)):
         log(20, '%r exits already; skipping this video.' % (fn,))
         continue
      
      for c in opts.dtype:
         getattr(ref,dt_map[c])()
   
   if (vids_failed):
      log(30, 'Failed to retrieve videos: %s' % (vids_failed,))
   log(50, 'All done.')

