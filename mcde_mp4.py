#!/usr/bin/env python3
# Yet Another Video Download Tool: Download information from youtube
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

# Media container data extraction: MP4

import collections
import datetime
import struct

from fractions import gcd
from functools import reduce

from mcio_base import *
from mcio_codecs import *

_mov_td = (datetime.datetime(1970,1,1) - datetime.datetime(1904,1,1))
_mov_time_offset = -1 * (_mov_td.days*86400 + _mov_td.seconds)
del(_mov_td)

def movts2unixtime(mov_ts):
   """Convert mov TS (seconds since 1904-01-01) to unixtime TS (seconds since 1970-01-01)."""
   return _mov_time_offset + mov_ts

class MovParserError(ContainerParserError):
   pass

class SubboxNotFoundError(MovParserError):
   pass

class BoxBoundaryOverrun(MovParserError):
   pass

fourcc_uuid = FourCC(b'uuid')

class MovBoxTypeUUID(bytes):
   def __new__(cls, x):
      if (isinstance(x, str)):
         x = x.encode('ascii')
      
      if (len(x) != 16):
         raise ValueError('Invalid value {0!a}.'.format(x))
      
      return bytes.__new__(cls, x)

def _make_mbt(x):
   if (isinstance(x,int) or (len(x) == 4)):
      return FourCC(x)
   return MovBoxTypeUUID(x)

class MovContext:
   """Aggregate type for external objects (backing fds) and mov parser state."""
   def __init__(self, f, tolerate_overrun_elements=False):
      self.f = f
      self._track_type = None
      self._t_toe = tolerate_overrun_elements

class MovBox:
   cls_map = {}
   
   def __init__(self, ctx, offset, size, hlen, btype):
      self.c = ctx
      self.hlen = hlen
      self.offset = offset
      self.size = size
      self.type = btype
      self.c.f.seek(self.offset + self.hlen)
      self._init2()

   def _init2(self):
      pass

   def get_body(self):
      """Return raw body data of this box."""
      self.c.f.seek(self.offset + self.hlen)
      bodylen = self.size - self.hlen
      data = self.c.f.read(bodylen)
      if (len(data) != bodylen):
         raise StandardError()
      return data

   def __repr__(self):
      return '{0}({1}, {2}, {3}, {4}({5}))'.format(type(self), self.c.f, self.offset,
         self.size, self.type, struct.pack('>L', self.type))

   def __format__(self, s):
      if (s != 'f'):
         return repr(self)
      
      try:
         formatter = self._format_f
      except AttributeError:
         pass
      else:
         return formatter(s)
      
      if (type(self) == MovBox):
         tstr = '({0})'.format(self.type)
      else:
         tstr = ''
      
      return '<{0}{1}>'.format(type(self).__name__, tstr)

   @classmethod
   def build(cls, ctx, offset, size, hlen, btype):
      try:
         cls = cls.cls_map[btype]
      except KeyError:
         pass
      return cls(ctx, offset, size, hlen, btype)

   @classmethod
   def build_from_ctx(cls, ctx):
      f = ctx.f
      off_start = f.seek(0,1)
      header = f.read(8)
      (size, btype) = struct.unpack('>LL', header)
      btype = FourCC(btype)
      if (size == 1):
         extsz = f.read(8)
         (size,) = struct.unpack('>Q', extsz)
         hlen = 16
      else:
         hlen = 8
         if (size == 0):
            off = f.seek(0,1)
            size = f.seek(0,2) - off_start
            f.seek(off)
      
      if (btype == fourcc_uuid):
         btype = MovBoxTypeUUID(f.read(16))
         hlen += 16
      
      rv = cls.build(ctx, off_start, size, hlen, btype)
      
      if (rv.hlen > size):
         raise MovParserError('Got box with header length == {0} > total length == {1}.'.format(hlen, size))
      
      return rv
   
   @classmethod
   def build_seq_from_ctx(cls, ctx, off_limit=None):
      f = ctx.f
      rv = []
      off = f.seek(0,1)
      if (off_limit is None):
         off_limit = f.seek(0,2)
      
      f.seek(off)
      while ((off < off_limit) and (len(f.read(8)) == 8)):
         f.seek(off)
         atom = cls.build_from_ctx(ctx)
         off += atom.size
         if (off > off_limit):
            break
         
         rv.append(atom)
         f.seek(off)
      
      if ((off > off_limit) and (not ctx._t_toe)):
         raise BoxBoundaryOverrun()
      
      return rv
   
   @classmethod
   def build_seq_from_file(cls, f, *args, **kwargs):
      ctx = MovContext(f, *args, **kwargs)
      return cls.build_seq_from_ctx(ctx)

