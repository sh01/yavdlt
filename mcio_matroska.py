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

# Media container I/O: Matroska format

from collections import deque
from copy import deepcopy
import datetime
import math
import random
import struct
import time

from mcio_codecs import *
from mcio_base import *


class MatroskaBaseError(ContainerError):
   pass

class EBMLError(MatroskaBaseError):
   pass

class MatroskaError(MatroskaBaseError):
   pass

def _calc_vint_size(i, signed):
   payload_len = (i+1-signed).bit_length() + signed
   rv = 1
   while ((rv*8 - rv) < payload_len):
      rv += 1
   return rv

class EBMLVInt(int):
   lt_sbit = (None,) + tuple(int(math.log(i,2)) for i in range(1,2**8))
   lt_prefix_reserved = tuple(not bool(math.log(i+1,2) % 1) for i in range(0,2**8))
   SIGNED = False
   
   def __init__(self, x):
      self.size = _calc_vint_size(x, self.SIGNED)
   
   def get_bindata(self):
      """Return binary string representing this VInt."""
      rv = bytearray(self.size)
      (prefix_bytes, prefix_bits) = divmod(self.size-1, 8)
      
      databits = (8*len(rv) - 8*prefix_bytes - prefix_bits - 1)
      mult = 1 << (8*(len(rv)-prefix_bytes-1))
      val = int(self)
      if (self.SIGNED):
         val += 2**(databits-1)-1
      
      for i in range(prefix_bytes, len(rv)):
         rv[i] = val // mult
         val %= mult
         mult >>= 8
      
      rv[prefix_bytes] |= (1 << (7-prefix_bits))
      return rv
   
   def write_to_file(self, c):
      bd = self.get_bindata()
      return c.f.write(bd)
   
   def new(cls, *args, **kwargs):
      return cls(*args, **kwargs)
   
   @classmethod
   def build_from_bindata(cls, bd):
      bd = memoryview(bd)
      idx = 0
      l = 0
      while (bd[idx][0] == 0):
         l += 8
         idx += 1
      
      sbits = cls.lt_sbit[bd[idx][0]]
      prefix_val = bd[idx][0] & (2**sbits-1)
      l += 7-sbits
      
      bid = bd[idx+1:l+1]
      bd[idx+l][0]
      
      # Test for reserved val
      if ((cls.lt_prefix_reserved[bd[idx][0]]) and (bid == (b'\xFF'*l))):
         raise EBMLError('Reserved int values are currently unimplemented.')
      
      # Can't reverse it directly, since it might be a memoryview object
      mult = 1
      ival = 0
      for i in reversed(range(len(bid))):
         ival += bid[i][0]*mult
         mult <<= 8
      
      ival += prefix_val*mult
      if (cls.SIGNED):
         ival -= 2**(sbits+l*8-1)-1
      
      return (cls(ival), l+1)
   
   @classmethod
   def build_from_file(cls, f):
      bs = 8
      off = f.tell()
      while (True):
         data = f.read(bs)
         f.seek(off)
         try:
            rv = cls.build_from_bindata(data)
         except IndexError:
            if (len(data) != bs):
               raise
         else:
            return rv
         bs *= 2

class EBMLSVInt(EBMLVInt):
   SIGNED = True

class MatroskaVInt(EBMLVInt):
   # This is a direct consequence of matroska vints being limited to 8 bytes total length.
   val_lim = 2**56-2
   def __init__(self, x):
      super().__init__(x)
      if (x > self.val_lim):
         raise MatroskaError('VInt val {0} outside of defined domain.'.format(x))

class MatroskaSVInt(EBMLSVInt):
   val_lim = 2**48-1
   def __init__(self, x):
      super().__init__(x)
      if not (-1*self.val_lim <= x <= self.val_lim):
         raise MatroskaError('SVInt val {0} outside of defined domain.'.format(x))


# ---------------------------------------------------------------- EBML base tag types

class EBMLElement:
   def __format__(self, s):
      return '{0}({1})'.format(type(self).__name__,(self.type))
   
   def write_to_file(self, *args, **kwargs):
      raise EBMLError('Unrecognized element of type {0}; unable to dump.'.format(self.type))

   def _write_header(self, c, size):
      if not (isinstance(size, MatroskaVInt)):
         size = MatroskaVInt(size)
      return self.type.write_to_file(c) + size.write_to_file(c)

class EBMLElementUnknown(EBMLElement):
   def __init__(self, type, size, f):
      super().__init__()
      self.type = type
      self.data_r = DataRefFile(f, f.tell(), size)
   
   def __deepcopy__(self, mdict):
      self.data_r.f.seek(self.data_r.off)
      return type(self)(self.type, self.data_r.size, self.data_r.f)

   def get_size(self):
      bd_size = MatroskaVInt(self.data_r.size)
      return (self.type.size + bd_size.size + bd_size)

   @classmethod
   def _bff_curry(cls, etype):
      def rv(*args, **kwargs):
         return cls._build_from_file(etype, *args, **kwargs)
      return rv

   @classmethod
   def _build_from_file(cls, *args, **kwargs):
      return cls(*args, **kwargs)

   def write_to_file(self, c):
      hl = self._write_header(c, self.data_r.size)
      return (hl + c.f.write(self.data_r.get_data()))

class MatroskaElement(EBMLElement):
   @classmethod
   def new(cls, *args, **kwargs):
      return cls(*args, **kwargs)

class MatroskaElementMaster(MatroskaElement):
   __slots__ = ('type', 'sub')
   def __init__(self, sub):
      super().__init__()
      self.sub = sub

   def __deepcopy__(self, mdict):
      return type(self)(deepcopy(self.sub,mdict))

   @property
   def val(self):
      return self.sub

   def write_to_file(self, c):
      blen = sum(e.get_size() for e in self.sub)
      hlen = self._write_header(c, MatroskaVInt(blen))
      
      blen2 = sum(e.write_to_file(c) for e in self.sub)
      if (blen != blen2):
         raise MatroskaError('Output sanity check failed in {0}: Wrote {1} bytes, expected {2}.'.format(self, blen2, blen))
      
      return (blen + hlen)

   def get_size(self):
      bd_size = MatroskaVInt(sum(c.get_size() for c in self.sub))
      return (self.type.size + bd_size.size + bd_size)

   def get_sub_by_cls(self, cls):
      for e in self.sub:
         if (isinstance(e, cls)):
            return e
      
   def get_subval_by_cls(self, cls):
      for e in self.sub:
         if (isinstance(e, cls)):
            return e.val

   def set_sub(self, new_e):
      for i in range(len(self.sub)):
         e = self.sub[i]
         if (e.type == new_e.type):
            self.sub[i] = new_e
            break
      else:
         self.sub.append(new_e)
   
   def get_subl_by_cls(self, cls):
      for e in self.sub:
         if (isinstance(e, cls)):
            yield e
   
   def remove_subvals_by_cls(self, cls):
      self.sub = [e for e in self.sub if not (isinstance(e,cls))]
   
   @classmethod
   def _build_from_file(cls, body_size, f):
      sub = ebml_ns_mkv.build_seq_from_file(f, body_size)
      return cls(sub)


class MatroskaElementBinary(MatroskaElement):
   __slots__ = ('type', 'data_r')
   def __init__(self, data_r):
      super().__init__()
      self.data_r = data_r

   def __deepcopy__(self, mdict):
      return type(self)(self.data_r)

   @property
   def val(self):
      return self.data_r

   def __format__(self, fs):
      return '<{0} {1}>'.format(self.__class__.__name__, self.data_r)

   def _get_body_size(self):
      return MatroskaVInt(self.data_r.get_size())

   def get_size(self):
      bd_size = self._get_body_size()
      return (self.type.size + bd_size.size + bd_size)
   
   def write_to_file(self, c):
      bd = self.data_r.get_data()
      hl = self._write_header(c, self._get_body_size())
      blw = c.f.write(bd)
      if (blw != len(bd)):
         raise IOError()
      return (hl+blw)
      
   @classmethod
   def new(cls, data, *args, **kwargs):
      if (isinstance(data, bytes)):
         data = DataRefBytes(data)
      
      return cls(data, *args, **kwargs)

   @classmethod
   def _build_from_file(cls, body_size, f):
      data_r = DataRefFile(f, f.tell(), body_size)
      return cls(data_r)


