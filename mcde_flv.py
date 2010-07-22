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

# Media container data extraction: FLV

import struct
from collections import namedtuple

from mcio_base import *
from mcio_codecs import *

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
      self.sample_size = sample_size
      self.sample_freq = rate
      self.channels = channels
      self.aac_pt = aac_pt

   def check_stream_consistency(self, other):
      return ((self.codec == other.codec) and (self.sample_freq == other.sample_freq) and (self.channels == other.channels))

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


class BitIterator:
   def __init__(self, s):
      self._si = iter(s)
      self._cb = None
      self._i = 0
      self.bits_read = 0
   
   def __iter__(self):
      return self
   
   def __next__(self):
      self._i -= 1
      if (self._i < 0):
         self._cb = self._si.__next__()[0]
         self._i = 7
      
      self.bits_read += 1
      return (self._cb & (1 << self._i)) >> self._i
   
   def read_bool(self):
      return bool(self.read_u(1))
   
   def read_u(self, l):
      rv = 0
      for i in range(l):
         rv <<= 1
         rv += self.__next__()
      return rv
   
   def read_ue(self):
      dbits = 0
      for bit in self:
         if (bit != 0):
            break
         dbits += 1
         
      rv = self.read_u(dbits) + (1 << dbits) - 1
      return rv
   
   def read_se(self):
      from math import ceil
      rv = self.read_ue()
      rv = ceil(rv/2)
      if (rv % 2 == 0):
         rv *= -1
      return rv


class H264InitData:
   def __init__(self, data):
      self.data = memoryview(data)
      if (b'\x00\x00\x03' in data):
         FLVParserError('Emulation prevention byte filtering is currently unsupported.')
      self.sps_map = {}
      self.pps_map = {}
      self._parse_data()

   def _parse_data(self):
      off = 6
      (config_ver, profile, level, nls, sps_c) = struct.unpack('BBxBBB', self.data[:off])
      if (config_ver != 1):
         raise FLVParserError('Incompatible config version {0}.'.format(config_ver))
      
      self.nls = (nls & 3) + 1
      sps_c &= 31
      
      for i in range(sps_c):
         (sps_l,) = struct.unpack('>H', self.data[off:off+2])
         off += 2
         sps = H264NALU(self.data[off:off+sps_l], None).read_body()
         self.sps_map[sps.id] = sps
         off += sps_l
      
      (pps_c,) = struct.unpack('>B', self.data[off:off+1])
      off += 1
      for i in range(pps_c):
         (pps_l,) = struct.unpack('>H', self.data[off:off+2])
         off += 2
         pps = H264NALU(self.data[off:off+sps_l], None).read_body()
         self.pps_map[pps.id] = pps
         off += pps_l
      
      if (len(self.data) != off):
         raise FLVParserError('Config parsing failed; unexpected additional trailing data {0}.'.format(bytes(self.data[off:])))