def _mov_box_type_reg(cls):
   MovBox.cls_map[cls.type] = cls
   return cls

class MovFullBox(MovBox):
   def _init2(self):
      super()._init2()
      self.c.f.seek(self.offset + self.hlen)
      data = self.c.f.read(4)
      (self.version, self.flags) = struct.unpack('>B3s', data)
      self.hlen += 4
   
   def _get_bfmt(self):
      try:
         rv = self.bfmts[self.version]
      except KeyError:
         raise MovParserError('Unable to parse ver {0!a}.'.format(self.version))
      except AttributeError:
         return self.bfmt
      return rv

class MovBoxBranch(MovBox):
   sub_cls_default = MovBox
   
   def _get_subel_off(self):
      return (self.offset + self.hlen)
   
   def _init2(self):
      super()._init2()
      self.c.f.seek(self._get_subel_off())
      try:
         self.sub = self.sub_cls_default.build_seq_from_ctx(self.c, self.offset + self.size)
      except MovParserError as exc:
         self.sub = None
         raise MovParserError('Error parsing subelements of {0}.'.format(self)) from exc

   def find_subbox(self, btype):
      """Find a direct subbox of specified boxtype."""
      btype = _make_mbt(btype)
      
      for box in self.sub:
         if (box.type == btype):
            return box
      raise SubboxNotFoundError('No subboxes of type {0}.'.format(btype))
   
   def find_subboxes(self, btype):
      """Return sequence of all direct subboxes of specified boxtype."""
      btype = _make_mbt(btype)
      rv = []
      for box in self.sub:
         if (box.type != btype):
            continue
         rv.append(box)
      return rv
   
   def __repr__(self):
      return '<{0} ({1}, {2}, {3}) sub: {4}>'.format(type(self), self.c.f, self.offset,
         self.size, self.sub)
      
class MovFullBoxBranch(MovBoxBranch, MovFullBox):
   pass

@_mov_box_type_reg
class MovBoxFtyp(MovBox):
   type = FourCC('ftyp')
   bfmt = '>LL'
   bfmt_len = struct.calcsize(bfmt)
   def _init2(self):
      super()._init2()
      (major_brand, minor_brand) = struct.unpack(self.bfmt, self.get_body()[:self.bfmt_len])
      self.major_brand = FourCC(major_brand)
      self.minor_brand = FourCC(minor_brand)
      self.hlen += self.bfmt_len
      
      self.compatible_brands = cb = []
      off = 0
      bd = self.get_body()
      while (off < len(bd)):
         cb.append(FourCC(struct.unpack('>L', bd[off:off+4])[0]))
         off += 4
   
   def _format_f(self, fs):
      return '<{0} type: {1} major: {2} minor: {3} compat: {4}>'.format(type(self).__name__, self.type, self.major_brand, self.minor_brand, self.compatible_brands)

@_mov_box_type_reg
class MovBoxMovieHeader(MovFullBox):
   type = FourCC('mvhd')
   bfmts = {
      0: '>LLLLLH10x36s7L',
      1: '>QQLQLH10x36s7L'
   }
   
   def _init2(self):
      super()._init2()
      (ts_creat, ts_mod, self.time_scale, self.dur, self.rate_p, self.vol_p, self.mat, pv_time, self.pv_dur, self.poster_time,
      self.select_time, self.select_dur, self.cur_time, self.tid_next) = struct.unpack(self._get_bfmt(), self.get_body())
      
      self.ts_creat = movts2unixtime(ts_creat)
      self.ts_mod = movts2unixtime(ts_mod)
   
   def _format_f(self, s):
      dt_creat = datetime.datetime.fromtimestamp(self.ts_creat)
      dt_mod = datetime.datetime.fromtimestamp(self.ts_mod)
      return '<{0} type: {1} time_scale: {2} ts_creat: {3} ts_mod: {4} dur: {5}>'.format(type(self).__name__, self.type,
         self.time_scale, dt_creat, dt_mod, self.get_dur())
   
   def get_dur(self):
      return self.dur/self.time_scale


