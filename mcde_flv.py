#!/usr/bin/env python3
# yt_getter: Download information from youtube
# Copyright (C) 2010  Sebastian Hagen
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

# Media container data extraction: FLV

from mcio_base import *

import struct

class FLVParserError(ContainerParserError):
   pass

class ASParserError(FLVParserError):
   pass


class _ASTerminator:
   pass

_dh = {}
class ASParser:
   END_MARKER = _ASTerminator()
   
   data_handlers = _dh
   def _adh(type_num):
      def rv(func):
         _dh[type_num] = func
         return func
      return rv
   
   def __init__(self, f, size, off=None):
      if (off is None):
         off = f.tell()
      self.off = off
      self.off_lim = off + size
      self.f = f
   
   def _read(self, count):
      if (self.f.tell() + count > self.off_lim):
         raise ASParserError('Attempted to read {0} bytes beyond valid domain.'.format(self.f.tell() + count - self.off_lim))
      return self.f.read(count)
   
   def parse_data(self):
      self.f.seek(self.off)
      # If the specs were actually correct and people implemented them correctly, we would be able to do self.read_array() here
      # and be fine. Naturally, this isn't the case. Instead we just get a sequence of SCRIPTDATAVALUEs - in practice it should
      # be safe to just read exactly two of them.
      key = self.read_value()
      val = self.read_value()
      return {key:val}
   
   @_adh(0)
   def read_double(self):
      (rv,) = struct.unpack('>d', self._read(8))
      return rv
   
   @_adh(1)
   def read_bool(self):
      (rv,) = struct.unpack('>B', self._read(1))
      return bool(rv)
   
   @_adh(2)
   def read_string(self):
      return self.read_binary().rstrip(b'\x00').decode('utf-8')
   
   @_adh(4)
   def read_binary(self):
      hdr = self._read(2)
      (l,) = struct.unpack('>H', hdr)
      rv = self._read(l)
      if (len(rv) != l):
         raise ASParserError('Insufficient data to finish string of length {0}.'.format(l))
      return rv
   
   @_adh(3)
   def read_array(self):
      rv = {}
      obj = None
      while (True):
         (key, val) = obj = self.read_obj()
         if (obj == ('', self.END_MARKER)):
            break
         if (isinstance(obj, list)):
            break
         rv[key] = val
      return rv

   @_adh(5)
   @_adh(6)
   def read_null(self):
      return None
   
   def read_reference(self):
      (rv,) = struct.unpack('>S', self._read(1))
      return rv
   
   @_adh(8)
   def read_ecmaarray(self):
      self.f.seek(4,1)
      return self.read_array()
   
   @_adh(9)
   def read_endmarker(self):
      return self.END_MARKER
   
   @_adh(10)
   def read_strictarray(self):
      (l,) = struct.unpack('>L', self._read(4))
      return dict(self.read_obj() for i in range(l))
      
   @_adh(11)
   def read_date(self):
      (ts,tz_off) = struct.unpack('>dS', self._read(10))
      ts /= 1000
      return (ts, tz_off)
   
   @_adh(12)
   def read_longstring(self):
      hdr = self._read(4)
      (l,) = struct.unpack('>L', hdr)
      rv = self.f.read(l)
      if (len(rv) != l):
         raise ASParserError('Insufficient data to finish string of length {0}.'.format(l))
      return rv
   
   def read_value(self):
      hdr = self._read(1)
      (t,) = struct.unpack('>B', hdr)
      return self.data_handlers[t](self)

   def read_obj(self):
      return (self.read_string(), self.read_value())
   del(_adh)   
del(_dh)


class FLVTag:
   def __init__(self, ts):
      self.ts = ts
   
   def get_ts(self):
      return self.ts

class FLVDummyTag(FLVTag):
   def __init__(self, ts, ttype, data_r):
      super().__init__(ts)
      self.type = ttype
      self.data_r = data_r
   
   @classmethod
   def build_from_file(cls, f, data_size, ttype):
      data_r = DataRefFile(f, f.tell(), data_size)
      return cls(ttype, data_r)
   

class FLVAudioData(FLVTag):
   type = 8
   SAMPLE_RATE_TABLE = (550, 11000, 22000, 44000)
   
   def __init__(self, ts, data_r, codec, rate, sample_size, channels, aac_pt):
      super().__init__(ts)
      self.data_r = data_r
      self.codec = codec
      self.sample_freq = rate
      self.sfreq = sample_size
      self.channels = channels
      self.aac_pt = aac_pt

   def check_stream_consistency(self, other):
      return ((self.codec == other.codec) and (self.sfreq == other.sfreq) and (self.channels == other.channels))

   def is_header(self):
      if (self.aac_pt is None):
         return None
      return (self.aac_pt == 0)
   
   def get_framedata(self):
      return (self.get_ts(), None, self.data_r, True)
   
   @classmethod
   def build_from_file(cls, f, data_size, ts):
      hdr_data = f.read(1)
      (flags,) = struct.unpack('>B', hdr_data)
      codec_id = (flags & 240) >> 4
      sound_rate = (flags & 12) >> 2
      sound_size = (flags & 2) >> 1
      channel_count = (flags & 1) + 1
      
      body_size = data_size - 1
      
      if (codec_id == 10):
         aacpt_data = f.read(1)
         (aacpt,) = struct.unpack('>B', aacpt_data)
         body_size -= 1
      else:
         aacpt = None
      
      data_r = DataRefFile(f, f.tell(), body_size)
      return cls(ts, data_r, codec_id, cls.SAMPLE_RATE_TABLE[sound_rate], sound_size, channel_count, aacpt)


