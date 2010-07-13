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

# Media container I/O: Base types

class ContainerParserError(Exception):
   pass

class DataRef:
   pass

class DataRefFile(DataRef):
   __slots__ = ('f', 'off', 'size')
   def __init__(self, f, off, size):
      self.f = f
      self.off = off
      self.size = size
   
   def get_data(self):
      self.f.seek(self.off)
      return self.f.read(self.size)
   
   def get_size(self):
      return self.size
   
   def __format__(self, fs):
      return '{0}{1}'.format(type(self).__name__, (self.f, self.off, self.size))

class DataRefBytes(DataRef, bytes):
   def __init__(self, *args, **kwargs):
      return bytes.__init__(self)
   
   def get_data(self):
      return self
      
   def get_size(self):
      return len(self)