class MovBoxSampledataBase(MovFullBox):
   bfmt = '>L'
   bfmt_len = struct.calcsize(bfmt)
   def _parse_header(self):
      data = memoryview(self.get_body())
      (self._elnum,) = struct.unpack(self.bfmt, data[:self.bfmt_len])
      
   def _init2(self):
      super()._init2()
      self._parse_header()

class MovBoxSampleTableBase(MovBoxSampledataBase):
   def _data_table_present(self):
      return True
   
   def _init2(self):
      super()._init2()
      data = memoryview(self.get_body())
      i = self.bfmt_len
      if (self._data_table_present()):
         entry_data = []
         bfmt_entry_len = struct.calcsize(self.bfmt_entry)
         onetuples = (len(self.bfmt_entry.lstrip('<>!')) == 1)
      
         for j in range(self._elnum):
            entry_val = struct.unpack(self.bfmt_entry, data[i:i+bfmt_entry_len])
            if (onetuples):
               (entry_val,) = entry_val
         
            entry_data.append(entry_val)   
            i += bfmt_entry_len   
      
      else:
         entry_data = None
      self.entry_data = entry_data

class MovBoxSampleTableSimple(MovBoxSampleTableBase):
   def __iter__(self):
      return self.entry_data.__iter__()

class MovBoxSampleTableRepeats(MovBoxSampleTableBase):
   def __iter__(self):
      for (count, *data) in self.entry_data:
         for i in range(count):
            if (len(data) == 1):
               yield data[0]
            else:
               yield data

class MovSampleEntry(MovBoxBranch):
   bfmt = '>6xH'
   bfmt_len = struct.calcsize(bfmt)
   # Data from <http://www.mp4ra.org/object.html>
   _OTI2CID = {
      0x20: CODEC_ID_MPEG4_2,
      0x21: CODEC_ID_MPEG4_10,
      0x60: CODEC_ID_MPEG2_2,
      0x61: CODEC_ID_MPEG2_2,
      0x62: CODEC_ID_MPEG2_2,
      0x63: CODEC_ID_MPEG2_2,
      0x64: CODEC_ID_MPEG2_2,
      0x65: CODEC_ID_MPEG2_2,
      
      0x40: CODEC_ID_AAC,
      0x66: CODEC_ID_AAC,
      0x67: CODEC_ID_AAC,
      0x68: CODEC_ID_AAC,
      0x69: CODEC_ID_MPEG2_3,
      0x6B: CODEC_ID_MP3,
      0x6D: CODEC_ID_PNG,
      0xA5: CODEC_ID_AC3
   }
   
   def _init2(self):
      self.hlen += self.bfmt_len
      (self.dri,) = struct.unpack(self.bfmt, self.get_body()[:self.bfmt_len])
   
   def get_codec_init_data(self):
      """Return codec-specific initialization data"""
      try:
         esds = self.find_subbox('esds')
      except SubboxNotFoundError:
         return None
      
      return esds.get_dsi()
   
   def get_codec(self):
      """Return codec id for this stream."""
      try:
         esds = self.find_subbox('esds')
      except SubboxNotFoundError as exc1:
         try:
            rv = self._B2CID[self.type]
         except KeyError as exc2:
            raise MovParserError('Unable to determine codec id for sample entry box {0!a}: no esds subbox, and unknown type.'.format(self)) from exc1
      else:
         oti = esds.get_oti()
         try:
            rv = self._OTI2CID[oti]
         except KeyError as exc:
            raise MovParserError('Unknown mp4 OTI {0!a}.'.format(oti)) from exc
      return rv
   
   def _format_f(self, fs):
      return '<{0} type: {1} dri: {2}>'.format(type(self).__name__, self.type, self.dri)

@_mov_box_type_reg
class MovBoxPixelAspectRatio(MovBox):
   type = FourCC('pasp')
   bfmt = '>LL'
   bfmt_hlen = struct.calcsize(bfmt)
   def _init2(self):
      (self.hs, self.vs) = struct.unpack(self.bfmt, self.get_body())
      self.hlen += self.bfmt_hlen
      super()._init2()
   
   def get_ar(self):
      return (self.hs/self.vs)
   
   def __format__(self, fs):
      return '<{0} ar: {1}>'.format(type(self).__name__, self.get_ar())
   