class FLVVideoData(FLVTag):
   type = 9
   
   def __init__(self, ts, data_r, is_keyframe, disposable, codec, avc_pt, ct_off):
      super().__init__(ts)
      self.data_r = data_r
      self.is_keyframe = is_keyframe
      self.disposable = disposable
      self.codec = codec
      self.avc_pt = avc_pt
      self.ct_off = ct_off

   def check_stream_consistency(self, other):
      return (self.codec == other.codec)

   def get_ts(self):
      rv = super().get_ts()
      if not (self.ct_off is None):
         rv += self.ct_off
      return rv

   def get_framedata(self):
      return (self.get_ts(), None, self.data_r, self.is_keyframe)

   def is_header(self):
      if (self.avc_pt is None):
         return None
      return (self.avc_pt == 0)
   
   @classmethod
   def build_from_file(cls, f, data_size, ts):
      hdr_data = f.read(1)
      (flags,) = struct.unpack('>B', hdr_data)
      frame_type = (flags & 240) >> 4
      codec_id = (flags & 15)
      
      body_size = data_size - 1
      
      if (codec_id == 7):
         hdr2_data = f.read(4)
         (avc_pt, ct_hb) = struct.unpack('>BBxx', hdr2_data)
         hdr2_buf = bytearray(4)
         if (ct_hb & 128):
            # It's a negative value; pad with 1 bits
            hdr2_buf[0] = 255
         hdr2_buf[1:4] = hdr2_data[1:4]
         (ct_off,) = struct.unpack('>L', hdr2_buf)
         body_size -= 4
      else:
         avc_pt = None
         ct_off = None
      
      data_r = DataRefFile(f, f.tell(), body_size)
      is_keyframe = frame_type in (1,5)
      is_disposable = frame_type == 3
      
      return cls(ts, data_r, is_keyframe, is_disposable, codec_id, avc_pt, ct_off)
      

class FLVScriptData(FLVTag):
   type = 18
   
   def __init__(self, ts, asp):
      super().__init__(ts)
      self.asp = asp
   
   def get_metadata(self):
      asp_data = self.asp.parse_data()
      try:
         rv = asp_data['onMetaData']
      except KeyError:
         rv = None
      return rv
   
   @classmethod
   def build_from_file(cls, f, data_size, ts):
      data_r = DataRefFile(f, f.tell(), data_size)
      asp = ASParser(f, data_size)
      return cls(ts, asp)


def _h264_id_get_ls(bd):
   (config_ver, pi, pc, li, ls_raw, sps_raw) = struct.unpack('>6B', bd[:6])
   if (config_ver != 1):
      raise FLVParserError('Incompatible config version {0}.'.format(config_ver))
   ls = (ls_raw & 3) + 1
   return ls

def _h264_nalu_seq_read(data_r, length_size):
   data = memoryview(data_r.get_data())
   lbuf = bytearray(4)
   rv = []
   while (data):
      lbuf[-length_size:] = data[:length_size]
      (body_size,) = struct.unpack('>L', lbuf)
      nal_b1 = data[length_size][0]
      nu_type = nal_b1 & 31
      nu_ref_idc = (nal_b1 & 96) >> 5
      
      header_size = length_size
      
      #if (nu_type in (5,6,9,10,11,12)):
         #raise FLVParserError("Invalid ref_idc {0} for nal unit type {1}.".format(nu_ref_idc, nu_type))
      
      if (nal_b1 >= 128):
         raise FLVParserError('NAL unit init sequence mismatch.')
      
      skip = body_size + header_size
      #print(len(data),skip,nu_type)
      if (skip > len(data)):
         if (len(data) != 11):
            print(skip, len(data))
         #raise FLVParserError('Element boundary overrun.')
      
      data = data[skip:]
      rv.append(nu_type)
   #print(rv)
      


