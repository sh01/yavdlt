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

# Media container I/O: Matroska format

import datetime
import math
import random
import struct
import time

from mcio_base import *

class MatroskaBaseError(Exception):
   pass

class EBMLError(MatroskaBaseError):
   pass

class MatroskaError(MatroskaBaseError):
   pass

def _calc_vint_size(i):
   payload_len = (i+1).bit_length()
   rv = 1
   while ((rv*8 - rv) < payload_len):
      rv += 1
   return rv

class EBMLVInt(int):
   lt_sbit = (None,) + tuple(int(math.log(i,2)) for i in range(1,2**8-1))
   lt_prefix_reserved = tuple(not bool(math.log(i+1,2) % 1) for i in range(0,2**8))
   
   def __init__(self, x):
      self.size = _calc_vint_size(x)
   
   def get_bindata(self):
      """Return binary string representing this VInt."""
      rv = bytearray(self.size)
      prefix_bytes = (self.size-1) // 8
      prefix_bits = (self.size-1) % 8
      
      mult = 1 << (8*(len(rv)-prefix_bytes-1))
      val = int(self)
      
      for i in range(prefix_bytes, len(rv)):
         rv[i] = val // mult
         val %= mult
         mult >>= 8
      
      rv[prefix_bytes] |= (1 << (7-prefix_bits))
      return rv
   
   def write_to_file(self, f):
      bd = self.get_bindata()
      return f.write(bd)
   
   def new(cls, *args, **kwargs):
      return cls(self.type, *args, **kwargs)
   
   @classmethod
   def build_from_bindata(cls, bd):
      idx = 0
      l = 0
      while (bd[idx] == 0):
         l += 8
         idx += 1
      
      sbits = cls.lt_sbit[bd[idx]]
      prefix_val = bd[idx] & (2**sbits-1)
      l += 7-sbits
      
      bid = bd[idx+1:l+1]
      bd[idx+l]
      
      # Test for reserved val
      if ((cls.lt_prefix_reserved[bd[idx]]) and (bid == (b'\xFF'*l))):
         raise EBMLError('Reserved int values are currently unimplemented.')
      
      # Can't reverse it directly, since it might be a memoryview object
      mult = 1
      ival = 0
      for i in reversed(range(len(bid))):
         ival += bid[i]*mult
         mult <<= 8
      
      ival += prefix_val*mult
      return (cls(ival), l+1)
   
   @classmethod
   def build_from_file(cls, f):
      bs = 8
      off = f.seek(0,1)
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
         
class MatroskaVInt(EBMLVInt):
   # This is a direct consequence of matroska vints being limited to 8 bytes total length.
   val_lim = 2**56-1
   
   def __init__(self, x):
      super().__init__(x)
      if (x >= self.val_lim):
         raise MatroskaError('VInt val {0} outside of defined domain.'.format(x))

class EBMLElement:
   cls_map = {}
   cls_build_default = None
   vint_type = EBMLVInt
   
   def __init__(self, etype):
      if ((not hasattr(self, 'type')) or (self.type != etype)):
         self.type = etype
   
   def __format__(self, s):
      return '{0}({1})'.format(type(self).__name__,(self.type))
   
   def write_to_file(self, f):
      raise EBMLError('Unrecognized element of type {0}; unable to dump.'.format(self.type))

   def _write_header(self, f, size):
      size = MatroskaVInt(size)
      return self.type.write_to_file(f) + size.write_to_file(f)

   @classmethod
   def _etype2cls(cls, etype):
      try:
         cls = cls.cls_map[etype]
      except KeyError:
         pass
      return cls
   
   @classmethod
   def _build_from_file(cls, etype, body_size, f):
      return cls(etype)
   
   @classmethod
   def build_from_file(cls, f):
      bufsize = 8
      off = f.seek(0,1)
      (etype, sz_et) = cls.vint_type.build_from_file(f)
      off += sz_et
      f.seek(off)
      (size, sz_sz) = cls.vint_type.build_from_file(f)
      off += sz_sz
      f.seek(off)
      cls_c = cls._etype2cls(etype)
      return (cls_c._build_from_file(etype, size, f), sz_sz+sz_et+size)

   @classmethod
   def build_seq_from_file(cls, f, size_lim=None):
      off = f.seek(0,1)
      rv = []
      if (size_lim is None):
         off_lim = f.seek(0,2)
      else:
         off_lim = off + size_lim
      
      while (off < off_lim):
         f.seek(off)
         (el, size) = cls.build_from_file(f)
         rv.append(el)
         off += size
      
      if (off != off_lim):
         raise EBMLError('Element size / filesize mismatch.')
      return rv

def _ebml_type_reg(cls):
   EBMLElement.cls_map[cls.type] = cls
   return cls