@_mov_box_type_reg
class MovBoxSampleDescription(MovFullBoxBranch):
   type = FourCC('stsd')
   sub_cls_map = {
   }
   
   bfmt = '>L'
   bfmt_len = struct.calcsize(bfmt)
   def _get_subel_off(self):
      return (super()._get_subel_off() + self.bfmt_len)
   
   def _init2(self):
      if (self.c._track_type is None):
         raise MovParserError('No track type information available.')
      try:
         self.sub_cls_default = self.sub_cls_map[self.c._track_type]
      except KeyError as exc:
         self.sub_cls_default = MovSampleEntryMpeg
         #raise MovParserError('Unsupported track type {0}.'.format(FourCC(self.c._track_type))) from exc
      
      super()._init2()
      (elcount,) = struct.unpack(self.bfmt, self.get_body()[:self.bfmt_len])
      if (len(self.sub) != elcount):
         raise MovParserError()

def _mov_sample_entry_type_reg(cls):
   MovBoxSampleDescription.sub_cls_map[cls.track_type] = cls
   return cls

@_mov_sample_entry_type_reg
class MovSampleEntryVideo(MovSampleEntry):
   track_type = FourCC(b'vide')
   bfmt2 = '>16xHHLL4xHB31sH2x'
   bfmt2_len = struct.calcsize(bfmt2)
   _B2CID = {
      FourCC('SVQ3'): CODEC_ID_SVQ3,
      FourCC('png '): CODEC_ID_PNG
   }
   def _init2(self):
      super()._init2()
      (self.width, self.height, self.res_h, self.res_v, self.frame_count, cname_len, cname, self.depth
      ) = struct.unpack(self.bfmt2, self.get_body()[:self.bfmt2_len])
      
      if (cname_len > 31):
         raise ParserError('Invalid compressor name length {0}.'.format(cname_len))
      cname = cname[:cname_len]
      self.cname = cname
      self.hlen += self.bfmt2_len
      MovBoxBranch._init2(self)
   
   def _format_f(self, fs):
      return '<{0} type: {1} dri: {2} cname: {3} dim: {4}x{5} depth: {6}>'.format(type(self).__name__, self.type, self.dri,
         self.cname, self.width, self.height, self.depth)

@_mov_box_type_reg
class MovSampleEntryVideo_AVC1(MovSampleEntryVideo):
   type = FourCC('avc1')
   def get_codec_init_data(self):
      """Return codec-specific initialization data, H264 variant."""
      return self.find_subbox('avcC').get_body()
   
   def get_codec(self):
      """Return ObjectTypeIndication, H264 variant."""
      return CODEC_ID_H264

@_mov_sample_entry_type_reg
class MovSampleEntrySound(MovSampleEntry):
   track_type = FourCC(b'soun')
   bfmt2 = '>8xHH4xL'
   bfmt2_len = struct.calcsize(bfmt2)
   _B2CID = {
      FourCC('.mp3'): CODEC_ID_MP3,
      FourCC('mp4a'): CODEC_ID_AAC
   }
   
   def _init2(self):
      super()._init2()
      (self.channel_count, self.sample_size, self.sample_rate) = struct.unpack(self.bfmt2, self.get_body()[:self.bfmt2_len])
      self.sample_rate /= 65536
      self.hlen += self.bfmt2_len
      MovBoxBranch._init2(self)
      
   def _format_f(self, fs):
      return '<{0} type: {1} dri: {2} channels: {3} sample size: {4} sample rate: {5}>'.format(type(self).__name__, self.type,
         self.dri, self.channel_count, self.sample_size, self.sample_rate)

@_mov_box_type_reg
class MovSampleEntryMpeg(MovSampleEntry):
   type = FourCC('mp4s')
   def _init2(self):
      super()._init2()
      self.sub = []
      MovBoxBranch._init2(self)

class _CPData:
   def __init__(self, data, off=0):
      self.data = data
      self.off = off
   
   def get_byte(self):
      rv = struct.unpack('>B', self.data[self.off:self.off+1])[0]
      self.off += 1
      return rv
   
   def get_length(self):
      rv = 0
      for i in range(4):
         d = self.get_byte()
         rv <<= 7
         rv |= (d & 127)
         if not (d & 128):
            break
      return rv
   
   def unpack(self, bfmt):
      blen = struct.calcsize(bfmt)
      rv = struct.unpack(bfmt, self.data[self.off:self.off+blen])
      self.off += blen
      return rv
   
   def len_remainder(self):
      return len(self.data)-self.off