class FLVReader:
   bfmt_file_header = '>3sBBL'
   bfmt_file_header_len = struct.calcsize(bfmt_file_header)
   bfmt_tag_header = '>B3s3sB3x'
   bfmt_tag_header_len = struct.calcsize(bfmt_tag_header)
   bfmt_tag_header2 = '>LL'
   
   tagtype_cls_map = {}
   
   VIDEO_CODEC_MKV_MAP = {
      7: 'V_MPEG4/ISO/AVC'
   }
   
   AUDIO_CODEC_MKV_MAP = {
       2: 'A_MPEG/L3',
      10: 'A_AAC',
      14: 'A_MPEG/L3'
   }
   
   for _cls in (FLVAudioData, FLVVideoData, FLVScriptData):
      tagtype_cls_map[_cls.type] = _cls
   
   def __init__(self, f):
      self.f = f
  
   def parse_header(self, off_base=None):
      if (off_base is None):
         off_base = self.f.tell()
      else:
         self.f.seek(off_base)
      hdr_data = self.f.read(self.bfmt_file_header_len)
      (sig, version, flags, data_off) = struct.unpack(self.bfmt_file_header, hdr_data)
      if (sig != b'FLV'):
         raise FLVParserError("Header didn't start with b'FLV'.")
      has_video = bool(flags & 1)
      has_audio = bool(flags & 2)
      
      self.f.seek(off_base + data_off)
      ts0_data = self.f.read(4)
      if (ts0_data != b''):
         (ts0,) = struct.unpack('>L', ts0_data)
         if (ts0 != 0):
            raise FLVParserError("Header followed by nonzero tagsize entry {0!a}.".format(ts0_data))
      
      return (version, data_off, has_video, has_audio)
   
   def make_mkvb(self, write_app):
      from collections import deque
      import mcio_matroska
      from mcio_matroska import MatroskaBuilder
      
      video_init = None
      audio_init = None
      vd = {}
      ad = {}
      
      for d in (ad, vd):
         d['data'] = deque()
         d['init_data'] = None
      
      avtmap = {8:ad, 9: vd}
         
      md = {
         'duration':None,
         'width':None,
         'height':None,
         'codec':None
      }
      
      for tag in self.parse_tags():
         try:
            d = avtmap[tag.type]
         except KeyError:
            if (tag.type == 18):
               md.update(tag.get_metadata())
            continue
         
         try:
            tag_last = d['data'][-1]
         except IndexError:
            d['codec'] = tag.codec
            if (isinstance(tag, FLVAudioData)):
               d['sfreq'] = tag.sfreq
               d['channel_count'] = tag.channels
               
         else:
            if not (tag_last.check_stream_consistency(tag)):
               raise FLVError('Stream metadata inconsistency between {0} and {1}.'.format(tag_prev, tag))
         
         if (tag.is_header()):
            d['init_data'] = tag.data_r.get_data()
         else:
            d['data'].append(tag)
            
         if (tag.type == 9):
            if (tag.is_header()):
               ls = (_h264_id_get_ls(d['init_data']))
            else:
               _h264_nalu_seq_read(tag.data_r, ls)
      
      mb = MatroskaBuilder(write_app, 1000000, md['duration'])
      
      print(400.186/len(ad['data']), 400.186/len(vd['data']))
      
      try:
         vc_mkv = self.VIDEO_CODEC_MKV_MAP[vd['codec']]
      except KeyError:
         pass
      else:
         width = md['width']
         height = int(md['height'])
         if not (width is None):
            width = int(width)
         if not (height is None):
            height = int(height)
         mb.add_track((t.get_framedata() for t in vd['data']), mcio_matroska.TRACKTYPE_VIDEO, vc_mkv, vd['init_data'], True,
            width, height)
         
      try:
         ac_mkv = self.AUDIO_CODEC_MKV_MAP[ad['codec']]
      except KeyError:
         pass
      else:
         mb.add_track((t.get_framedata() for t in ad['data']), mcio_matroska.TRACKTYPE_AUDIO, ac_mkv, ad['init_data'], False,
            ad['sfreq'], ad['channel_count'])
      
      return mb
   
   def parse_tags(self):
      off = self.f.tell()
      while (True):         
         header_data = self.f.read(self.bfmt_tag_header_len)
         if (header_data == b''):
            # End of FLV data.
            break
         if (len(header_data) != self.bfmt_tag_header_len):
            raise FLVParserError('Failed to read another full tag header; expected {0} bytes, but only got {1!a}.'.format(
               self.bfmt_tag_header_len, header_data))
         
         hdr2_data = bytearray(8)
         # Stupid 24bit-sized ints, and middle-endian 32bit ints.
         (ttype, hdr2_data[1:5], hdr2_data[6:9], hdr2_data[5:6]) = struct.unpack(self.bfmt_tag_header, header_data)
         (body_size, ts) = struct.unpack(self.bfmt_tag_header2, hdr2_data)
         tag_size = body_size + self.bfmt_tag_header_len
         
         try:
            tcls = self.tagtype_cls_map[ttype]
         except KeyError:
            tag = FLVDummyTag.build_from_file(self.f, body_size, ts, ttype)
         else:
            tag = tcls.build_from_file(self.f, body_size, ts)
         
         self.f.seek(off + tag_size)
         
         (tag_size2,) = struct.unpack('>L', self.f.read(4))
         if (tag_size != tag_size2):
            raise FLVParserError("Tag header-derived size == {0} != {1} == post-tag size.".format(tag_size,tag_size2))
         
         yield(tag)
         
         off += tag_size + 4
         self.f.seek(off)
         


def _main():
   import sys
   fn = sys.argv[1]
   f = open(fn, 'rb')
   flvr = FLVReader(f)
   flvr.parse_header()
   mb = flvr.make_mkvb('mcde_flv selftester pre-versioning version')
   mb.write_to_file(open(b'__flvdump.mkv.tmp', 'wb'))
   

if (__name__ == '__main__'):
   _main()