class MatroskaElement(EBMLElement):
   cls_map_m = {}
   vint_type = MatroskaVInt

   @classmethod
   def new(cls, *args, **kwargs):
      return cls(cls.type, *args, **kwargs)

   @classmethod
   def _etype2cls(cls, etype):
      try:
         cls = cls.cls_map[etype]
      except KeyError:
         try:
            cls = cls.cls_map_m[etype]
         except KeyError:
            pass
      return cls


def _mkv_type_reg(cls):
   MatroskaElement.cls_map_m[cls.type] = cls
   return cls


class MatroskaElementMaster(MatroskaElement):
   __slots__ = ('type', 'sub')
   def __init__(self, etype, sub):
      super().__init__(etype)
      self.sub = sub

   def write_to_file(self, f):
      self._write_header(f, MatroskaVInt(sum(c.get_size() for c in self.sub)))
      for c in self.sub:
         c.write_to_file(f)

   def get_size(self):
      bd_size = MatroskaVInt(sum(c.get_size() for c in self.sub))
      return (self.type.size + bd_size.size + bd_size)

   @classmethod
   def _build_from_file(cls, etype, body_size, f):
      sub = MatroskaElement.build_seq_from_file(f, body_size)
      return cls(etype, sub)

@_ebml_type_reg
class EBMLHeader(MatroskaElementMaster):
   type = EBMLVInt(172351395)

class MatroskaElementBinary(MatroskaElement):
   __slots__ = ('type', 'data_r')
   def __init__(self, etype, data_r):
      super().__init__(etype)
      self.data_r = data_r
   
   def __format__(self, fs):
      return '<{0} {1}>'.format(self.__class__.__name__, self.data_r)

   def get_size(self):
      bd_size = self.data_r.get_size()
      return (self.type.size + MatroskaVInt(bd_size).size + bd_size)

   def write_to_file(self, f):
      bd = self.data_r.get_data()
      self._write_header(f, MatroskaVInt(len(bd)))
      rv = f.write(bd)
      if (rv != len(bd)):
         raise IOError()
      return rv
      
   @classmethod
   def new(cls, data, *args, **kwargs):
      if (isinstance(data, bytes)):
         data = DataRefBytes(data)
      
      return cls(cls.type, data, *args, **kwargs)

   @classmethod
   def _build_from_file(cls, etype, body_size, f):
      data_r = DataRefFile(f, f.seek(0,1), body_size)
      return cls(etype, data_r)


class MatroskaElementBaseNum(MatroskaElement):
   __slots__ = ('type', 'val')
   def __init__(self, etype, val, body_size=None):
      super().__init__(etype)
      self.val = val

   def write_to_file(self, f, _val=None):
      if (_val is None):
         _val = self.val
      
      bd_len = self._get_body_size()
      body_data = struct.pack(self._get_bfmt(bd_len), _val)
      rv = self._write_header(f, bd_len)
      rv += f.write(body_data[-1*bd_len:])
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
   def _get_data_from_file(cls, etype, body_size, f):
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
      return (cls, etype, val, body_size)
   
   @classmethod
   def _adjust_padding(cls, buf, pad_sz):
      return
   
   def __format__(self, fs):
      return '<{0} {1}>'.format(self.__class__.__name__, self.val)

class MatroskaElementUInt(MatroskaElementBaseNum):
   bfmt = '>Q'
   def _get_body_size(self):
      return math.ceil(self.val.bit_length()/8)

class MatroskaElementSInt(MatroskaElementBaseNum):
   bfmt = '>q'
   def _get_body_size(self):
      return math.ceil(((self.val + (self.val < 0)).bit_length() + 1)/8)
   
   @classmethod
   def _adjust_padding(cls, buf, pad_sz):
      if not (buf[pad_sz] & 128):
         return
      buf[:pad_sz] = b'\xFF'*pad_sz

class MatroskaElementFloat(MatroskaElementBaseNum):
   def __init__(self, etype, val, body_size):
      super().__init__(etype, val, body_size)
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
      rv = cls(cls.type, 0)
      rv._val = ts
      return rv
   
   def __format__(self, fs):
      return '<{0} {1}>'.format(self.__class__.__name__, time.strftime('%Y-%m-%d %H:%M:%S.%M', time.gmtime(self._val)))