class _DecoderConfigDescriptor(collections.namedtuple('_dcdb', 'oti type bufsize br_max br_avg dsi')):
   bfmt = '>BLLL'
   @classmethod
   def build_from_bindata(cls, bd):
      (oti, data2, br_max, br_avg) = bd.unpack(cls.bfmt)
      bs = (data2 & 16777215)
      si = (data2 >> 25)
      stype = (si >> 1)
      
      if (bd.len_remainder()):
         dsi_tag = bd.get_byte()
         if (dsi_tag == 0x24): #DecSpecificInfoShortTag
            dsi_len = bd.get_byte()
         elif (dsi_tag == 0xE0): #DecSpecificInfoLargeTag
            (dsi_len,) = bd.unpack('>L')
         elif (dsi_tag == 0x05):
            # This isn't defined in ISO/IEC 14496-1 ... but empirical tests on files in the wild indicate this is probably
            # correct.
            dsi_len = bd.get_length()
         else:
            raise ValueError('Unknown tag {0} for DSI section.'.format(dsi_tag))
      
         if (dsi_len != bd.len_remainder()):
            raise ValueError('DecoderSpecificInfo section length (dsi_tag {0}) mismatch; read length value {1}, while container indicates a length of {2}.'.format(dsi_tag, dsi_len, bd.len_remainder()))
      
         dsi = bd.data[bd.off:]
      else:
         dsi = None
      
      return cls(oti, stype, bs, br_max, br_avg, dsi)

@_mov_box_type_reg
class MovBoxCodecPrivate_EsDescriptor(MovFullBox):
   type = FourCC('esds')
   bfmt = '>HB'
   
   def get_dsi(self):
      if (self.dcd_data):
         return self.dcd_data[0].dsi
      return None
   
   def get_oti(self):
      if (self.dcd_data):
         return self.dcd_data[0].oti
      return None
   
   def _init2(self):
      super()._init2()
      bd = _CPData(self.get_body())
      self.dcd_data = []
      
      tag = bd.get_byte()
      length = bd.get_length()
      
      if (tag != 3):
         raise ValueError('Unexpected ES tag value {0}.'.format(tag))
      if (length != bd.len_remainder()):
         raise ValueError('Unexpected ES body len {0}; expected {1}.'.format(length,len(bd)-2))
      
      (es_id, flags) = bd.unpack(self.bfmt)
      
      # DecoderConfigDescriptor parsing
      while (bd.len_remainder() > 0):
         tag = bd.get_byte()
         length = bd.get_length()
         data = _CPData(bd.data[bd.off:bd.off+length])
         bd.off += length
         if (tag == 4):
            self.dcd_data.append(_DecoderConfigDescriptor.build_from_bindata(data))
      
      if (bd.len_remainder()):
         raise ValueError('Failed to parse ESDS body data {0} correctly: length {{over,under}}run.'.format(bd))
   
   def _format_f(self, fs):
      return '<{0} dcd: {1}>'.format(type(self).__name__, self.dcd_data)
      

@_mov_box_type_reg
class MovBoxMediaHeader(MovFullBox):
   type = FourCC('mdhd')
   bfmts = {
      0: '>LLLLH2x',
      1: '>QQLQH2x'
   }
   
   def _init2(self):
      super()._init2()
      (ts_creat, ts_mod, self.time_scale, self.dur, lc_raw) = struct.unpack(self._get_bfmt(), self.get_body())
      
      self.ts_creat = movts2unixtime(ts_creat)
      self.ts_mod = movts2unixtime(ts_mod)
      self.lc = self._lc_parse(lc_raw)
   
   @staticmethod
   def _lc_parse(lc_raw):
      """Parse an 16bit MP4 mdhd language code integer to a string."""
      # Someone must have felt really clever for saving a single friggin' byte per stream in a media container container format
      # by coming up with this ridiculous and overcomplicated encoding scheme. Ugh. Talk about premature optimization.
      b = bytearray(3)
      (r, b[2]) = divmod(lc_raw, 32)
      (r, b[1]) = divmod(r, 32)
      (r, b[0]) = divmod(r, 32)
      if (r):
         raise ValueError('Top lc bit not zero.')
      b[0] += 0x60
      b[1] += 0x60
      b[2] += 0x60
      return b.decode('ascii')
   
   def _format_f(self, s):
      dt_creat = datetime.datetime.fromtimestamp(self.ts_creat)
      dt_mod = datetime.datetime.fromtimestamp(self.ts_mod)
      return '<{0} type: {1} time_scale: {2} ts_creat: {3} ts_mod: {4} dur: {5} lc: {6!a}>'.format(type(self).__name__, self.type,
         self.time_scale, dt_creat, dt_mod, self.get_dur(), self.lc)
   
   def get_dur(self):
      return self.dur/self.time_scale