class MatroskaElementBaseNum(MatroskaElement):
   __slots__ = ('type', 'val')
   def __init__(self, val, body_size=None):
      super().__init__()
      self.val = val

   def __deepcopy__(self, mdict):
      return type(self)(deepcopy(self.val,mdict), self._get_body_size())

   def write_to_file(self, c, _val=None):
      if (_val is None):
         _val = self.val
      
      bd_len = self._get_body_size()
      if (bd_len > 8):
         raise MatroskaError("Can't encode int value {0}; would require {1} (> 8) data bytes.".format(self.val, bd_len))
      body_data = struct.pack(self._get_bfmt(bd_len), _val)
      rv = self._write_header(c, bd_len)
      if (bd_len):
         rv += c.f.write(body_data[-1*bd_len:])
      return rv

   def get_size(self):
      bd_size = self._get_body_size()
      return (self.type.size + MatroskaVInt(bd_size).size + bd_size)

   @classmethod
   def _get_bfmt(cls, size):
      return cls.bfmt

   @classmethod
   def _build_from_file(cls, *args, **kwargs):
      (sub_cls, *sub_args) = cls._get_data_from_file(*args, **kwargs)
      return sub_cls(*sub_args)
      
   @classmethod
   def _get_data_from_file(cls, body_size, f):
      bfmt = cls._get_bfmt(body_size)
      
      buf = bytearray(struct.calcsize(bfmt))
      if (body_size > len(buf)):
         raise MatroskaError('Support for >64bit data ints is currently unimplemented.')
      pad_sz = len(buf)-body_size
      i = f.readinto(memoryview(buf)[pad_sz:])
      cls._adjust_padding(buf, pad_sz)
      
      if (i != body_size):
         raise MatroskaError('Domain read error.')
      
      (val,) = struct.unpack(bfmt, buf)
      return (cls, val, body_size)
   
   @classmethod
   def _adjust_padding(cls, buf, pad_sz):
      return
   
   def __format__(self, fs):
      return '<{0} {1}>'.format(self.__class__.__name__, self.val)

class MatroskaElementUInt(MatroskaElementBaseNum):
   bfmt = '>Q'
   def __init__(self, val, *args, **kwargs):
      assert(val >= 0)
      val.bit_length()
      super().__init__(val, *args, **kwargs)
   
   def _get_body_size(self):
      # While EBML allows for 0-byte ints, matroska sets a minimum length of 1 byte for ... some reason.
      return math.ceil(self.val.bit_length()/8) or 1

class MatroskaElementSInt(MatroskaElementBaseNum):
   bfmt = '>q'
   def __init__(self, val, *args, **kwargs):
      val.bit_length()
      super().__init__(val, *args, **kwargs)
   
   def _get_body_size(self):
      return math.ceil(((self.val + (self.val < 0)).bit_length() + 1)/8)
   
   @classmethod
   def _adjust_padding(cls, buf, pad_sz):
      if not (buf[pad_sz] & 128):
         return
      buf[:pad_sz] = b'\xFF'*pad_sz

class MatroskaElementFloat(MatroskaElementBaseNum):
   def __init__(self, val, body_size=8):
      super().__init__(val, body_size)
      float(val)
      self.bfmt = self._get_bfmt(body_size)
      self._body_size = body_size
   
   @classmethod
   def _get_bfmt(cls, size):
      if (size == 4):
         return '>f'
      elif (size == 8):
         return '>d'
      raise MatroskaError('Support for non {{32, 64}}bit floats (got {0} bytes) is currently unimplemented.'.format(size))
   
   def _get_body_size(self):
      return self._body_size

class MatroskaElementDate(MatroskaElementSInt):
   bfmt = '>q'
   ut_td = (datetime.datetime(1970,1,1) - datetime.datetime(2001,1,1))
   ut_offset = (ut_td.days*86400 + ut_td.seconds)
   del(ut_td)
   def __deepcopy__(self, mdict):
      return self.new(deepcopy(self._val,mdict))
   
   @property
   def val(self):
      rv = self._val + self.ut_offset
      rv *= 1000000000
      return round(rv)
   
   @val.setter
   def val(self, val):
      val /= 1000000000
      val -= self.ut_offset
      self._val = val
   
   @classmethod
   def new(cls, ts):
      rv = cls(0)
      rv._val = ts
      return rv
   
   def __format__(self, fs):
      return '<{0} {1}>'.format(self.__class__.__name__, time.strftime('%Y-%m-%d %H:%M:%S.%M', time.gmtime(self._val)))


class MatroskaElementStringBase(MatroskaElement):
   def __init__(self, val):
      super().__init__()
      self.val = val
      val.encode(self.codec)

   def __deepcopy__(self, mdict):
      return type(self)(deepcopy(self.val,mdict))

   def write_to_file(self, c):
      body_data = self.val.encode(self.codec)
      rv = self._write_header(c, MatroskaVInt(len(body_data)))
      rv += c.f.write(body_data)
      return rv

   def get_size(self):
      bd_size = len(self.val.encode(self.codec))
      return (self.type.size + MatroskaVInt(bd_size).size + bd_size)

   def __format__(self, fs):
      return '<{0} {1!a}>'.format(self.__class__.__name__, self.val)

   @classmethod
   def _build_from_file(cls, body_size, f):
      val = f.read(body_size).decode(cls.codec)
      if (len(val) != body_size):
         raise MatroskaError('Domain read error.')
      return cls(val)


class MatroskaElementStringASCII(MatroskaElementStringBase):
   codec = 'ascii'

class MatroskaElementStringUTF8(MatroskaElementStringBase):
   codec = 'utf-8'

def _cls_default_set(cls):
   try:
      default_val = cls.default
   except AttributeError:
      cls.default = None
   else:
      cls.default = cls(default_val)

# ---------------------------------------------------------------- EBML namespaces
class EBMLNamespace:
   cls_build_default = EBMLElementUnknown
   def __init__(self, vint_type, bases=()):
      self.vint_type = vint_type
      self.bases = tuple(bases)
      self.cls_map = {}
   
   def register_element(self, cls):
      """Add supported EBML element cls to this namespace. Intended for use as a decorator."""
      self.cls_map[cls.type] = cls
      _cls_default_set(cls)
      return cls
   
   def _etype2bff(self, etype, default_ok=True):
      try:
         rv = self.cls_map[etype]._build_from_file
      except KeyError:
         pass
      else:
         return rv
      
      for base in self.bases:
         rv = base._etype2bff(etype, default_ok=False)
         if not (rv is None):
            return rv
      
      if ((not default_ok) or (self.cls_build_default is None)):
         return None
      
      return self.cls_build_default._bff_curry(etype)
   
   def build_from_file(self, f):
      """Deserialize one EBML element from filelike, returning it and the number of bytes consumed."""
      bufsize = 8
      off = f.tell()
      (etype, sz_et) = self.vint_type.build_from_file(f)
      off += sz_et
      f.seek(off)
      (size, sz_sz) = self.vint_type.build_from_file(f)
      off += sz_sz
      f.seek(off)
      bff = self._etype2bff(etype)
      rv = (bff(size, f), sz_sz+sz_et+size)
      return rv

   def build_seq_from_file(self, f, size_lim=None):
      """Deserialize sequence of EBML elements from filelike, up to size_lim (or EOF if not specified)."""
      off = f.tell()
      rv = []
      if (size_lim is None):
         off_lim = f.seek(0,2)
      else:
         off_lim = off + size_lim
      
      while (off < off_lim):
         f.seek(off)
         (el, size) = self.build_from_file(f)
         rv.append(el)
         off += size
      
      if (off != off_lim):
         raise EBMLError('Element size / filesize mismatch.')
      return rv

ebml_ns_ebml = EBMLNamespace(EBMLVInt)
ebml_ns_mkv = EBMLNamespace(MatroskaVInt, (ebml_ns_ebml,))

_ebml_type_reg = ebml_ns_ebml.register_element
_mkv_type_reg = ebml_ns_mkv.register_element

# ---------------------------------------------------------------- Master elements
@_ebml_type_reg
class EBMLHeader(MatroskaElementMaster):
   type = EBMLVInt(172351395)

@_mkv_type_reg
class MatroskaElementSignatureSlot(MatroskaElementMaster):
   type = EBMLVInt(190023271)