class MatroskaElementStringBase(MatroskaElement):
   def __init__(self, etype, val):
      super().__init__(etype)
      self.val = val

   def write_to_file(self, f):
      body_data = self.val.encode(self.codec)
      rv = self._write_header(f, MatroskaVInt(len(body_data)))
      rv += f.write(body_data)
      return rv

   def get_size(self):
      bd_size = len(self.val.encode(self.codec))
      return (self.type.size + MatroskaVInt(bd_size).size + bd_size)

   def __format__(self, fs):
      return '<{0} {1!a}>'.format(self.__class__.__name__, self.val)

   @classmethod
   def _build_from_file(cls, etype, body_size, f):
      val = f.read(body_size).decode(cls.codec)
      if (len(val) != body_size):
         raise MatroskaError('Domain read error.')
      return cls(etype, val)


class MatroskaElementStringASCII(MatroskaElementStringBase):
   codec = 'ascii'

class MatroskaElementStringUTF8(MatroskaElementStringBase):
   codec = 'utf-8'


# ---------------------------------------------------------------- Master elements
@_mkv_type_reg
class MatroskaElementSignatureSlot(MatroskaElementMaster):
   type = EBMLVInt(190023271)

@_mkv_type_reg
class MatroskaElementSegment(MatroskaElementMaster):
   type = EBMLVInt(139690087)

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
      self = cls(cls.type, [MatroskaElementTimecode.new(timecode)])
      self._tc = timecode
      return self
   
   def _get_tc(self):
      return self._tc
      

@_mkv_type_reg
class MatroskaElementSilentTracks(MatroskaElementMaster):
   type = EBMLVInt(6228)

@_mkv_type_reg
class MatroskaElementBlockGroup(MatroskaElementMaster):
   type = EBMLVInt(32)

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

@_mkv_type_reg
class MatroskaElementTrackEntry(MatroskaElementMaster):
   type = EBMLVInt(46)

@_mkv_type_reg
class MatroskaElementTrackTranslate(MatroskaElementMaster):
   type = EBMLVInt(9764)

@_mkv_type_reg
class MatroskaElementVideo(MatroskaElementMaster):
   type = EBMLVInt(96)
   @classmethod
   def new(cls, width, height):
      sub = [
         MatroskaElementPixelWidth.new(width),
         MatroskaElementPixelHeight.new(height)
      ]
      return cls(cls.type, sub)

@_mkv_type_reg
class MatroskaElementAudio(MatroskaElementMaster):
   type = EBMLVInt(97)
   @classmethod
   def new(cls, sfreq, channels=2):
      sub = [
         MatroskaElementSamplingFrequency.new(sfreq,8),
         MatroskaElementChannels.new(channels)
      ]
      return cls(cls.type, sub)

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

@_mkv_type_reg
class MatroskaElementCuePoint(MatroskaElementMaster):
   type = EBMLVInt(59)

@_mkv_type_reg
class MatroskaElementCueTrackPositions(MatroskaElementMaster):
   type = EBMLVInt(55)

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

@_mkv_type_reg
class MatroskaElementSegmentUID(MatroskaElementBinary):
   type = EBMLVInt(13220)

@_mkv_type_reg
class MatroskaElementBlock(MatroskaElementBinary):
   type = EBMLVInt(33)

@_mkv_type_reg
class MatroskaElementSimpleBlock(MatroskaElementBinary):
   type = EBMLVInt(35)

class MatroskaElementCodecPrivate(MatroskaElementBinary):
   type = EBMLVInt(9122)

class MatroskaElementBlock_r(MatroskaElement):
   _bfmt_subhdr = '>hB'
   _bfmt_subhdr_len = struct.calcsize(_bfmt_subhdr)
   def __init__(self, etype, tracknum, timecode, flags, data_r):
      super().__init__(etype)
      
      struct.pack(self._bfmt_subhdr, timecode, flags)
      self.tracknum = MatroskaVInt(tracknum)
      self.timecode = timecode
      self.flags = flags
      self.data_r = data_r
      
   @classmethod
   def new(cls, *args, **kwargs):
      return cls(MatroskaElementBlock.type, *args, **kwargs)
   
   @classmethod
   def new_simple(cls, *args, **kwargs):
      return cls(MatroskaElementSimpleBlock.type, *args, **kwargs)
      
   def get_size(self):
      bd_size = self._bfmt_subhdr_len + self.tracknum.size + self.data_r.get_size()
      return (self.type.size + MatroskaVInt(bd_size).size + bd_size)

   def write_to_file(self, f):
      bd = self.tracknum.get_bindata() + struct.pack(self._bfmt_subhdr, self.timecode, self.flags) + \
         self.data_r.get_data()
      self._write_header(f, MatroskaVInt(len(bd)))
      rv = f.write(bd)
      if (rv != len(bd)):
         raise IOError()
      return rv


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
class MatroskaElementTimecodeScale(MatroskaElementUInt):
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
   type = EBMLVInt(31)

@_mkv_type_reg
class MatroskaElementBlockDuration(MatroskaElementUInt):
   type = EBMLVInt(27)

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
class MatroskaElementWritingApp(MatroskaElementStringASCII):
   type = EBMLVInt(5953)

