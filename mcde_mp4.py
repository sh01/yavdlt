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

# Media container data extraction: MP4

import datetime
import struct


_mov_td = (datetime.datetime(1970,1,1) - datetime.datetime(1904,1,1))
_mov_time_offset = -1 * (_mov_td.days*86400 + _mov_td.seconds)
del(_mov_td)

def movts2unixtime(mov_ts):
   """Convert mov TS (seconds since 1904-01-01) to unixtime TS (seconds since 1970-01-01)."""
   return _mov_time_offset + mov_ts

class MovParserError(Exception):
   pass

class MovBoxType:
   pass

class MovBoxTypeInt(MovBoxType, int):
   def __new__(cls, x):
      if (isinstance(x, str)):
         x = x.encode('ascii')
      if (isinstance(x, bytes)):
         (x,) = struct.unpack('>L', x)
      
      return int.__new__(cls, x)
   
   def __format__(self, s):
      rv = struct.pack('>L', self)
      return ascii(rv)

btype_uuid = MovBoxTypeInt(b'uuid')

class MovBoxTypeUUID(MovBoxType, bytes):
   def __new__(cls, x):
      if (isinstance(x, str)):
         x = x.encode('ascii')
      
      if (len(x) != 16):
         raise ValueError('Invalid value {0!a}.'.format(x))
      
      return bytes.__new__(cls, x)

def _make_mbt(x):
   if (isinstance(x,int) or (len(x) == 4)):
      return MovBoxTypeInt(x)
   return MovBoxTypeUUID(x)

class MovBox:
   cls_map = {}
   
   def __init__(self, f, offset, size, hlen, btype):
      self.f = f
      self.hlen = hlen
      self.offset = offset
      self.size = size
      self.type = btype
      self.f.seek(self.offset + self.hlen)
      self._init2()

   def _init2(self):
      pass

   def get_body(self):
      """Return raw body data of this box."""
      self.f.seek(self.offset + self.hlen)
      bodylen = self.size - self.hlen
      data = self.f.read(bodylen)
      if (len(data) != bodylen):
         raise StandardError()
      return data

   def __repr__(self):
      return '{0}({1}, {2}, {3}, {4}({5}))'.format(type(self), self.f, self.offset,
         self.size, self.type, struct.pack('>L', self.type))

   def __format__(self, s):
      if (s != 'f'):
         return repr(self)
      
      if (type(self) == MovBox):
         tstr = '({0})'.format(self.type)
      else:
         tstr = ''
      
      return '<{0}{1}>'.format(type(self).__name__, tstr)

   @classmethod
   def build(cls, f, offset, size, hlen, btype):
      try:
         cls = cls.cls_map[btype]
      except KeyError:
         pass
      
      return cls(f, offset, size, hlen, btype)

   @classmethod
   def build_from_file(cls, f):
      off_start = f.seek(0,1)
      header = f.read(8)
      (size, btype) = struct.unpack('>LL', header)
      btype = MovBoxTypeInt(btype)
      if (size == 1):
         extsz = f.read(8)
         (size,) = struct.unpack('>Q', extsz)
         hlen = 16
      else:
         hlen = 8
         if (size == 0):
            off = f.seek(0,1)
            size = f.seek(0,2) - off
            f.seek(off)
      
      if (btype == btype_uuid):
         btype = MovBoxTypeUUID(f.read(16))
         hlen += 16
      
      return cls.build(f, off_start, size, hlen, btype)
   
   @classmethod
   def build_seq_from_file(cls, f, off_limit=None):
      rv = []
      off = f.seek(0,1)
      if (off_limit is None):
         off_limit = f.seek(0,2)
      
      f.seek(off)
      while ((off < off_limit) and (len(f.read(8)) == 8)):
         f.seek(off)
         atom = cls.build_from_file(f)
         rv.append(atom)
         off += atom.size
         f.seek(off)
      
      return rv

def _mov_box_type_reg(cls):
   MovBox.cls_map[cls.type] = cls
   return cls