@_mkv_type_reg
class MatroskaElementSegment(MatroskaElementMaster):
   type = EBMLVInt(139690087)
   def _iter_frames(self, tn, default_dur):
      for c in self.get_subl_by_cls(MatroskaElementCluster):
         tc = c.get_subval_by_cls(MatroskaElementTimecode)
         for bc in c.get_subl_by_cls((MatroskaElementSimpleBlock, MatroskaElementBlockGroup)):
            block = bc.get_block()
            (btn, btc, lacing, frame_count, hdrlen) = block._get_hd()
            if (btn != tn):
               continue
            
            dur = bc.get_dur()
            if not (dur is None):
               dur = round(dur/frame_count)
            else:
               dur = default_dur
            
            is_kf = bc.is_keyframe()
            ftc_delta = 0
            ftc = tc + btc
            for frame_data in block:
               ftc += ftc_delta
               yield(ftc, dur, frame_data, is_kf)
               ftc_delta = dur
      
   def make_mkvb(self):
      info = self.get_sub_by_cls(MatroskaElementInfo)
      
      tcs = info.get_subval_by_cls(MatroskaElementTimecodeScale)
      dur = info.get_subval_by_cls(MatroskaElementDuration)*tcs/10**9
      mb = MatroskaBuilder(tcs, dur)
      track_c = self.get_sub_by_cls(MatroskaElementTracks)
      
      tracks = list(track_c.sub)
      tracks.sort(key=lambda t:t.get_subval_by_cls(MatroskaElementTrackNumber))
      
      cue_track_set = set()
      for cp in self.get_sub_by_cls(MatroskaElementCues).sub:
         for ctp in cp.get_subl_by_cls(MatroskaElementCueTrackPositions):
            tn = ctp.get_subval_by_cls(MatroskaElementCueTrack)
            cue_track_set.add(tn)
      
      for te in tracks:
         te_cp = deepcopy(te)
         tn = te.get_subval_by_cls(MatroskaElementTrackNumber)
         default_dur = te.get_subval_by_cls(MatroskaElementDefaultDuration)
         if (default_dur is None):
            sdd = None
         else:
            sdd = round(default_dur/tcs)
         
         
         mb.add_track_by_entry(self._iter_frames(tn, sdd), te_cp, make_cues=(tn in cue_track_set))
      
      return mb
   
   def write_to_file(self, c):
      rv = self._write_header(c, MatroskaVInt(sum(e.get_size() for e in self.sub)))
      c.seg_off = c.f.tell()
      for e in self.sub:
         rv += e.write_to_file(c)
      return rv

@_mkv_type_reg
class MatroskaElementSeekHead(MatroskaElementMaster):
   type = EBMLVInt(21863284)

@_mkv_type_reg
class MatroskaElementSeek(MatroskaElementMaster):
   type = EBMLVInt(3515)

@_mkv_type_reg
class MatroskaElementInfo(MatroskaElementMaster):
   type = EBMLVInt(88713574)

@_mkv_type_reg
class MatroskaElementChapterTranslate(MatroskaElementMaster):
   type = EBMLVInt(10532)

@_mkv_type_reg
class MatroskaElementCluster(MatroskaElementMaster):
   type = EBMLVInt(256095861)
   @classmethod
   def new(cls, timecode):
      self = cls([MatroskaElementTimecode.new(timecode)])
      self._tc = timecode
      return self
   
   def write_to_file(self, c):
      if not (c.callback_cluster is None):
         c.callback_cluster(self, c.f.tell() - c.seg_off)
      return super().write_to_file(c)
   
   def _get_tc(self):
      return self._tc

@_mkv_type_reg
class MatroskaElementSilentTracks(MatroskaElementMaster):
   type = EBMLVInt(6228)

@_mkv_type_reg
class MatroskaElementBlockGroup(MatroskaElementMaster):
   type = EBMLVInt(32)   
   def get_block(self):
      return self.get_sub_by_cls(MatroskaElementBlock)
   
   def get_dur(self):
      return self.get_subval_by_cls(MatroskaElementBlockDuration)
   
   def is_keyframe(self):
      return (self.get_sub_by_cls(MatroskaElementReferenceBlock) is None)

@_mkv_type_reg
class MatroskaElementBlockAdditions(MatroskaElementMaster):
   type = EBMLVInt(13729)

@_mkv_type_reg
class MatroskaElementBlockMore(MatroskaElementMaster):
   type = EBMLVInt(38)

@_mkv_type_reg
class MatroskaElementSlices(MatroskaElementMaster):
   type = EBMLVInt(14)

@_mkv_type_reg
class MatroskaElementTimeSlice(MatroskaElementMaster):
   type = EBMLVInt(104)

@_mkv_type_reg
class MatroskaElementTracks(MatroskaElementMaster):
   type = EBMLVInt(106212971)
   def sort_tracks(self):
      def _key(e):
         return e.get_track_type()
      
      self.sub.sort(key=_key)
      rv = {}
      for i in range(len(self.sub)):
         t = self.sub[i]
         id1 = t.get_sub_by_cls(MatroskaElementTrackNumber)
         id2 = t.get_sub_by_cls(MatroskaElementTrackUID)
         rv[id1.val] = MatroskaVInt(i + 1)
         id1.val = id2.val = i + 1
      return rv
   
   def get_track(self, idx):
      return self.sub[idx-1]   

@_mkv_type_reg
class MatroskaElementTrackEntry(MatroskaElementMaster):
   type = EBMLVInt(46)
   def get_track_type(self):
      return self.get_sub_by_cls(MatroskaElementTrackType).val
   
   def get_track_id(self):
      return self.get_sub_by_cls(MatroskaElementTrackUID).val

@_mkv_type_reg
class MatroskaElementTrackTranslate(MatroskaElementMaster):
   type = EBMLVInt(9764)

@_mkv_type_reg
class MatroskaElementVideo(MatroskaElementMaster):
   type = EBMLVInt(96)
   @classmethod
   def new(cls, width, height):
      sub = []
      if not (width is None):
         sub.append(MatroskaElementPixelWidth.new(width))
      if not (height is None):
         sub.append(MatroskaElementPixelHeight.new(height))
      return cls(sub)

@_mkv_type_reg
class MatroskaElementAudio(MatroskaElementMaster):
   type = EBMLVInt(97)
   @classmethod
   def new(cls, sfreq, channels=2):
      sub = [
         MatroskaElementSamplingFrequency.new(sfreq,8),
         MatroskaElementChannels.new(channels)
      ]
      return cls(sub)

@_mkv_type_reg
class MatroskaElementContentEncodings(MatroskaElementMaster):
   type = EBMLVInt(11648)

@_mkv_type_reg
class MatroskaElementContentEncodings(MatroskaElementMaster):
   type = EBMLVInt(11648)

@_mkv_type_reg
class MatroskaElementContentEncoding(MatroskaElementMaster):
   type = EBMLVInt(8768)

@_mkv_type_reg
class MatroskaElementContentCompression(MatroskaElementMaster):
   type = EBMLVInt(4148)

@_mkv_type_reg
class MatroskaElementContentEncryption(MatroskaElementMaster):
   type = EBMLVInt(4149)

@_mkv_type_reg
class MatroskaElementCues(MatroskaElementMaster):
   type = EBMLVInt(206814059)
   def write_to_file(self, c):
      if not (c.callback_cues is None):
         c.callback_cues(c.f.tell())
      
      return super().write_to_file(c)
   
   def update_ccps(self, co_map):
      for cp in self.sub:
         for ctp in cp.get_subl_by_cls(MatroskaElementCueTrackPositions):
            ccp = ctp.get_sub_by_cls(MatroskaElementCueClusterPosition)
            ccp.val = co_map[ccp._clust_id]

@_mkv_type_reg
class MatroskaElementCuePoint(MatroskaElementMaster):
   type = EBMLVInt(59)
   @classmethod
   def new(cls, tv):
      return super().new([MatroskaElementCueTime.new(tv)])

@_mkv_type_reg
class MatroskaElementCueTrackPositions(MatroskaElementMaster):
   type = EBMLVInt(55)
   
   @classmethod
   def new(cls, tracknum, clust_id, cbn):
      ccp = MatroskaElementCueClusterPosition.new()
      ccp._clust_id = clust_id
      rv = super().new([
         MatroskaElementCueTrack.new(tracknum),
         ccp,
         MatroskaElementCueBlockNumber.new(cbn),
      ])
      return rv

@_mkv_type_reg
class MatroskaElementCueReference(MatroskaElementMaster):
   type = EBMLVInt(1)