class H264NALU:
   BODY_HANDLERS = {}
   SPS_T = namedtuple('SPS', 'id profile_idc level_idc flags seperate_colour_plane chroma_fmt_idc frame_num_bits pic_order_cnt_type max_pic_order_cnt_lsb_bits delta_pic_order_always_zero max_num_ref_frames frame_mbs_only')
   PPS_T = namedtuple('PPS', 'id sps_id bottom_field_pic_order_in_frame_present weighted_pred weighted_bipred_idc')
   
   SLICE_P = 0
   SLICE_B = 1
   SLICE_I = 2
   SLICE_SP = 3
   SLICE_SI = 4
   
   def _rbh(t, _dbh=BODY_HANDLERS):
      def rv(f):
         _dbh[t] = f
         return f
         
      return rv
   
   def __init__(self, data, initdata):
      if (b'\x00\x00\x03' in data):
         FLVParserError('Emulation prevention byte filtering is currently unsupported.')         
      
      nal_b1 = data[0]
      if (isinstance(nal_b1, bytes)):
         nal_b1 = nal_b1[0]
      
      self.type = nal_b1 & 31
      self.ref_idc = (nal_b1 & 96) >> 5
      header_len = 1
      
      if (nal_b1 >> 7):
         raise FLVParserError('NAL unit init sequence mismatch.')
      
      if (self.ref_idc == 0):
         if (self.type == 5):
            raise FLVParserError("Invalid ref_idc {0} for nal unit type {1}.".format(self.ref_idc, self.type))
      elif (self.type in (6,9,10,11,12)):
         raise FLVParserError("Invalid ref_idc {0} for nal unit type {1}.".format(self.ref_idc, self.type))
      
      if (self.type in (14, 20)):
         header_len += 3
      
      self.initdata = initdata
      self.data = data
      self.header_len = header_len
   
   def get_body(self):
      return self.data[self.header_len:]
   
   def _get_sps(self, _id):
      return self.initdata.sps_map[_id]

   def _get_psps(self, _id):
      pps = self.initdata.pps_map[_id]
      sps = self._get_sps(pps.sps_id)
      return (pps, sps)

   def read_body(self):
      return self.BODY_HANDLERS[self.type](self)
   
   @_rbh(1)
   @_rbh(5)
   def _rb_slice_without_part(self):
      bd = self.get_body()
      bits = BitIterator(bd)
      fmb = bits.read_ue()
      s_type = bits.read_ue()
      
      (pps, sps) = self._get_psps(bits.read_ue())
      
      if (sps.seperate_colour_plane):
         colour_plane_id = bits.read_u(2)
         chroma_array_type = sps.chroma_fmt_idc
      else:
         chroma_array_type = 0
      
      frame_num = bits.read_u(sps.frame_num_bits)
      
      bottom_field = False
      if (not sps.frame_mbs_only):
         field_pic = bits.read_bool()
         if (field_pic):
            bottom_field = bits.read_bool()
      else:
         field_pic = False
      
      if (self.type == 5):
         idr_pic_id = bits.read_ue()
      
      delta_pic_order_cnt_bottom = 0
      if (sps.pic_order_cnt_type == 0):
         pic_order_cnt_lsb = bits.read_u(sps.max_pic_order_cnt_lsb_bits)
         if (pps.bottom_field_pic_order_in_frame_present and (not field_pic)):
            delta_pic_order_cnt_bottom = bits.read_ue()
      elif (sps.pic_order_cnt_type == 1):
         if (not sps.delta_pic_order_always_zero):
            dpo_cnt0 = bits.read_ue()
            if (pps.bottom_field_pic_order_in_frame_present and (not field_pic)):
               dpo_cnt1 = bits.read_ue()
      
      s_typeb = s_type % 5
      if (s_typeb == self.SLICE_B):
         direct_spatial_mv_pred = bits.read_bool()
      if (s_typeb in (self.SLICE_B, self.SLICE_SP, self.SLICE_P)):
         num_ref_idx_active_override = bits.read_bool()
         if (num_ref_idx_active_override):
            num_ref_idx_l0_active = bits.read_ue() + 1
            if (s_typeb == self.SLICE_B):
               num_ref_idx_l1_active = bits.read_ue() + 1
      
      if (self.type == 20):
         raise FLVParserError('Type 20 NALUs currently unsupported.')
      else:
         # ref_pic_list_modification
         if not (s_typeb in (2, 4)):
            ref_pic_list_modification = bits.read_bool()
            if (ref_pic_list_modification):
               modification_of_pic_nums_idc = None
               while (modification_of_pic_nums_idc != 3):
                  modification_of_pic_nums_idc = bits.read_ue()
                  if (modification_of_pic_nums_idc in (0,1)):
                     abs_diff_pic_num = bits.read_ue() - 1
                     continue
                  if (modification_of_pic_nums_idc == 2):
                     long_term_pic_num = bits.read_ue()
      
      if ((pps.weighted_pred and (s_typeb in (self.SLICE_P, self.SLICE_B))) or
         ((pps.weighted_bipred_idc == 1) and (s_typeb == self.SLICE_B))):
         # pred_weight_table
         luma_log2_weight_denom = bits.read_ue()
         if (chroma_array_type != 0):
            chroma_log2_weight_denom = bits.read_ue()
         raise FLVParserError('pred_weight_table parsing currently unsupported.')
         #for i in range(num_ref_idx_l0_active):
            #_luma_weight_l0 = bits.read_bool()
            #if (_luma_weight_l0):
               #bits.read_se()
               #bits.read_se()
      
      mmc_ops = []
      if (self.ref_idc):
         # dec_ref_pic_marking
         if (self.type == 5):
            no_output_of_prior_pics = bits.read_bool()
            long_term_reference = bits.read_bool()
         else:
            adaptive_ref_pic_marking = bits.read_bool()
            if (adaptive_ref_pic_marking):
               mmc_op = None
               while(mmc_op != 0):
                  mmc_op = bits.read_ue()
                  if (mmc_op in (1,3)):
                     difference_of_pic_nums = bits.read_ue() + 1
                  if (mmc_op == 2):
                     long_term_pic_num = bits.read_ue()
                  if (mmc_op in (3,6)):
                     long_term_frame_idx = bits.read_ue()
                  if (mmc_op == 4):
                     max_long_term_frame_idx = bits.read_ue()-1
                  mmc_ops.append(mmc_op)
      
      #print(self.type, fmb, s_type, frame_num, pic_order_cnt_lsb, mmc_ops, bottom_field, end=', ')
      return (s_type % 5, pps, sps, pic_order_cnt_lsb, frame_num, mmc_ops, field_pic, bottom_field, delta_pic_order_cnt_bottom)
   
   def compute_po(self, prev):
      (s_type, pps, sps, poc_lsb, frame_num, mmc_ops, field_pic, bottom_field, delta_pic_order_cnt_bottom) = self.read_body()
      poc_t = sps.pic_order_cnt_type
      
      if (poc_t == 0):
         self.mmc_ops = mmc_ops
         if (self.type == 5):
            self.poc_msb = 0
            self.poc_lsb = 0
            return
      
         max_pic_order_cnt_lsb = (1 << sps.max_pic_order_cnt_lsb_bits)
      
         if (5 in prev.mmc_ops):
            raise FLVParserError('Accounting for MMC Op 5 is currently unimplemented.')
         else:
            p_poc_msb = prev.poc_msb
            p_poc_lsb = prev.poc_lsb
      
         if ((poc_lsb < p_poc_lsb) and (p_poc_lsb - poc_lsb >= max_pic_order_cnt_lsb/2)):
            poc_msb = p_poc_msb + max_pic_order_cnt_lsb
         elif ((poc_lsb > p_poc_lsb) and (poc_lsb - p_poc_lsb > max_pic_order_cnt_lsb/2)):
            poc_msb = p_poc_msb - max_pic_order_cnt_lsb
         else:
            poc_msb = p_poc_msb
         
         if (bottom_field):
            tf_oc = None
            bf_oc = poc_msb + poc_lsb
         else:
            tf_oc = poc_msb + poc_lsb
            if (not field_pic):
               bf_oc = tf_oc + delta_pic_order_cnt_bottom
            else:
               bf_oc = None
         
         self.poc_msb = poc_msb
         self.poc_lsb = poc_lsb
         #print(frame_num, s_type, poc_msb, poc_lsb, tf_oc, bf_oc)
      else:
         raise FLVParserError('Pic order cnt type {0} interpretation unimplemented.'.format(poc_t))

   @_rbh(8)
   def _rb_pps(self):
      data = self.get_body()
      bits = BitIterator(data)
      
      pps_id = bits.read_ue()
      sps_id = bits.read_ue()
      ecm = bits.read_u(1)
      bottom_field_pic_order_in_frame_present = bool(bits.read_u(1))
      sg_c = bits.read_ue() + 1
      if (sg_c > 1):
         sg_map_type = bits.read_ue()
         if (sg_map_type == 0):
            run_length = [bits.read_ue() for i in range(sg_c)]
         elif (sg_map_type == 2):
            tl_br = [(bits.read_ue(),bits.read_ue()) for i in range(sg_c)]
         elif (sg_map_type in (3,4,5)):
            sg_change_direction = bits.read_bool()
            sg_change_rate = bits.read_ue() + 1
         elif (sg_map_type == 6):
            pic_size_in_map_units = bits.read_ue() + 1
            sgi_len = (sg_c-1).bit_length()
            slice_group_id = [bits.read_u(sgi_len) for i in range(pic_size_in_map_units)]
      
      num_ref_idx_l0_default_active = bits.read_ue() + 1
      num_ref_idx_l1_default_active = bits.read_ue() + 1
      weighted_pred = bits.read_bool()
      weighted_bipred_idc = bits.read_u(2)
      
      return self.PPS_T(pps_id, sps_id, bottom_field_pic_order_in_frame_present, weighted_pred, weighted_bipred_idc)
   
   @_rbh(7)
   def _rb_sps(self):
      data = self.get_body()
      (profile_idc, flags, level_idc) = struct.unpack('>BBB',data[:3])
      if (flags & 3):
         raise FLVParserError("Invalid beginning for sps body.")
      
      bits = BitIterator(data[3:])
      sps_id = bits.read_ue()
      scpf = False
      if (profile_idc in (44, 83, 86, 100, 110, 118, 122, 128, 244)):
         chroma_fmt_idc = bits.read_ue()
         if (chroma_fmt_idc == 3):
            scpf = bool(bits.read_u(1))
         bd_luma = bits.read_ue() + 8
         bd_chroma = bits.read_ue() + 8
         qyztbf = bool(bits.read_u(1))
         ssmp = bool(bits.read_u(1))
         if (ssmp):
            if (chroma_fmt_idc == 3):
               ssmp_len = 12
            else:
               ssmp_len = 8
            bits.read_u(1)
      else:
         chroma_fmt_idc = None
         
      frame_num_bits = (bits.read_ue() + 4)
      pic_order_cnt_type = bits.read_ue()
      
      max_pic_order_cnt_lsb_bits = None
      delta_pic_order_a0 = None
      if (pic_order_cnt_type == 0):
         max_pic_order_cnt_lsb_bits = (bits.read_ue() + 4)
      elif (pic_order_cnt_type == 1):
         delta_pic_order_a0 = bool(bits.read_u(1))
         nrp_off = bits.read_se()
         t2b_off = bits.read_se()
         nrf = bits.read_ue()
         for i in range(nrf):
            bits.read_se()
      
      max_num_ref_frames = bits.read_ue()
      gaps_in_frame_num_value_allowed_flag = bool(bits.read_u(1))
      width_mbs = bits.read_ue() + 1
      height_mbs = bits.read_ue() + 1
      frame_mbs_only_flag = bool(bits.read_u(1))
      
      return self.SPS_T(sps_id, profile_idc, level_idc, flags, scpf, chroma_fmt_idc, frame_num_bits,
         pic_order_cnt_type, max_pic_order_cnt_lsb_bits, delta_pic_order_a0, max_num_ref_frames, frame_mbs_only_flag)

   del(_rbh)
      