@_mov_box_type_reg
class MovBoxTTS(MovBoxSampleTableRepeats):
   type = FourCC('stts')
   bfmt_entry = '>LL'
   def time2sample(self, dt):
      for (count, dur) in self.data:
         if (count > dt):
            return dur
         dt -= count
      raise ValueError('Invalid time value {0}.'.format(dt))
      

@_mov_box_type_reg
class MovBoxSyncSample(MovBoxSampleTableSimple):
   type = FourCC('stss')
   bfmt_entry = '>L'

@_mov_box_type_reg
class MovBoxSampleToChunk(MovBoxSampleTableBase):
   type = FourCC('stsc')
   bfmt_entry = '>LLL'
   def _init2(self):
      super()._init2()
      fc_l = 1
      
      ed_pp = []
      for (fc, spc, sdi) in self.entry_data:
         if (fc > fc_l):
            ed_pp.append((fc - fc_l, spc_l))
            
         (fc_l, spc_l) = (fc, spc)
      
      ed_pp.append((None, spc))
      self.entry_data_pp = ed_pp

@_mov_box_type_reg
class MovBoxSampleSize(MovBoxSampleTableSimple):
   type = FourCC('stsz')
   bfmt = '>LL'
   bfmt_len = struct.calcsize(bfmt)
   bfmt_entry = '>L'
   
   def _parse_header(self):
      data = memoryview(self.get_body())
      (ss, elnum) = struct.unpack(self.bfmt, data[:self.bfmt_len])
      self._elnum = elnum
      if (ss == 0):
         ss = None
      self.sample_size = ss
   
   def _data_table_present(self):
      return (self.sample_size is None)
   
   def get_ss_count(self):
      return self._elnum
   
   def get_ss(self, i):
      return (self.sample_size or self.entry_data[i])


class MovBoxChunkOffset(MovBoxSampleTableBase):
   def get_co(self, i):
      return self.entry_data[i]

@_mov_box_type_reg
class MovBoxChunkOffset32(MovBoxSampleTableSimple):
   type = FourCC('stco')
   bfmt_entry = '>L'

@_mov_box_type_reg
class MovBoxChunkOffset64(MovBoxSampleTableSimple):
   type = FourCC('co64')
   bfmt_entry = '>Q'

@_mov_box_type_reg
class MovBoxCompositionTimeToSample(MovBoxSampleTableRepeats):
   type = FourCC('ctts')
   bfmt_entry = '>LL'

@_mov_box_type_reg
class MovBoxMovie(MovBoxBranch):
   type = FourCC(b'moov')   
   _HTYPE_SOUN = FourCC(b'soun')
   _HTYPE_VIDE = FourCC(b'vide')
   
   def make_mkvb(self):
      import mcio_matroska
      from mcio_matroska import MatroskaBuilder
      
      tracks = self.find_subboxes('trak')
      td_gcd = reduce(gcd, (t.get_sample_delta_gcd() for t in tracks))
      ts_base = max(t.get_mdhd().time_scale for t in tracks)
      dur = max(t.get_mdhd().get_dur() for t in tracks)
      
      mvhd = self.find_subbox('mvhd')
      dur = max(dur, mvhd.get_dur())
      (tcs, elmult, _tcs_err) = MatroskaBuilder.tcs_from_secdiv(ts_base, td_gcd)
      mb = MatroskaBuilder(tcs, dur)
      
      for track in tracks:
         mdhd = track.get_mdhd()
         se = track.get_sample_entry()
         
         htype = track.find_subbox(b'mdia').find_subbox(b'hdlr').handler_type
         if (htype == self._HTYPE_VIDE):
            ttype = mcio_matroska.TRACKTYPE_VIDEO
            at_args = (se.width, se.height)
         elif (htype == self._HTYPE_SOUN):
            ttype = mcio_matroska.TRACKTYPE_AUDIO
            at_args = (round(se.sample_rate), se.channel_count)
         else:
            continue
         
         codec_id = se.get_codec()
         ts_fact = (ts_base / mdhd.time_scale)
         mcd = track._get_most_common_dur()
         mb.add_track(track.get_sample_data(elmult*ts_fact, mcd), ttype, codec_id, se.get_codec_init_data(),
            not (track.stts is None), default_dur=round(10**9*mcd/mdhd.time_scale), *at_args)
      
      return mb