@_mkv_type_reg
class MatroskaElementAttachments(MatroskaElementMaster):
   type = EBMLVInt(155296873)

@_mkv_type_reg
class MatroskaElementAttachedFile(MatroskaElementMaster):
   type = EBMLVInt(8615)

@_mkv_type_reg
class MatroskaElementChapters(MatroskaElementMaster):
   type = EBMLVInt(4433776)

@_mkv_type_reg
class MatroskaElementEditionEntry(MatroskaElementMaster):
   type = EBMLVInt(1465)

@_mkv_type_reg
class MatroskaElementChapterAtom(MatroskaElementMaster):
   type = EBMLVInt(54)

@_mkv_type_reg
class MatroskaElementChapterTrack(MatroskaElementMaster):
   type = EBMLVInt(15)

@_mkv_type_reg
class MatroskaElementChapterDisplay(MatroskaElementMaster):
   type = EBMLVInt(0)

@_mkv_type_reg
class MatroskaElementChapProcess(MatroskaElementMaster):
   type = EBMLVInt(10564)

@_mkv_type_reg
class MatroskaElementChapProcessCommand(MatroskaElementMaster):
   type = EBMLVInt(10513)

@_mkv_type_reg
class MatroskaElementTags(MatroskaElementMaster):
   type = EBMLVInt(39109479)

@_mkv_type_reg
class MatroskaElementTag(MatroskaElementMaster):
   type = EBMLVInt(13171)

@_mkv_type_reg
class MatroskaElementTagTargets(MatroskaElementMaster):
   type = EBMLVInt(9152)

@_mkv_type_reg
class MatroskaElementSimpleTag(MatroskaElementMaster):
   type = EBMLVInt(10184)

# ---------------------------------------------------------------- Binary elements
@_mkv_type_reg
class MatroskaElementCRC32(MatroskaElementBinary):
   type = EBMLVInt(63)

@_mkv_type_reg
class MatroskaElementVoid(MatroskaElementBinary):
   type = EBMLVInt(108)
   def __init__(self, *args, **kwargs):
      super().__init__(*args, **kwargs)
      self._bd_size = MatroskaVInt(self.data_r.get_size())
      
   def _get_body_size(self):
      rv = self._bd_size
      if (rv != self.data_r.get_size()):
         raise ValueError('Internal data structure mismatch; cached bd_size = {0} != {1} = current body size.'.format(
            rv, self.data_r.get_size()))
      return rv
   
   @classmethod
   def new_by_size(cls, full_size):
      vint_len_max = 8
      # Modifying just the body size will not allow us to fit full body sizes of the form n=(128**i+i), i.e. 129, 16386,
      # 2097155, etc. due to the way size vint lengths scale with body length. The case of (i=0) is completely impossible to
      # satisfy; for all others, we can compensate by padding our size field by one byte.
      # Catch these cases here and determine size vint length padding, if any.
      i = 1
      n = 0
      while (n < full_size):
         n = (1 << (7*i)) + i
         i += 1
      
      sz_len_pad = (full_size == n)
      
      # Simple incremental approximation algorithm for finding the right body size. Probably not the most elegant way to do
      # this, but it should work.
      bd_sz = full_size
      bd_sz_set = set()
      while (not (bd_sz in bd_sz_set)):
         bd_sz_set.add(bd_sz)
         void = cls(DataRefNull(bd_sz))
         sz_diff = full_size - void.get_size()
         if (0 <= sz_diff <= sz_len_pad):
            void._bd_size.size += sz_len_pad
            if (void._bd_size.size > vint_len_max):
               raise MatroskaError('Unable to craft Void element with precise length {0}; size vint length overflow.'.format(full_size))
            
            return void
         bd_sz += sz_diff
         bd_sz = max(bd_sz, 0)
      
      raise MatroskaError('Unable to craft Void element with precise length {0}.'.format(full_size))
      

@_mkv_type_reg
class MatroskaElementSegmentUID(MatroskaElementBinary):
   type = EBMLVInt(13220)

class MatroskaElementBlockBase(MatroskaElementBinary):
   type = EBMLVInt(33)
   def _get_hd(self):
      data = self.data_r.get_data()
      (tn,off) = MatroskaVInt.build_from_bindata(data)
      (tc,flags) = struct.unpack('>hB', data[off:off+3])
      lacing = (flags >> 1) & 3
      off += 3
      if (lacing == 0):
         return (tn, tc, lacing, 1, off)
      
      frame_count = struct.unpack('>B', data[off:off+1])[0] + 1
      off += 1
      return (tn, tc, lacing, frame_count, off)
   
   def __iter__(self):
      data = memoryview(self.data_r.get_data())
      (tn, tc, lacing, frame_count, off) = self._get_hd()
      ldata = len(data)
      
      if (lacing == 0):
         yield DataRefMemoryView(data[off:])
         return
      
      elif (lacing == 1):
         frame_lengths = [None]*(frame_count-1)
         
         for i in range(frame_count-1):
            (frame_length,) = (inc,) = struct.unpack('>B', data[off:off+1])
            off += 1
            while (inc == 255):
               (inc,) = struct.unpack('>B', data[off:off+1])
               frame_length += inc
               off += 1
            frame_lengths[i] = frame_length
      
      elif (lacing == 2):
         body_len = ldata-off
         frame_len = body_len//frame_count
         if (frame_len != body_len/frame_count):
            raise MatroskaError('Bogus fixed size lacing: {0} frames in {1} bytes.'.format(frame_count, ldata))
         frame_lengths = [frame_len]*(frame_count-1)

      else: # lacing == 3
         frame_lengths = [None]*(frame_count-1)
         (frame_lengths[0],off_i) = MatroskaVInt.build_from_bindata(data[off:])
         off += off_i
         
         for i in range(frame_count-2):
            (sval, off_i) = MatroskaSVInt.build_from_bindata(data[off:])
            frame_lengths[i+1] = sval + frame_lengths[i]
            off += off_i

      if (sum(frame_lengths,0) + off > len(data)):
         raise MatroskaError('Bogus lacing: {0} bytes header, and alleged frame size list {1} in {2} bytes.'.format(off, frame_lengths, len(data)))

      for frame_len in frame_lengths:
         yield DataRefMemoryView(data[off:off+frame_len])
         off += frame_len
      yield DataRefMemoryView(data[off:])
        

@_mkv_type_reg
class MatroskaElementBlock(MatroskaElementBlockBase):
   type = EBMLVInt(33)

@_mkv_type_reg
class MatroskaElementSimpleBlock(MatroskaElementBlockBase):
   type = EBMLVInt(35)
   def get_block(self):
      return self
   
   def get_dur(self):
      return None
      
   def is_keyframe(self):
      data = self.data_r.get_data()
      off = MatroskaVInt.build_from_bindata(data)[1]
      return bool(data[off+2] >> 7)

@_mkv_type_reg
class MatroskaElementCodecPrivate(MatroskaElementBinary):
   type = EBMLVInt(9122)

_MATROSKA_LT_XIPH = 1
_MATROSKA_LT_EBML = 2
_MATROSKA_LT_FIXED = 4
_MATROSKA_LT_ANY = _MATROSKA_LT_XIPH | _MATROSKA_LT_EBML | _MATROSKA_LT_FIXED