class MovFullBox(MovBox):
   def _init2(self):
      super()._init2()
      self.f.seek(self.offset + self.hlen)
      data = self.f.read(4)
      (self.version, self.flags) = struct.unpack('>B3s', data)
      self.hlen += 4
   

@_mov_box_type_reg
class MovBoxMovieHeader(MovFullBox):
   type = _make_mbt('mvhd')
   bfmts = {
      0: '>5LH10x36s7L',
      1: '>QQLQLH10x36s7L'
   }
   
   def _get_bfmt(self):
      try:
         rv = self.bfmts[self.version]
      except KeyError:
         raise MovParserError('Unable to parse ver {0!a}.'.format(self.version))
      return rv
   
   def _init2(self):
      super()._init2()
      (ts_creat, ts_mod, self.time_scale, self.dur, self.rate_p, self.vol_p, self.mat, pv_time, self.pv_dur, self.poster_time,
      self.select_time, self.select_dur, self.cur_time, self.tid_next) = struct.unpack(self._get_bfmt(), self.get_body())
      
      self.ts_creat = movts2unixtime(ts_creat)
      self.ts_mod = movts2unixtime(ts_mod)
      
      print(self.__dict__)


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
   def _init2(self):
      super()._init2()
      data = memoryview(self.get_body())
      i = self.bfmt_len
      entry_data = []
      bfmt_entry_len = struct.calcsize(self.bfmt_entry)
      onetuples = (len(self.bfmt_entry.lstrip('<>!')) == 1)
      
      while (i < len(data)):
         entry_val = struct.unpack(self.bfmt_entry, data[i:i+bfmt_entry_len])
         if (onetuples):
            (entry_val,) = entry_val
         
         entry_data.append(entry_val)   
         i += bfmt_entry_len   
      
      self.entry_data = entry_data

@_mov_box_type_reg
class MovBoxSampleDescription(MovBoxSampledataBase):
   type = _make_mbt('stsd')
   bfmt_entry = '>LL6xH'
   bfmt_entry_len = struct.calcsize(bfmt_entry)
   def _init2(self):
      super()._init2()
      off = self.bfmt_len
      data = memoryview(self.get_body())
      
      entry_data = []
      for i in range(self._elnum):
         (sz, dfmt, dri) = struct.unpack(self.bfmt_entry, data[off:off+self.bfmt_entry_len])
         entry_data.append((dfmt, dri))
      self.entry_data = entry_data

@_mov_box_type_reg
class MovBoxTTS(MovBoxSampleTableBase):
   type = _make_mbt('stts')
   bfmt_entry = '>LL'
   def time2sample(self, dt):
      for (count, dur) in self.data:
         if (count > dt):
            return dur
         dt -= count
      raise ValueError('Invalid time value {0}.'.format(dt))
      

@_mov_box_type_reg
class MovBoxSyncSample(MovBoxSampleTableBase):
   type = _make_mbt('stss')
   bfmt_entry = '>L'

@_mov_box_type_reg
class MovBoxSampleToChunk(MovBoxSampleTableBase):
   type = _make_mbt('stsc')
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
class MovBoxSampleSize(MovBoxSampleTableBase):
   type = _make_mbt('stsz')
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
   
   def get_ss(self, i):
      return (self.sample_size or self.entry_data[i])


class MovBoxChunkOffset(MovBoxSampleTableBase):
   def get_co(self, i):
      return self.entry_data[i]

@_mov_box_type_reg
class MovBoxChunkOffset32(MovBoxChunkOffset):
   type = _make_mbt('stco')
   bfmt_entry = '>L'

@_mov_box_type_reg
class MovBoxChunkOffset64(MovBoxChunkOffset):
   type = _make_mbt('co64')
   bfmt_entry = '>Q'