@_mov_box_type_reg
class MovBoxUserdata(MovBoxBranch):
   type = FourCC(b'udta')

@_mov_box_type_reg
class MovBoxTrack(MovBoxBranch):
   type = FourCC(b'trak')
   def _init2(self):
      super()._init2()
      stbl = self.find_subbox(b'mdia').find_subbox(b'minf').find_subbox(b'stbl')
      self.stbl = stbl
      self.stts = stbl.find_subbox(b'stts')
      self.stsd = stbl.find_subbox(b'stsd')
      self.stsc = stbl.find_subbox(b'stsc')
      self.stsz = stbl.find_subbox(b'stsz')
      for name in (b'stss', b'stco', b'co64', b'edts', b'ctts'):
         try:
            table = stbl.find_subbox(name)
         except SubboxNotFoundError:
            table = None
         setattr(self, name.decode('ascii'), table)
   
   def get_sample_entry(self):
      for box in self.stsd.sub:
         if isinstance(box, MovSampleEntry):
            return box
   
   def dump_media_data(self, out):
      for (timeval, dur, data_ref, sync) in self.get_sample_data(1):
         block = data_ref.get_data()
         if (len(block) != data_ref.get_size()):
            raise MovParserError('Unable to read {0} bytes from offset {1} from {2}.'.format(sz, off, self.f))
         out(block)
   
   def get_mdhd(self):
      return self.find_subbox('mdia').find_subbox('mdhd')
   
   def get_sample_delta_gcd(self):
      return reduce(gcd, (e[1] for e in self.stts.entry_data))
   
   def sample_durations(self):
      tsi = self.stts.__iter__()
      if (self.ctts is None):
         for td in tsi:
            yield td
         return
      
      coi = self.ctts.__iter__()
         
      ts = 0
      ts_l = []
      for (td, td_off) in zip(tsi, coi):
         ts += td
         ts_l.append(ts + td_off)
      
      ts_l.sort()
      ts_prev = 0
      for ts in ts_l:
         yield (ts-ts_prev)
         ts_prev = ts
   
   def _get_most_common_dur(self):
      from collections import defaultdict
      dur_freqs = defaultdict(lambda: 0)
      for dur in self.sample_durations():
         dur_freqs[dur] += 1
      
      return max((val,key) for (key, val) in dur_freqs.items())[1]
   
   def get_sample_data(self, time_mult, default_dur=None):
      if not (self.edts is None):
         raise MovParserError('EDTS support is currently unimplemented.')
      
      get_sz = self.stsz.get_ss
      sc = self.stsc.entry_data_pp
      co = self.stco.entry_data
      sduri = self.sample_durations()
      
      if (self.stss is None):
         ss = None
      else:
         ss = self.stss.entry_data
         ss_i = 0
      
      tsi = self.stts.__iter__()
      if (self.ctts is None):
         coi = None
      else:
         base_co = self.ctts.__iter__().__next__()
         coi = self.ctts.__iter__()
      
      s = 0
      s_lim = self.stsz.get_ss_count()
      s_sublim = 0
      
      c = 0
      c_lim = 0
      cblock = 0
      
      timeval = 0
      while (s < s_lim):
         if (s >= s_sublim):
            while ((not (c_lim is None)) and (c >= c_lim)):
               (cnum, spc) = sc[cblock]
               if (cnum is None):
                  c_lim = None
               else:
                  c_lim += cnum
               cblock += 1
            s_sublim += spc
            s_off = co[c]
            c += 1
         
         if (ss is None):
            is_sync = True
         elif ((ss_i < len(ss)) and (s == ss[ss_i]-1)):
            is_sync = True
            ss_i += 1
         else:
            is_sync = False
         
         timedelta = tsi.__next__()
         
         tv_d = timeval
         if not (coi is None):
            tv_d += coi.__next__()-base_co
         
         dur = sduri.__next__()
         if (dur == default_dur):
            dur = None
         else:
            dur = round(dur*time_mult)
         
         size = get_sz(s)
         yield ((round(tv_d*time_mult), dur, DataRefFile(self.c.f, s_off, size), is_sync))
         s_off += size
         s += 1
         timeval += timedelta
      

@_mov_box_type_reg
class MovBoxMedia(MovBoxBranch):
   type = FourCC(b'mdia')
   def _init2(self):
      self.c._track_type = None
      super()._init2()
      self.c._track_type = None