class MatroskaElementBlock_r(MatroskaElement):
   _bfmt_subhdr = '>hB'
   _bfmt_subhdr_len = struct.calcsize(_bfmt_subhdr)
   _LT_MAP = {
      None: 0,
      _MATROSKA_LT_XIPH: 1,
      _MATROSKA_LT_EBML: 3,
      _MATROSKA_LT_FIXED: 2
   }
   _LT_MAP_INV = dict((v,k) for (k,v) in _LT_MAP.items())
   
   def __init__(self, etype:int, tracknum:int, timecode:int, flags:int, keyframe:bool, frame_data, lace_data=None):
      super().__init__()
      self.type = etype
      
      struct.pack(self._bfmt_subhdr, timecode, flags)
      self.tracknum = MatroskaVInt(tracknum)
      self.timecode = timecode
      self.flags = flags
      self.frame_data = frame_data
      self.lace_data = lace_data
      
   @classmethod
   def new(cls, *args, **kwargs):
      return cls(MatroskaElementBlock.type, *args, **kwargs)
   
   def get_timecode(self):
      return self.timecode
   
   @classmethod
   def new_simple(cls, tracknum:int, timecode:int, flags:int, keyframe, *args, **kwargs):
      flags |= (keyframe << 7)
      return cls(MatroskaElementSimpleBlock.type, tracknum, timecode, flags, keyframe, *args, **kwargs)
   
   def get_size(self):
      bd_size = self._bfmt_subhdr_len + self.tracknum.size + self._get_lacehdr_size() + self._get_data_size()
      return (self.type.size + MatroskaVInt(bd_size).size + bd_size)
   
   def _get_data_size(self):
      return sum(d.get_size() for d in self.frame_data)
   
   def _get_lacehdr_size(self):
      if (self.lace_data is None):
         return 0
      return self.lace_data.get_size() + 1
   
   def write_to_file(self, c):
      ld = self.lace_data
      
      ls = self._get_lacehdr_size()
      if (ld is None):
         if (len(self.frame_data) > 1):
            raise MatroskaError("Can't encode {0} frames without lacing.".format(len(self.frame_data)))
         lh = b''
      else:
         lh = struct.pack('>B', len(self.frame_data)-1)
         ls -= 1
      
      flags = self.flags & ~6
      if not (ld is None):
         flags |= self._LT_MAP[ld.LT] << 1
      shdr = b''.join((self.tracknum.get_bindata(), struct.pack(self._bfmt_subhdr, self.timecode, flags), lh))
      bd_sz = len(shdr) + ls + self._get_data_size()
      
      hl = self._write_header(c, MatroskaVInt(bd_sz))
      bl = c.f.write(shdr)
      if not (ld is None):
         bl += ld.write_to_file(c)
      for data_r in self.frame_data:
         bl += c.f.write(data_r.get_data())
      
      if (bl != bd_sz):
         raise IOError()
      return (hl + bl)

# ---------------------------------------------------------------- UInt elements
@_ebml_type_reg
class EBMLElementVersion(MatroskaElementUInt):
   type = EBMLVInt(646)

@_ebml_type_reg
class EBMLElementReadVersion(MatroskaElementUInt):
   type = EBMLVInt(759)

@_ebml_type_reg
class EBMLElementMaxIDLength(MatroskaElementUInt):
   type = EBMLVInt(754)

@_ebml_type_reg
class EBMLElementMaxIDLength(MatroskaElementUInt):
   type = EBMLVInt(754)

@_ebml_type_reg
class EBMLElementMaxSizeLength(MatroskaElementUInt):
   type = EBMLVInt(755)

@_ebml_type_reg
class EBMLElementDocTypeVersion(MatroskaElementUInt):
   type = EBMLVInt(647)

@_ebml_type_reg
class EBMLElementDocTypeReadVersion(MatroskaElementUInt):
   type = EBMLVInt(645)

@_mkv_type_reg
class MatroskaElementPrevSize(MatroskaElementUInt):
   type = EBMLVInt(43)

@_mkv_type_reg
class MatroskaElementFlagLacing(MatroskaElementUInt):
   type = EBMLVInt(28)

@_mkv_type_reg
class MatroskaElementCueClusterPosition(MatroskaElementUInt):
   type = EBMLVInt(113)
   def write_to_file(self, c):
      return super().write_to_file(c)
   
   @classmethod
   def new(cls, cp=None):
      if (cp is None):
         cp = (1 << 64) - 1
      return super().new(cp)

@_mkv_type_reg
class MatroskaElementCueTime(MatroskaElementUInt):
   type = EBMLVInt(51)

@_mkv_type_reg
class MatroskaElementCueTrack(MatroskaElementUInt):
   type = EBMLVInt(119)

@_mkv_type_reg
class MatroskaElementCueBlockNumber(MatroskaElementUInt):
   type = EBMLVInt(4984)

@_mkv_type_reg
class MatroskaElementTimecodeScale(MatroskaElementUInt):
   default = 1000000
   type = EBMLVInt(710577)

@_mkv_type_reg
class MatroskaElementTimecode(MatroskaElementUInt):
   type = EBMLVInt(103)

@_mkv_type_reg
class MatroskaElementTrackNumber(MatroskaElementUInt):
   type = EBMLVInt(87)

@_mkv_type_reg
class MatroskaElementTrackUID(MatroskaElementUInt):
   type = EBMLVInt(13253)

TRACKTYPE_VIDEO = 0x01
TRACKTYPE_AUDIO = 0x02
TRACKTYPE_SUB = 0x11
@_mkv_type_reg
class MatroskaElementTrackType(MatroskaElementUInt):
   type = EBMLVInt(3)

@_mkv_type_reg
class MatroskaElementFlagEnabled(MatroskaElementUInt):
   type = EBMLVInt(57)

@_mkv_type_reg
class MatroskaElementFlagDefault(MatroskaElementUInt):
   type = EBMLVInt(8)

@_mkv_type_reg
class MatroskaElementPixelWidth(MatroskaElementUInt):
   type = EBMLVInt(48)

@_mkv_type_reg
class MatroskaElementPixelHeight(MatroskaElementUInt):
   type = EBMLVInt(58)

@_mkv_type_reg
class MatroskaElementChannels(MatroskaElementUInt):
   default = 1
   type = EBMLVInt(31)

@_mkv_type_reg
class MatroskaElementBlockDuration(MatroskaElementUInt):
   type = EBMLVInt(27)

@_mkv_type_reg
class MatroskaElementMaxCache(MatroskaElementUInt):
   type = EBMLVInt(11751)

@_mkv_type_reg
class MatroskaElementDefaultDuration(MatroskaElementUInt):
   type = EBMLVInt(254851)

# ---------------------------------------------------------------- SInt elements
@_mkv_type_reg
class MatroskaElementReferenceBlock(MatroskaElementSInt):
   type = EBMLVInt(123)

# ---------------------------------------------------------------- Date elements
@_mkv_type_reg
class MatroskaElementDateUTC(MatroskaElementDate):
   type = EBMLVInt(1121)


# ---------------------------------------------------------------- float elements
@_mkv_type_reg
class MatroskaElementDuration(MatroskaElementFloat):
   type = EBMLVInt(1161)

@_mkv_type_reg
class MatroskaElementSamplingFrequency(MatroskaElementFloat):
   default = 8000
   type = EBMLVInt(53)

@_mkv_type_reg
class MatroskaElementOutputSamplingFrequency(MatroskaElementFloat):
   type = EBMLVInt(14517)

# Big fat warning: Non-trivial (!= 1.0) uses of this tag appear to be extremely rare in mkv files, and support for them less
# than universal in media players in 2010-07. Based on webrumors this situation might become permanent. This state of affairs
# is unfortunate, but for now decent TTS support should probably be presumed absent unless proven otherwise, and uses of this
# tag avoided for that reason.
@_mkv_type_reg
class MatroskaElementTrackTimecodeScale(MatroskaElementFloat):
   type = EBMLVInt(209231)

# ---------------------------------------------------------------- String elements
@_ebml_type_reg
class EBMLElementDocType(MatroskaElementStringASCII):
   type = EBMLVInt(642)

@_mkv_type_reg
class MatroskaElementMuxingApp(MatroskaElementStringASCII):
   type = EBMLVInt(3456)

@_mkv_type_reg
class MatroskaElementName(MatroskaElementStringUTF8):
   type = EBMLVInt(4974)

@_mkv_type_reg
class MatroskaElementWritingApp(MatroskaElementStringASCII):
   type = EBMLVInt(5953)

@_mkv_type_reg
class MatroskaElementTitle(MatroskaElementStringUTF8):
   type = EBMLVInt(15273)

@_mkv_type_reg
class MatroskaElementLang(MatroskaElementStringASCII):
   type = EBMLVInt(177564)

@_mkv_type_reg
class MatroskaElementCodec(MatroskaElementStringASCII):
   type = EBMLVInt(6)

@_mkv_type_reg
class MatroskaElementFileDescription(MatroskaElementStringUTF8):
   type = EBMLVInt(1662)

@_mkv_type_reg
class MatroskaElementFileName(MatroskaElementStringUTF8):
   type = EBMLVInt(1646)

@_mkv_type_reg
class MatroskaElementFileMimeType(MatroskaElementStringASCII):
   type = EBMLVInt(1632)

# ---------------------------------------------------------------- File construction
def _make_random_uid():
   return struct.pack('>QQ', random.getrandbits(64), random.getrandbits(64))