@_mkv_type_reg
class MatroskaElementTitle(MatroskaElementStringUTF8):
   type = EBMLVInt(15273)

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


class MatroskaBuilder:
   settings_map = {
      TRACKTYPE_VIDEO: MatroskaElementVideo,
      TRACKTYPE_AUDIO: MatroskaElementAudio
   }
   tcs_error_lim_default = 0.0001
   TLEN_CLUSTER = 2**16
   TOFF_CLUSTER = 2**15
   
   def __init__(self, write_app, tcs, ts=None):
      self.ebml_hdr = EBMLHeader.new([
         EBMLElementDocType.new('matroska'),
         EBMLElementDocTypeVersion.new(2), 
         EBMLElementDocTypeVersion.new(2)
      ])
      
      if (ts is None):
         ts = time.time()
      
      self.mkv_info = MatroskaElementInfo.new([
         MatroskaElementSegmentUID.new(DataRefBytes(_make_random_uid())),
         MatroskaElementTimecodeScale.new(tcs),
         MatroskaElementDateUTC.new(ts),
         MatroskaElementMuxingApp.new(self._get_muxapp()),
         MatroskaElementWritingApp.new(write_app)
      ])
      self.tcs = tcs
      self.tracks = MatroskaElementTracks.new([])
      self.clusters = []
      #self.mkv_seg = MatroskaElementSegment.new([self.mkv_info])
      
   def _get_muxapp(self):
      return 'yt_getter.mcio_matroska pre-versioning-version'
   
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

   def _build_track(self, ttype, codec, cid, *args, **kwargs):
      """Build MatroskaElementTrackEntry structure and add to tracks."""
      track_num = len(self.tracks.sub) + 1
      
      sub_els = [
         MatroskaElementTrackNumber.new(track_num),
         MatroskaElementTrackUID.new(track_num),
         MatroskaElementTrackType.new(ttype),
         MatroskaElementCodec.new(codec)
      ]
      if not (cid is None):
         sub_els.append(MatroskaElementCodecPrivate.new(cid))
      
      if (ttype in self.settings_map):
         settings_cls = self.settings_map[ttype]
         sub_els.append(settings_cls.new(*args, **kwargs))
      
      te = MatroskaElementTrackEntry.new(sub_els)
      self.tracks.sub.append(te)
      return (track_num, te)
   
   def _get_cluster(self, tv):
      """Return cluster for specific timeval."""
      idx = (tv // self.TLEN_CLUSTER)
      tv_base = len(self.clusters)*self.TLEN_CLUSTER
      while (idx >= len(self.clusters)):
         self.clusters.append(MatroskaElementCluster.new(tv_base + self.TOFF_CLUSTER))
         tv_base += self.TLEN_CLUSTER
      
      return self.clusters[idx]
   
   def add_track(self, data, ttype, codec, codec_init_data, *args, **kwargs):
      """Add track to MKV structure."""
      (track_num, track_entry) = self._build_track(ttype, codec, codec_init_data, *args, **kwargs)
      tv_base = None
      for (tv, data_r, is_keyframe) in data:
         clust = self._get_cluster(tv)
         tv_rel = (tv - clust._tc)
         flags = (is_keyframe << 7)
         sblock = MatroskaElementBlock_r.new_simple(track_num, tv_rel, flags, data_r)
         clust.sub.append(sblock)
    
   def write_to_file(self, f):
      seg = MatroskaElementSegment.new([self.mkv_info, self.tracks] + self.clusters)
      self.ebml_hdr.write_to_file(f)
      seg.write_to_file(f)       


# ---------------------------------------------------------------- Test code
def _dump_elements(seq, depth=0):
   for element in seq:
      print('{0}{1:f}'.format(' '*depth, element))
      
      if (hasattr(element, 'sub') and (not isinstance(element, (
            MatroskaElementSeekHead,
            #MatroskaElementCluster,
            MatroskaElementCues
         )))):
            _dump_elements(element.sub, depth+1)


def _main():
   """Run module selftests."""
   from sys import argv
   for bval in (b'\x1A\x45\xDF\xA3', b'\x42\x86', b'\x1b\x53\x86\x67', b'\x80'):
      (ival,ival_sz) = MatroskaVInt.build_from_bindata(bval)
      bval2 = ival.get_bindata()
      if (bval != bval2):
         raise Exception('Failed VInt testcase for {0} ({1},{2}).'.format(bval, ival, bval2))
   
   fn = argv[1]
   f = open(fn, 'rb')
   els = MatroskaElement.build_seq_from_file(f)
   _dump_elements(els)


if (__name__ == '__main__'):
   _main()