def _h264_nalu_seq_read(data_r, initdata):
   length_size = initdata.nls
   data = memoryview(data_r.get_data())
   lbuf = bytearray(4)
   rv = []
   while (data):
      lbuf[-length_size:] = data[:length_size]
      (body_size,) = struct.unpack('>L', lbuf)
      nalu = H264NALU(data[length_size:length_size+body_size], initdata)
      
      skip = body_size + length_size
      if (skip > len(data)):
         raise FLVParserError('Element boundary overrun.')
      
      data = data[skip:]
      rv.append(nalu)
   return rv


class FLVReader:
   bfmt_file_header = '>3sBBL'
   bfmt_file_header_len = struct.calcsize(bfmt_file_header)
   bfmt_tag_header = '>B3s3sB3x'
   bfmt_tag_header_len = struct.calcsize(bfmt_tag_header)
   bfmt_tag_header2 = '>LL'
   
   tagtype_cls_map = {}
   
   CODEC2ID_V = {
      2: CODEC_ID_FLV1,
      3: CODEC_ID_FLASHSV,
      4: CODEC_ID_VP6,
      5: CODEC_ID_VP6A,
      7: CODEC_ID_MPEG4_10
   }
   
   CODEC2ID_A = {
       2: CODEC_ID_MP3,
      10: CODEC_ID_AAC,
      11: CODEC_ID_SPEEX,
      14: CODEC_ID_MP3
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
   
   def make_mkvb(self):
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
      
      prev_vn = None
      #vfbuf = []
      
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
               d['sfreq'] = tag.sample_freq
               d['channel_count'] = tag.channels
               
         else:
            if not (tag_last.check_stream_consistency(tag)):
               raise FLVParserError('Stream metadata inconsistency between {0} and {1}.'.format(tag_prev, tag))
         
         if (tag.is_header()):
            d['init_data'] = tag.data_r.get_data()
         else:
            d['data'].append(tag)
            
         #if ((tag.type == 9) and (tag.codec == 7)):
            ## H264 data ... try reordering.
            #if (tag.is_header()):
               #vid_id = H264InitData(d['init_data'])
            #else:
               #(nalu,) = _h264_nalu_seq_read(tag.data_r, vid_id)
               #nalu.compute_po(prev_vn)
               #prev_vn = nalu
               
               #if (tag.is_keyframe):
                  #self._v_reorder(vfbuf)
                  #del(vfbuf[:])
               #vfbuf.append(tag)
               
      mb = MatroskaBuilder(1000000, md['duration'])
      
      try:
         vc_id = self.CODEC2ID_V[vd['codec']]
      except KeyError:
         raise FLVParserError('Unknown video codec {0}.'.format(vd['codec']))
         
      width = md['width']
      height = md['height']
      if not (width is None):
         width = int(width)
      if not (height is None):
         height = int(height)
      mb.add_track((t.get_framedata() for t in vd['data']), mcio_matroska.TRACKTYPE_VIDEO, vc_id, vd['init_data'], True,
         width, height)
         
      try:
         ac_id = self.CODEC2ID_A[ad['codec']]
      except KeyError:
         raise FLVParserError('Unknown audio codec {0}.'.format(ad['codec']))
      
      mb.add_track((t.get_framedata() for t in ad['data']), mcio_matroska.TRACKTYPE_AUDIO, ac_id, ad['init_data'], False,
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
         

def make_mkvb_from_file(f):
   flvr = FLVReader(f)
   flvr.parse_header()
   return flvr.make_mkvb()

def _main():
   import sys
   fn = sys.argv[1]
   mb = make_mkvb_from_file(open(fn, 'rb'))
   mb.set_writingapp('mcde_flv selftester pre-versioning version')
   mb.write_to_file(open(b'__flvdump.mkv.tmp', 'wb'))
   

if (__name__ == '__main__'):
   _main()