class MatroskaCodec(str):
   CODEC_ID2MKV = {
      #video
      CODEC_ID_MPEG1_2: 'V_MPEG1',
      CODEC_ID_MPEG2_2: 'V_MPEG2',
      CODEC_ID_MPEG4_2: 'V_MPEG4/ISO/ASP',
      CODEC_ID_MPEG4_10: 'V_MPEG4/ISO/AVC',
      CODEC_ID_SNOW: 'V_SNOW',
      CODEC_ID_THEORA: 'V_THEORA',
      CODEC_ID_VP8: 'V_VP8',
      # audio
      CODEC_ID_AAC: 'A_AAC',
      CODEC_ID_AC3: 'A_AC3',
      CODEC_ID_DTS: 'A_DTS',
      CODEC_ID_FLAC: 'A_FLAC',
      CODEC_ID_MP1: 'A_MPEG/L1',
      CODEC_ID_MP2: 'A_MPEG/L2',
      CODEC_ID_MP3: 'A_MPEG/L3',
      CODEC_ID_VORBIS: 'A_VORBIS',
      # subtitles
      CODEC_ID_ASS: 'S_TEXT/ASS',
      CODEC_ID_SSA: 'S_TEXT/SSA',
      # pseudo codecs
      CODEC_ID_MKV_MSC_VFW: 'V_MS/VFW/FOURCC',
      CODEC_ID_MKV_MSC_ACM: 'A_MS/ACM'
   }
   @classmethod
   def build_from_id(cls, codec_id):
      return cls(cls.CODEC_ID2MKV[codec_id])

class _OutputCtx:
   def __init__(self, f):
      self.f = f
      self.callback_cluster = None
      self.callback_cues = None

class MatroskaFrame:
   def __init__(self, timecode, flags, tc_dependencies, data_r, dur=None):
      self.tc = timecode
      self.flags = flags
      self.tc_dependencies = tc_dependencies
      self.data_r = data_r
      self.dur = dur
   
   def is_keyframe(self):
      return (not self.tc_dependencies)
   
   def build_blockthing(self, tracknum, c_off):
      keyframe = self.is_keyframe()
      if (self.dur is None):
         return MatroskaElementBlock_r.new_simple(tracknum, self.tc-c_off, self.flags, keyframe, [self.data_r])
   
      se = [MatroskaElementReferenceBlock.new(tc) for tc in self.tc_dependencies]
      if not (self.dur is None):
         se.append(MatroskaElementBlockDuration.new(self.dur))
      se.append(MatroskaElementBlock_r.new(tracknum, self.tc-c_off, self.flags, keyframe, [self.data_r]))
      return MatroskaElementBlockGroup.new(se)


class BitmapInfoHeader:
   BFMT = '<lllhh4slllll'
   BFMT_LEN = struct.calcsize(BFMT)
   
   WRAP_CODEC_ID = CODEC_ID_MKV_MSC_VFW
   
   ID2CODEC = {
      CODEC_ID_FLV1: FourCC(b'FLV1'),
      CODEC_ID_VP6: FourCC(b'FLV4'),
      CODEC_ID_PNG: FourCC(b'MPNG')
   }
   
   def __init__(self, codec_id, width, height):
      self.width = width
      self.height = height
      try:
         self.codec = self.ID2CODEC[codec_id]
      except KeyError as exc:
         raise ContainerCodecError("Can't encapsulate {0} data into {1} as part of MS compatibility mode.".format(codec_id, type(self).__name__)) from exc
      self.planes = 1
      self.colour_depth = 0
      self.x_ppm = 0
      self.y_ppm = 0
      self.get_bindata()
   
   def get_bindata(self):
      return struct.pack(self.BFMT, self.BFMT_LEN, self.width, self.height, self.planes, self.colour_depth,
         struct.pack('>L', self.codec), 0, self.x_ppm, self.y_ppm, 0, 0)

class _LaceLengthSeq(deque):
   pass

class _LaceLengthSeqEBML(_LaceLengthSeq):
   LT = _MATROSKA_LT_EBML
   @classmethod
   def build_from_frames(cls, frames):
      sz_prev = frames[0].data_r.get_size()
      rv = cls((MatroskaVInt(sz_prev),))
      for frame in frames[1:-1]:
         sz = frame.data_r.get_size()
         rv.append(MatroskaSVInt(sz - sz_prev))
         sz_prev = sz
      return rv
   
   def get_size(self):
      return sum(i.size for i in self)
   
   def write_to_file(self, c):
      rv = 0
      for e in self:
         rv += e.write_to_file(c)
      return rv

class _LaceLengthSeqXiph(_LaceLengthSeq):
   LT = _MATROSKA_LT_XIPH
   @classmethod
   def build_from_frames(cls, frames):
      rv = cls()
      for frame in frames[:-1]:
         (pl, mod) = divmod(frame.data_r.get_size(),255)
         rv.append(b'\xFF'*pl + struct.pack('>B', mod))
   
      return rv
   
   def get_size(self):
      return sum(len(e) for e in self)
   
   def write_to_file(self, c):
      rv = 0
      for e in self:
         rv += c.f.write(e)
      return rv


class _FrameQueue:
   def __init__(self, frames, lace_mask):
      self._f = frames
      self._i = 0
      self._lm = lace_mask
      
      if (frames):
         if (self._lm):
            dur = frames[0].dur
            for frame in frames[1:]:
               if (frame.dur != dur):
                  self._lm = False
                  break
       
         if (self._lm & _MATROSKA_LT_FIXED):
            sz = frames[0].dur
            for frame in frames[1:]:
               if (frame.data_r.get_size() != sz):   
                  self._lm &= ~_MATROSKA_LT_FIXED
                  break
   
   def __bool__(self):
      return (self._i < len(self._f))
   
   def get_frame(self):
      return self._f[self._i]
   
   def _pop_frame(self):
      rv = self._f[self._i]
      self._i += 1
      return rv
      
   def make_blockthing(self, lace_limit, *args, **kwargs):
      ll = min(lace_limit, 256)
      frame0 = self._pop_frame()
      kf = frame0.is_keyframe()
      bt = frame0.build_blockthing(*args, **kwargs)
      fl = [frame0]
      if (self._lm):
         while (self and (len(fl) < ll)):
            frame = self._pop_frame()
            if (frame.is_keyframe() != kf):
               self._i -= 1
               break
            fl.append(frame)
      
      if ((not self._lm) or (len(fl) <= 1)):
         return bt
      
      if (self._lm & _MATROSKA_LT_FIXED):
         lt = _MATROSKA_LT_FIXED
      else:
         lls = None
         for lls_cls in (_LaceLengthSeqXiph, _LaceLengthSeqEBML):
            if not (lls_cls.LT & self._lm):
               continue
            lls_n = lls_cls.build_from_frames(fl)
            if ((lls is None) or (lls_n.get_size() < lls.get_size())):
               lls = lls_n
         
         if (lls is None):
            raise MatroskaError("Lacing type choosing failed. :( This shouldn't happen, and indicates a bug in yavdlt.")

      bt.lace_data = lls
      bt.frame_data = [frame.data_r for frame in fl]
      return bt