@_mov_box_type_reg
class MovBoxMeta(MovFullBoxBranch):
   type = FourCC(b'meta')

@_mov_box_type_reg
class MovBoxMeta(MovBoxBranch):
   type = FourCC(b'ilst')

@_mov_box_type_reg
class MovBoxHandlerReference(MovFullBox):
   type = FourCC(b'hdlr')
   bfmt = '>LL12x'
   bfmt_len = struct.calcsize(bfmt)
   def _init2(self):
      super()._init2()
      data = self.get_body()
      (pdef, self.handler_type) = struct.unpack(self.bfmt, data[:self.bfmt_len])
      name = data[self.bfmt_len:]
      try:
         name = name[:name.index(b'\x00')]
      except ValueError:
         pass
      
      self.name = name
      # HACK: Work around files that have a hdlr box below minf, as well as directly in mdia; the former is used to set a
      # handler data type for alis information, as opposed to the stream itself.
      # It'd be cleaner to look at the precise part of the structure where this occurs, instead of assuming that the first
      # element of this type is the right one to use for stream handler identification.
      if (self.c._track_type is None):
         self.c._track_type = self.handler_type
   
   def _format_f(self, fs):
      return '<{0} htype: {1} name: {2}>'.format(type(self).__name__, FourCC(self.handler_type), self.name)
         

@_mov_box_type_reg
class MovBoxMediaInformation(MovBoxBranch):
   type = FourCC(b'minf')

@_mov_box_type_reg
class MovBoxDataInformation(MovBoxBranch):
   type = FourCC(b'dinf')

@_mov_box_type_reg
class MovBoxDataReference(MovBoxBranch):
   type = FourCC(b'dref')
   def _init2(self):
      self.c.f.seek(self.offset + self.hlen + 4 + 4)
      self.sub = MovBox.build_seq_from_ctx(self.c, self.offset + self.size)

@_mov_box_type_reg
class MovBoxSampleTable(MovBoxBranch):
   type = FourCC(b'stbl')

@_mov_box_type_reg
class MovBoxTrackHeader(MovFullBox):
   type = FourCC(b'tkhd')
   bfmts = {
      0: '>LLL4xL8xhhhxx36sLL',
      1: '>QQL4xQ8xhhhxx36sLL'
   }
   
   def _init2(self):
      super()._init2()
      (ts_creat, ts_mod, self.tid, self.dur, self.layer, self.altgroup, self.vol, self.mat, self.width, self.height
      ) = struct.unpack(self._get_bfmt(), self.get_body())
      
      self.width /= 65536
      self.height /= 65536
      
      self.ts_creat = movts2unixtime(ts_creat)
      self.ts_mod = movts2unixtime(ts_mod)
   
   def _format_f(self, fs):
      dt_mod = datetime.datetime.fromtimestamp(self.ts_mod)
      return '<{0} dur: {1} mod_ts: {2} width: {3} height: {4}>'.format(type(self).__name__, self.dur, dt_mod, self.width,
         self.height)

@_mov_box_type_reg
class MovBoxEditList(MovBoxBranch):
   type = FourCC(b'edts')


def _dump_atoms(seq, depth=0):
   for atom in seq:
      print('{0}{1:f}'.format(' '*depth, atom))
      
      if (hasattr(atom, 'sub')):
         _dump_atoms(atom.sub, depth+1)
   
def make_mkvb_from_file(f, *args, **kwargs):
   boxes = MovBox.build_seq_from_file(f, *args, **kwargs)
   for box in boxes:
      if isinstance(box, MovBoxMovie):
         break
   else:
      raise ValueError('No movie box in MP4 file; got: {0!a}.'.format(boxes))
   
   return box.make_mkvb()

def main():
   import sys
   fn = sys.argv[1]
   f = open(fn, 'rb')
   
   ot = ('-t' in sys.argv[2:])
   
   boxes = MovBox.build_seq_from_file(f, tolerate_overrun_elements=ot)
   _dump_atoms(boxes)
   
   f.seek(0)
   mb = make_mkvb_from_file(f, tolerate_overrun_elements=ot)
   mb.set_writingapp('mcde_mp4 selftester')
   mb.sort_tracks()
   mb.write_to_file(open(b'__mp4dump.mkv.tmp', 'wb'))

if (__name__ == '__main__'):
   main()

