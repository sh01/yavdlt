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

__doc__ = """MCIO CODEC_ID_* constants.

Note that there are no guarantees about these ids remaining constant through
version changes; modules needing to send or interpret these values should
always import the ids from here instead of hardcoding them."""

class _CodecID(int):
   def __new__(cls, x, name):
      rv = int.__new__(cls, x)
      rv.name = name
      return rv
   
   def __repr__(self):
      return '{0}.{1}'.format(_CodecID.__module__, self.name)
   
   def __str__(self):
      return self.name

def __init():
   codecs = (
      # video codecs
      'MPEG1',
      'MPEG2',
      ('MPEG4_2', 'DIVX'),
      ('H264', 'AVC', 'MPEG4_10'),
      'SNOW',
      'THEORA',
      ## flash video stuff
      'FLASHSV', # flash screen video
      'FLV1', # H263 variant: flash video
      'VP6',
      'VP6A',
      'VP8',
      
      # audio codecs
      'AAC',
      'AC3',
      'DTS',
      'FLAC',
      ('MP1', 'MPEG1_1'),
      ('MP2', 'MPEG1_2'),
      ('MP3', 'MPEG1_3'),
      'SPEEX',
      'VORBIS',
      
      # pseudo codecs: MKV
      'MKV_MSC_VFW',
      'MKV_MSC_ACM',
      
      # non-codec codec ids
      '_MAXNUM'
   )
   
   gv = globals()
   
   for (codec_set,i) in zip(codecs,range(1,len(codecs)+1)):
      if (isinstance(codec_set, str)):
         codec_set = (codec_set,)
      
      for cname in codec_set:
         varname = 'CODEC_ID_{0}'.format(cname)
         gv[varname] = _CodecID(i, varname)

__init()