class MatroskaBuilder:
   settings_map = {
      TRACKTYPE_VIDEO: MatroskaElementVideo,
      TRACKTYPE_AUDIO: MatroskaElementAudio
   }
   tcs_error_lim_default = 0.0001
   TLEN_CLUSTER = 2**16
   TOFF_CLUSTER = 2**15
   #TLEN_CLUSTER = 2**15
   #TOFF_CLUSTER = 0

   TRACKTYPE_VIDEO = TRACKTYPE_VIDEO
   TRACKTYPE_AUDIO = TRACKTYPE_AUDIO
   TRACKTYPE_SUB = TRACKTYPE_SUB

   MS_CM_CLS_MAP = {
      TRACKTYPE_VIDEO: BitmapInfoHeader
   }
   
   # Be bug compatible with mplayer r1.0~rc3+svn20100502-4.4.4, at the cost of allocating the first cluster suboptimally.
   bc_old_mplayer = True
   # Be bug compatible with libavformat from ffmpeg r25042 (current as-of-writing), at the cost of losing all ability to
   # perform interlacing on non-audio streams.
   bc_lavf_lacing = True
   # Work around some VLC bugs. Note that VLC is sufficiently buggy that working around *all* of them is likely to remain
   # impractical for the forseeable future. For instance, there are currently no plans to implement a workaround for
   # <https://trac.videolan.org/vlc/ticket/2702>.
   bc_vlc = True
   # How many frames at most to put in the same block through interlacing. 256 is the theoretical maximum, but media player
   # support for that at the time of writing is ... extremely lacking. 255 seems to work ok for the most part - but
   # mplayer/lavf might start to complain about playback performance. 32 appears to work well, and the additional benefits
   # from going up to 255 aren't sufficient to bother at this time.
   frames_per_block_limit = 32
   
   MS_CM_NEVER = 0
   MS_CM_AUTO = 1
   MS_CM_FORCE = 2
   
   def __init__(self, tcs, dur, ts=None, lace_mask=_MATROSKA_LT_ANY):
      self.ebml_hdr = EBMLHeader.new([
         EBMLElementDocType.new('matroska'),
         EBMLElementDocTypeVersion.new(2),
         EBMLElementDocTypeReadVersion.new(2)
      ])
      
      if (ts is None):
         ts = time.time()
      
      self.mkv_info = MatroskaElementInfo.new([
         MatroskaElementSegmentUID.new(DataRefBytes(_make_random_uid())),
         MatroskaElementTimecodeScale.new(tcs),
         MatroskaElementDateUTC.new(ts),
         MatroskaElementMuxingApp.new(self._get_muxapp())
      ])
      
      self.dur = dur
      if not (dur is None):
         self.mkv_info.sub.append(MatroskaElementDuration.new(dur*10**9/tcs,8))
      
      self.tcs = tcs
      self.tracks = MatroskaElementTracks.new([])
      self.frames = {}
      self.lace_mask = lace_mask
   
   def set_writingapp(self, write_app):
      self.mkv_info.set_sub(MatroskaElementWritingApp.new(write_app))
   
   def set_segment_title(self, title):
      self.mkv_info.set_sub(MatroskaElementTitle.new(title))
      
   def set_track_name(self, tid, name):
      self.tracks.sub[tid].set_sub(MatroskaElementName.new(name))
   
   def _get_muxapp(self):
      return 'yavdlt.mcio_matroska pre-versioning-version'
   
   def _add_frame(self, tracknum, *args, **kwargs):
      frame = MatroskaFrame(*args, **kwargs)
      try:
         fl = self.frames[tracknum]
      except KeyError:
         fl = self.frames[tracknum] = []
      
      fl.append(frame)
      return frame
   
   def get_tracks_by_type(self, tt):
      return [t for t in self.tracks.sub if (t.get_track_type() == tt)]
   
   def drop_track(self, tid):
      del(self.tracks.sub[tid-1])
      del(self.frames[tid])
   
   def _build_clusters(self):
      frames = {}
      for (key, val) in self.frames.items():
         frames[key] = _FrameQueue(val, self.lace_mask)
      
      clusters = deque()
      cues = {}
      c = None
      c_max = -1
      c_min = 0
      
      tlen_c = self.TLEN_CLUSTER
      if (self.bc_old_mplayer or self.bc_vlc):
         # Old mplayer versions can't seek backwards with more than cluster-level granularity; IOW, when seeking backwards,
         # they will always pick the first keyframe from a cluster, even if the closest such match is e.g. an entire minute
         # in the past.
         # VLC also doesn't like long clusters, though the symptoms are somewhat different (in particular, short forward
         # seek requests can end up seeking backwards instead).
         # To prevent these bugs from having too much of a UI impact, we limit the absolute cluster duration to 5 seconds
         # seconds here if either of the relevant bug compatibility modes is enabled.
         tlen_c = min(tlen_c, int(5*10**9/self.tcs))
      
      def add_cluster(tc):
         nonlocal c, c_max, c_min
         c2 = MatroskaElementCluster.new(tc+self.TOFF_CLUSTER)
         if not (c is None):
            c2.set_sub(MatroskaElementPrevSize.new(c.get_size()))
         c = c2
         clusters.append(c)
         c_min = c._tc - 2**15
         c_max = c_min + tlen_c - 1
         c.__blockcount = 0
      
      if (self.bc_old_mplayer):
         # Older mplayer is a big baby about this, using the base timecode of the first cluster in a segment to set the
         # beginning TC of said segment. Even for segments that start with the block with the lowest display time at the
         # beginning of the first cluster, this will blow up if said cluster TC is set to allow for the maximum range, since
         # this implies negative block timecodes for the first blocks in the cluster.
         # This code works around that bug by aligning the base TC of the first cluster with the TC of our earliest frame, at
         # the cost of leaving half of the possible timecodes in that cluster unusuable and therefore slightly increasing mkv
         # file size on average.
         try:
            frame_tc_min = min(min(f.tc for f in frame_list) for frame_list in self.frames.values())
         except ValueError:
            pass
         else:
            add_cluster(-self.TOFF_CLUSTER+frame_tc_min)
      
      while (frames):
         (tn, tframes) = min(frames.items(), key=lambda x:x[1].get_frame().tc)
         track = self.tracks.get_track(tn)
         frame0 = tframes.get_frame()
         tc = frame0.tc
         
         if ((tc > c_max) or (tc < c_min)):
            add_cluster(tc)
         
         if (track._lacing):
            bt = tframes.make_blockthing(self.frames_per_block_limit, tn, c._tc)
         else:
            bt = tframes.make_blockthing(1, tn, c._tc)
         
         if (not tframes):
            del(frames[tn])
         
         #c.sub.append(frame.build_blockthing(tn, c._tc))
         c.sub.append(bt)
         c.__blockcount += 1
         
         if (frame0.is_keyframe() and track._make_cues):
            # Make cue entry.
            try:
               cp = cues[tc]
            except KeyError:
               cp = cues[tc] = MatroskaElementCuePoint.new(tc)

            ctp = MatroskaElementCueTrackPositions.new(tn, id(c), c.__blockcount-1)
            cp.sub.append(ctp)
         
      return (clusters, cues)

   @classmethod
   def tcs_from_secdiv(cls, sdiv:int, td_gcd:int, error_lim:float=None) -> ('tcs','elmult','error'):
      """Calculate appropriate mkv timecodescale from (1s/siv) TCS, gcd of timedeltas and desired error.
      
      Note that error_lim isn't a hard limit; the results will exceed it under sufficiently bad conditions."""
      
      if (error_lim is None):
         error_lim = cls.tcs_error_lim_default
      
      # TODO: This function suffers from a likely case of insufficient understanding of the problem domain and code
      # overdesign. The main optimization loop might not even come up for any non-pathological input. See if this can be
      # refactored without significant loss of generality at some point.
      
      ival = (td_gcd*10**9/sdiv)
      def get_error(elmult):
         tcs = round(10**9/sdiv/elmult)
         oval = (tcs*round(td_gcd*elmult))
         return abs(ival - oval)/ival
      
      # Reference ival is (10**9/sdiv*td_gcd). Our task is to split the factor into tcs and elmult. Absolute rounding errors
      # on both sides are limited to 0.5, cumulative error is the product of both errors. To minimize this product, we'd
      # ideally keep both sides as close in magnitude as possible, i.e. close to the sqrt of their product.
      # Or at least that's the behaviour in the td_gcd->inf limit.
      elmult_minerr = ((10**9/sdiv) * td_gcd)**0.5/td_gcd
      
      # Inside an mkv cluster, relative delta between blocks are limited to a total of 2**16-1. So subject to our accuracy
      # limitations, we'll try to aim for small elmults. We don't wanna go below 1/td_gcd though, that's just asking for massive
      # extra inaccuracy we can't reasonably track here.
      elmult_min = 1/td_gcd
      
      # For sufficiently small td_gcd, the advantage of not getting any inaccuracy on one side can outweigh a factor imbalance,
      # so check for this case seperately.
      # PTODO: It would be more accurate to check all integer factors of td_gcd here ... but that's probably overkill.
      if (get_error(elmult_min) <= get_error(elmult_minerr)):
         # Never mind the optimization loop, then - just use this value.
         elmult_minerr = elmult_min
      
      elmult = elmult_min
      delta = (elmult_minerr - elmult_min)/2
      
      if ((delta > 0) and (get_error(elmult) > error_lim)):
         while (delta > 2**-64):
            if (get_error(elmult) < error_lim):
               elmult -= delta
            else:
               elmult += delta
            
            delta /= 2
      
      tcs = round(10**9/sdiv/elmult)
      return (tcs, elmult, get_error(elmult))

   def _track_lacing(self, ttype):
      if (self.lace_mask == 0):
         return False
      if (self.bc_lavf_lacing):
         return (ttype == TRACKTYPE_AUDIO)
      return True

   def _build_track(self, ttype, codec, cid, default_dur, make_cues, ms_cm, track_name, track_lc, flag_default, *args, **kwargs):
      """Build MatroskaElementTrackEntry structure and add to tracks."""
      track_num = len(self.tracks.sub) + 1
      
      try_ms_cm = (ms_cm == self.MS_CM_FORCE)
      if (isinstance(codec, MatroskaCodec)):
         if (try_ms_cm):
            raise ContainerCodecError("Raw matroska codec specification conflicts with ms_cm==MS_CM_FORCE.")
         mkv_codec = codec
      
      elif not (try_ms_cm):
         try:
            mkv_codec = MatroskaCodec.build_from_id(codec)
         except KeyError as exc:
            if not (ms_cm):
               raise ContainerCodecError("Can't natively encapsulate {0} data into MKV, and wasn't supposed to try MS compatibility mode.".format(codec)) from exc
            
            try_ms_cm = True
      
      if (try_ms_cm):
         try:
            ms_cm_cls = self.MS_CM_CLS_MAP[ttype]
         except KeyError as exc:
            raise ContainerCodecError("MS compatibility mode for track type {0} is currently not supported.")
         
         ms_header = ms_cm_cls(codec, *args, **kwargs)
         cid2 = ms_header.get_bindata()
         if not (cid is None):
            cid2 += cid
         cid = cid2
         
         mkv_codec = MatroskaCodec.CODEC_ID2MKV[ms_cm_cls.WRAP_CODEC_ID]
      
      lacing = self._track_lacing(ttype)
      
      sub_els = [
         MatroskaElementTrackNumber.new(track_num),
         MatroskaElementTrackUID.new(track_num),
         MatroskaElementTrackType.new(ttype),
         MatroskaElementFlagLacing.new(lacing),
         MatroskaElementCodec.new(mkv_codec),
         MatroskaElementFlagDefault.new(flag_default)
      ]
      if not (cid is None):
         sub_els.append(MatroskaElementCodecPrivate.new(cid))
      
      if not (default_dur is None):
         sub_els.append(MatroskaElementDefaultDuration.new(default_dur))
      
      if not (track_name is None):
         sub_els.append(MatroskaElementName.new(track_name))
      
      if not (track_lc is None):
         sub_els.append(MatroskaElementLang.new(track_lc))
      
      if (ttype in self.settings_map):
         settings_cls = self.settings_map[ttype]
         sub_els.append(settings_cls.new(*args, **kwargs))
      
      te = MatroskaElementTrackEntry.new(sub_els)
      te._make_cues = make_cues
      te._lacing = lacing
      self.tracks.sub.append(te)
      return (track_num, te)
   
   def _add_track_data(self, track_num, data):
      tv_prev = None
      for (tv, dur, data_r, is_keyframe) in data:
         if ((is_keyframe) or (tv_prev is None)):
            tc_dependencies = ()
         else:
            tc_dependencies = (tv_prev-tv,)
         
         frame = self._add_frame(track_num, tv, 0, tc_dependencies, data_r, dur)
         tv_prev = tv
   
   def add_track_by_entry(self, data, te, make_cues):
      """Add track specified by existing MatroskaElementTrackEntry to MKV structure.
      
         Note that the track number element of te - if present already - will be overwritten."""
      track_num = len(self.tracks.sub) + 1
      
      te.set_sub(MatroskaElementTrackNumber.new(track_num))
      te._make_cues = make_cues

      lacing = self._track_lacing(te.get_track_type())
      te._lacing = lacing
      te.set_sub(MatroskaElementFlagLacing.new(lacing))
      
      self.tracks.sub.append(te)
      self._add_track_data(track_num, data)
   
   def add_track(self, data, ttype, codec, codec_init_data, make_cues, *args, default_dur=None, ms_cm=MS_CM_AUTO,
         track_name=None, track_lang=None, flag_default=True, **kwargs):
      """Add track to MKV structure."""
      (track_num, track_entry) = self._build_track(ttype, codec, codec_init_data, default_dur, make_cues, ms_cm, track_name, track_lang, flag_default, *args, **kwargs)
      self._add_track_data(track_num, data)
   
   def sort_tracks(self):
      """Sort our tracks by type number, updating any block references."""
      tn_map = self.tracks.sort_tracks()
      frames_new = {}
      for (tn_old,f) in self.frames.items():
         frames_new[tn_map[tn_old]] = f
      
      self.frames = frames_new
   
   def write_to_file(self, f):
      (clusters, cues) = self._build_clusters()
      cue_tvs = sorted(cues.keys())
      # Void elements have a minimum size of two. We insert one here because if we didn't, we might end up with exactly
      # one byte of empty space after the cues size adjustment, and would be unable to fill it with a seperate void element
      # otherwise.
      cues = MatroskaElementCues.new([cues[tv] for tv in cue_tvs] + [MatroskaElementVoid.new_by_size(2)])
      seg_sub = [self.mkv_info, self.tracks, cues]
      seg_sub.extend(clusters)
      seg = MatroskaElementSegment.new(seg_sub)
      
      ctx = _OutputCtx(f)
      self.ebml_hdr.write_to_file(ctx)
      clust_offs = {}
      
      def cc(clust, off):
         clust_offs[id(clust)] = off
      
      cue_off = None
      def ccues(off):
         nonlocal cue_off
         cue_off = off
      
      ctx.callback_cluster = cc
      ctx.callback_cues = ccues
      
      cues_sz_base = cues.get_size()
      seg.write_to_file(ctx)
      cues.remove_subvals_by_cls(MatroskaElementVoid)
      
      ctx.callback_cluster = None
      ctx.callback_cues = None
      
      cues.update_ccps(clust_offs)
      
      f.seek(cue_off)
      l = cues.write_to_file(ctx)
      cues_sz = cues.get_size()
      if (cues_sz > cues_sz_base):
         raise MatroskaError("Cues size guessing failed. :( This shouldn't happen, and indicates a bug in yavdlt.")
      if (cues_sz < cues_sz_base):
         f.seek(cue_off+cues_sz)
         void = MatroskaElementVoid.new_by_size(cues_sz_base - cues_sz)
         void.write_to_file(ctx)