class MovBoxBranch(MovBox):
   def _init2(self):
      super()._init2()
      self.sub = MovBox.build_seq_from_file(self.f, self.offset + self.size)

   def find_subbox(self, btype):
      """Find a direct subbox of specified boxtype."""
      btype = _make_mbt(btype)
      
      for box in self.sub:
         if (box.type == btype):
            return box
      raise ValueError('No subboxes of type {0}.'.format(btype))
   
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
      return '<{0} ({1}, {2}, {3}) sub: {4}>'.format(type(self), self.f, self.offset,
         self.size, self.sub)
      

class MovFullBoxBranch(MovFullBox, MovBoxBranch):
   pass

@_mov_box_type_reg
class MovBoxMovie(MovBoxBranch):
   type = _make_mbt(b'moov')

@_mov_box_type_reg
class MovBoxUserdata(MovBoxBranch):
   type = _make_mbt(b'udta')

@_mov_box_type_reg
class MovBoxTrack(MovBoxBranch):
   type = _make_mbt(b'trak')
   def _init2(self):
      super()._init2()
      stbl = self.find_subbox(b'mdia').find_subbox(b'minf').find_subbox(b'stbl')
      self.stsd = stbl.find_subbox(b'stsd')
      self.stsc = stbl.find_subbox(b'stsc')
      self.stsz = stbl.find_subbox(b'stsz')
      try:
         self.stco = stbl.find_subbox(b'stco')
      except ValueError:
         self.stco = stbl.find_subbox(b'co64')
   
   def dump_media_data(self, out):
      for (off, sz) in self.get_media_data_offsets():
         self.f.seek(off)
         block = self.f.read(sz)
         if (len(block) != sz):
            raise MovParserError('Unable to read {0} bytes from offset {1} from {2}.'.format(sz, off, self.f))
         out(block)
   
   def get_media_data_offsets(self):
      ss = self.stsz.entry_data
      sc = self.stsc.entry_data_pp
      co = self.stco.entry_data
      
      s = 0
      s_lim = len(ss)
      s_sublim = 0
      c = 0
      c_lim = 0
      cblock = 0
      
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
         
         size = ss[s]
         yield ((s_off, size))
         s_off += size
         s += 1
      

@_mov_box_type_reg
class MovBoxMedia(MovBoxBranch):
   type = _make_mbt(b'mdia')

@_mov_box_type_reg
class MovBoxMeta(MovFullBoxBranch):
   type = _make_mbt(b'meta')

@_mov_box_type_reg
class MovBoxHandlerReference(MovFullBoxBranch):
   type = _make_mbt(b'hdlr')
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
   
   def __format__(self, s):
      if (s != 'f'):
         return super().__format__(s)
      return '<{0} {1} {2}>'.format(type(self).__name__, MovBoxTypeInt(self.handler_type), self.name)
         

@_mov_box_type_reg
class MovBoxMediaInformation(MovBoxBranch):
   type = _make_mbt(b'minf')

@_mov_box_type_reg
class MovBoxDataInformation(MovBoxBranch):
   type = _make_mbt(b'dinf')

@_mov_box_type_reg
class MovBoxDataReference(MovBoxBranch):
   type = _make_mbt(b'dref')
   def _init2(self):
      self.f.seek(self.offset + self.hlen + 4 + 4)
      self.sub = MovBox.build_seq_from_file(self.f, self.offset + self.size)

@_mov_box_type_reg
class MovBoxSampleTable(MovBoxBranch):
   type = _make_mbt(b'stbl')

def _dump_atoms(seq, depth=0):
   for atom in seq:
      print('{0}{1:f}'.format(' '*depth, atom))
      
      if (hasattr(atom, 'sub')):
         _dump_atoms(atom.sub, depth+1)
   

def main():
   import sys
   fn = sys.argv[1]
   f = open(fn, 'rb')
   boxes = MovBox.build_seq_from_file(f)
   _dump_atoms(boxes)
   tracks = boxes[1].find_subboxes('trak')
   i = 0
   for track in tracks:
      f = open('__mp4dump.{0}.tmp'.format(i), 'wb')
      track.dump_media_data(f.write)
      f.close()
      i += 1


if (__name__ == '__main__'):
   main()