# ---------------------------------------------------------------- public module-level functions
def make_mkvb_from_file(f):
   els = ebml_ns_mkv.build_seq_from_file(f)
   for el in els:
      if (isinstance(el, MatroskaElementSegment)):
         break
   else:
      raise ValueError('No segment in MKV file; got {0!a}.'.format(els))
   
   return el.make_mkvb()

# ---------------------------------------------------------------- Test code
def _dump_elements(seq, depth=0):
   for element in seq:
      print('{0}{1:f}'.format(' '*depth, element))
      
      if (hasattr(element, 'sub') and (not isinstance(element, (
            MatroskaElementSeekHead,
            MatroskaElementCluster,
            MatroskaElementCues
         )))):
            _dump_elements(element.sub, depth+1)

def _main():
   """Run module selftests."""
   from sys import argv
   for bval in (b'\x1A\x45\xDF\xA3', b'\x42\x86', b'_\xbf', b'\x1b\x53\x86\x67', b'\x80'):
      for VI in (MatroskaVInt, MatroskaSVInt):
         try:
            (ival,ival_sz) = VI.build_from_bindata(bval)
            bval2 = ival.get_bindata()
         except Exception as exc:
            raise Exception('Failed {0} testcase for {1} ({2},{3}).'.format(VI.__name__, bval, ival, bval2)) from exc
         if (bval != bval2):
            raise Exception('Failed {0} testcase for {1} ({2},{3}).'.format(VI.__name__, bval, ival, bval2))
   
   fn = argv[1]
   f = open(fn, 'rb')
   els = ebml_ns_mkv.build_seq_from_file(f)
   _dump_elements(els)
   f.seek(0)
   mb = make_mkvb_from_file(f)
   mb.set_writingapp('mcio_matroska self-test code, pre-versioning version')
   mb.write_to_file(open(b'__mkvdump.mkv.tmp', 'wb'))
   

if (__name__ == '__main__'):
   _main()
