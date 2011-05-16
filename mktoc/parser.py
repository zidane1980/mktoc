#  Copyright 2008, Patrick C. McGinty

#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
   This module provides object(s) to parse text files describing the layout of
   an audio CD. After the parse step is complete, it is possible to access the
   data or convert into any other output format.

   The following are a list of the classes provided in this module:

   * :class:`ParseData`
   * :class:`CueParser`
   * :class:`WavParser`
"""

__date__    = '$Date$'
__version__ = '$Revision$'

import os
import re
import logging
import itertools as itr
import operator  as op

from mktoc.base import *
from mktoc import disc as mt_disc
from mktoc import wav  as mt_wav
from mktoc import progress_bar as mt_pb

__all__ = ['CueParser','WavParser']

log = logging.getLogger('mktoc.parser')


##############################################################################
class ParseData(object):
   """
   Stores parsed CD-ROM data and provides methods for modifcation and access.

   Automatically generated by invoking the :meth:`parse` method defined in one
   of the :class:`_Parser` classes.
   """

   # Disc object that stores global disc info.
   _disc    = None

   # Track object that stores track info.
   _tracks  = None

   # In-order list of WAV files that apply to the CD audio.
   _files   = None

   def __init__(self, disc, tracks, files):
      """
      Initialize data structures. The input data is post processed by call the
      :meth:`mung` method on each object.

      :param disc: CD info data object
      :type  disc: :class:`disc.Disc`

      :param tracks: a lost of objects with track info and indexes for each
                     portion of the track.
      :type  tracks: :func:`list` of class:`disc.Track`

      :param files:  in-order list of WAV files associated with 'tracks'
      :type  files:  :func:`list` of file name str\s
      """
      self._disc     = disc
      self._tracks   = tracks
      self._files    = files
      # modify data to workable formats
      self._disc.mung()
      # current track and "next" track or None
      map(lambda t1,t2: t1.mung(t2), # update values in each track
          self._tracks, itr.islice(self._tracks, 1, None))

   def getToc(self):
      """
      Access method to return a text stream of the CUE data in TOC format.
      """
      toc = []
      toc.extend( str(self._disc).split('\n') )
      for trk in self._tracks:
         toc.extend( str(trk).split('\n') )
      # expand tabs to 4 spaces, strip trailing white space on each line
      toc = [line.expandtabs(4).rstrip() for line in toc]
      return toc

   def modWavOffset(self,samples,tmp=False):
      """
      Optional method to correct the audio WAV data by shifting the samples by
      a positive or negative offset.

      This can be used to compensate for a write offset in a CD/DVD burner. If
      the `tmp` variable is :data:`True`, all new WAV files will be created in
      the :file;`/tmp` directory.

      :param samples:   Number of samples to shift the audio data by. This
                        value can be negative or positive.
      :type  samples:   int

      :param tmp:    :data:`True` or :data:`False`; when :data:`True` any
                     new WAV files will be created in :file:`/tmp`.
      :type tmp:     bool
      """
      # create WavOffset object, initialize sample offset and progress output
      wo = mt_wav.WavOffsetWriter( samples, mt_pb.ProgressBar,
                                   ('processing WAV files:',))
      new_files = wo.execute( self._files, tmp )

      # change all index file names to newly generated files
      file_map = dict( zip(self._files,new_files) )
      indexes = itr.imap(op.attrgetter('indexes'), self._tracks);
      for idx in itr.chain(*indexes):
         log.debug( "updating index file '%s'", idx.file_ )
         idx.file_ = file_map[idx.file_]


##############################################################################
class _Parser(object):
   """
   A generic CD TOC parsing class.

   This class provides a foundation of public and private methods to access and
   modify an audio CD track listing.
   """

   # Dictionary to map input WAV files to actual files on the system. The map
   # is for use in cases where the defined file name does not exactly match the
   # file system WAV name.
   _file_map         = None

   # In-order list of WAV files that apply to the CD audio.
   _files            = None

   # True or flase, when True the WAV file must be found in the FS or an
   # exception is raised.
   _find_wav         = None

   # WavFileCache object that can quickly find WAV files in the local file
   # system.
   _wav_file_cache   = None

   def __init__(self, work_dir=os.curdir, find_wav=True):
      """
      Parses CUE file text data and initializes object data. The primary output
      of this function is to create the '_disc' and '_tracks' objects.

      All of the processed CUE data is stored in these two structures.

      Parameters:
         find_wav    : True/False, True causes exceptions to be raised
                       if a WAV file can not be found in the FS.

         work_dir    : Path location of the working directory.
      """
      # init class options
      self._file_map       = {}
      self._files          = []
      self._find_wav       = find_wav
      assert(work_dir)
      self._wav_file_cache = mt_wav.WavFileCache(work_dir)

   def parse( self, *args, **kwargs ):
      """
      Execute parsing steps on the input data. Must be implemented by all
      subclasses.

      Returns a correctly formed :class:`ParseData` instance.
      """
      abstract

   def _lookup_file_name(self,file_):
      """
      Attempts to return the path to a valid WAV file in the files
      system using the input 'file_' value.

      If the WAV file can not be found and '_find_wav' is True, then an
      exception is raised.

      Parameter:
         file  : audio file name parsed from the CUE text.
      """
      if file_ in self._file_map:
         return self._file_map[file_]
      else:
         try:  # attempt to find the WAV file
            file_on_disk = self._wav_file_cache.lookup(file_)
         except FileNotFoundError:
            # file not found, but 'file_exists' indicates that the file
            # must exists
            if self._find_wav: raise
            else: file_on_disk = file_
         self._file_map[file_] = file_on_disk
         return file_on_disk


##############################################################################
class CueParser(_Parser):
   """
   An audio CUE sheet text file parsing class.

   By matching the known format of a CUE file, the relevant text information is
   extracted and converted to a binary representation. The binary
   representation is created by using combination of Disc, Track, and
   TrackIndex objects. With the data, the CUE file can be re-created or
   converted into a new format.
   """

   # The following constants contain all of the pattern matching expressions
   # for the CUE file parsing steps. The patterns are combined and applied
   # depending on the current step of the scanning process.

   # regex pattern used for matching WAV files
   _FILE_REGEX  = [
      ('file',  r"""
         ^\s*FILE       # FILE
         \s+"(.*)"      # 'file name' in quotes
         \s+WAVE$       # WAVE
      """ )]

   # regex pattern for track parsing
   _TRACK_REGEX = [
      ('track',  r"""
         ^\s*TRACK                  # TRACK
         \s+(\d+)                   # track 'number'
         \s+(AUDIO|MODE.*)$         # AUDIO or MODEx/xxxx
      """)]

   # regex pattern for disc info
   _DISC_REGEX = [
      ('rem' , r"""
         ^\s*REM           # match 'REM'
         \s+(\w+)          # match 'key'
         \s+(.*)$          # match 'value'
      """),
      ('quote', r"""
         ^\s*(\w+)         # match 'key'
         \s+"(.*)"$        # match 'value' surrounded with double quotes
      """),
      ('catalog', r"""
         ^\s*(CATALOG)     # CATALOG
         \s+(\d{13})$      # catalog 'value'
      """)]

   # regex patterns for associated Track info.
   _TINFO_REGEX = [
      ('index', r"""
         ^\s*INDEX                  # INDEX
         \s+(\d+)                   # 'index number'
         \s+(\d{2}:\d{2}:\d{2})$    # 'index time'
      """),
      ('quote', r"""
         ^\s*(PERFORMER|TITLE)      # 'key'
         \s+"(.*)"$                 # 'value' surrounded with double quotes
      """),
      ('named', r"""
         ^\s*(ISRC|PREGAP)          # a known CUE command
         \s+(.*)$                   # single arg
      """),
      ('flag', r"""
         ^\s*FLAGS               # a FLAG command
         \s+(.*)$                # one or more flags
      """)]

   # list of processed CUE text data. The processing step removes text comments
   # and strips white spaces.
   _cue = None

   # list of CUE file line numbers and WAV files tuple pairs for each WAV file
   # in the CUE.
   _file_lines = None

   # list used as a lookup table, indexed by track number, to map each CUE
   # track to its line number in the CUE text.
   _track_lines = None

   # RegexStore list of regex searches for first partial scan of the TOC text.
   _part_search = None

   # RegexStore list of regex searches for disc info scan of the TOC.
   _disc_search = None

   # RegexStore list of regex searches for track info scan of the TOC.
   _tinfo_search = None

   def __init__(self, cue_dir=os.curdir, find_wav=True):
      """
      Initializes object data.

      :param cue_dir:   Path location of the CUE file's directory.
      :type cue_dir:    str

      :param find_wav:  :data:`True`/:data:`False`, :data:`True` causes
                        exceptions to be raised if a WAV file can not be found
                        in the FS.
      :type find_wav:   bool
      """
      assert(cue_dir)
      super(CueParser,self).__init__(cue_dir, find_wav)
      self._part_search  = _RegexStore( dict(self._FILE_REGEX + \
                                            self._TRACK_REGEX) )
      self._disc_search  = _RegexStore( dict(self._FILE_REGEX + \
                                            self._DISC_REGEX) )
      self._tinfo_search = _RegexStore( dict(self._FILE_REGEX + \
                                        self._TINFO_REGEX + self._TRACK_REGEX))

   def parse(self, fh):
      """
      Parses CUE file text data and return a :class:`ParseData` instance.

      The primary output of this function is to initialize the 'disc' and
      'tracks' member variables of :class:`ParseData`. All of the processed
      CUE data is stored in these two structures.

      :param fh:  An open file handle used to read the CUE text data
      :type fh:   :data:`file`
      """
      # create a list of regular expressions before starting the parse
      rem_regex   = re.compile( r'^\s*REM\s+COMMENT' )
      # parse disc into memory, ignore comments
      self._cue = [line.strip() for line in fh if not rem_regex.search(line)]
      if not len(self._cue):
         raise EmptyCueData
      self._build_lookup_tbl()
      # create data objects for CUE info
      disc = self._parse_disc()
      return ParseData( disc, self._parse_all_tracks(disc), self._files )

   def _active_file(self,trk_idx):
      """Returns the previous WAV file used before the start of 'trk_idx'."""
      tline = self._track_lines[trk_idx] # line number track begins at
      # return the first wav file found that is at a lower line than 'tline'
      return itr.ifilter(lambda (x,y): x < tline,
                         reversed(self._file_lines)).next()[1]

   def _build_lookup_tbl(self):
      """Helper function to create the '_files', '_file_lines' and
      '_track_lines' lists structures required before the class initialization
      is complete."""
      # return an iterator of tuples with line nums, re.match name, and
      # re.match data
      matchi = itr.chain(*itr.imap( self._part_search.match, self._cue ))
      num_matchi = itr.izip( itr.count(), matchi, matchi )
      # create list of valid matches
      matches = filter(op.itemgetter(2), num_matchi)
      # iterator of 'file' matches
      files = filter(lambda (i,key,match): key == 'file', matches)
      # create a list of 'wav file name'
      self._files = map( lambda m: self._lookup_file_name(m.group(1)),
                         itr.imap(op.itemgetter(2),files) )
      # create a tuple of (i,wav file name)
      self._file_lines = zip( itr.imap(op.itemgetter(0),files), self._files )
      # iterator of 'track' matches
      tracks = itr.ifilter( lambda (i,key,match): key == 'track', matches)
      self._track_lines = map(op.itemgetter(0), tracks)

   def _parse_all_tracks(self,disc):
      """Return a list of Track objects that contain the track information from
      the fully parsed CUE text data."""
      return list( itr.imap( self._parse_track, range(len(self._track_lines)),
                             itr.repeat(disc)) )

   def _parse_disc(self):
      """Return a Disc object that contains the disc information from the fully
      parsed CUE text data. This method implements the 'disc' scanning steps of
      the parser."""
      disc_ = mt_disc.Disc()
      # splice disc data from the cue list, and return an iterator of tuples
      # returned by re.match
      cue_data = map( self._disc_search.match,
                      itr.islice(self._cue, 0, self._track_lines[0]) )
      # raise error if unkown match is found
      if filter( lambda (key,match): not match, cue_data):
         raise ParseError, "Unmatched pattern in stream: '%s'" % txt
      # ignore 'file' matches
      for key,value in \
            [match.groups() for key,match in cue_data if key != 'file']:
         key = key.lower()
         if hasattr(disc_,key):
            # add match value to Disc object
            setattr(disc_, key, value.strip())
         else:
            raise ParseError, "Unmatched keyword in stream: '%s'" % txt
      return disc_

   def _parse_track(self, num, disc):
      """Return a Track object that contains a single track element
      from the parsed CUE text data. This method implements the
      'track' scanning steps of the parser.

      Parameters:
         num   : the track index of the track to parse. The first
                 track starts at 0.
      """
      # splice track data
      if num+1 < len(self._track_lines):
         data = itr.islice(self._cue, self._track_lines[num],
                           self._track_lines[num+1])
      else:
         data = itr.islice(self._cue, self._track_lines[num], None)
      # lookup the previous file name
      file_name = self._active_file(num)

      # <-- This is the main track parsing step --->
      trk = mt_disc.Track(num+1)
      # Every CUE file has list of FILE, TRACK, and INDEX commands. The FILE
      # commands specify the active FILE for the following INDEX commands. The
      # TRACK indicate the logical beginning of a new TRACK info list with TITLE
      # and PERFORMER tags.
      cue_data = map( self._tinfo_search.match, data )
      # raise error if unkown match is found
      if filter( lambda (key,match): not match, cue_data):
         raise ParseError, "Unmatched pattern in stream: '%s'" % txt
      for re_key,match in cue_data:
         if re_key == 'track':
            assert trk.num == int(match.group(1))
            if match.group(2) != 'AUDIO':
               trk.is_data = True
               disc.setMultisession()     # disc is multi-session
         elif re_key == 'file':
            # update file name
            file_name = self._lookup_file_name(match.group(1))
         elif re_key == 'index':
            # track INDEX, file_name is associated with the index
            idx_num,time = match.groups()
            i = mt_disc.TrackIndex( idx_num, time, file_name )
            trk.appendIdx( i )
         elif re_key in ['quote','named']:
            # track information (PERFORMER, TITLE, ...)
            key,value = match.groups()
            key = key.lower()
            if hasattr(trk,key):    # add match value to Disc object
               setattr(trk, key, value.strip())
            else:
               raise ParseError, "Unmatched keyword in stream: '%s'" % txt
         elif re_key == 'flag':
            for f in itr.ifilter( lambda x: x in ['DCP','4CH','PRE'],
                                  match.group(1).split() ):
               if f == '4CH': f = 'four_ch'     # change '4CH' flag name
               setattr(trk, f.lower(), True)
         else: # catch unhandled patterns
            raise ParseError, "Unmatched pattern in stream: '%s'" % txt
      return trk


##############################################################################
class WavParser(_Parser):
   """
   A simple parser object that uses a list of WAV files to create a CD TOC.

   The class assumes that each WAV file is an individual track, in ascending
   order.
   """
   def __init__(self, work_dir=os.curdir, find_wav=True):
      """
      Initialize the parser.


      :param work_dir:  Path location of the CUE file's directory.
      :type work_dir:   str

      :param find_wav:  :data:`True`/:data:`False`, :data:`True` causes
                        exceptions to be raised if a WAV file can not be found
                        in the FS.
      :type find_wav: bool
      """
      # init class options
      assert( work_dir )
      super(WavParser,self).__init__(work_dir, find_wav)

   def parse( self, wav_files):
      """
      Parses a list of WAV files and return a :class:`ParseData` instance.

      The primary output of this function is to initialize the 'disc' and
      'tracks' member variables of :class:`ParseData`.  All of the processed
      CUE data is stored in these two structures.

      :param wav_files: WAV files to add to the TOC
      :type wav_files:  list
      """
      self._files = map(self._lookup_file_name, wav_files)
      # return a new Track object with a single Index using 'file_'
      def mk_track((idx,file_)):
         # create a new track for the WAV file
         trk = mt_disc.Track(idx+1)
         # add the WAV file to the first index in the track
         trk.appendIdx( mt_disc.TrackIndex(1,0,file_) )
         return trk
      # return a new ParseData object with empy Disc and complete Track list
      return ParseData( mt_disc.Disc(),
                        map( mk_track, enumerate(self._files)),
                        self._files )


##############################################################################
class _RegexStore(object):
   """
   A helper class that simplifies the management of regular expressions.

   The RegexStore class is used to apply a list of regular expressions to a
   single text stream. The first matching regular expression is returned.
   """
   # Dictionary of compiled regex's keyed by a user supplied string value.
   _searches = None

   def __init__(self, pat_dict):
      """
      Initialize the '_searches' dictionary using the 'pat_dict' parameter.

      Parameters:
         pat_dict : A dictionary of regular expression strings. The
                    regex value is compiled and stored in the
                    '_searches' dictionary, keyed by the original
                    'pat_dict' key.
      """
      # build RegEx searches
      re_searches = [re.compile(pat, re.VERBOSE) for pat in pat_dict.values()]
      self._searches = dict(zip(pat_dict.keys(),re_searches))

   def match( self, text ):
      """
      Applies the 'text' parameter to a dictionary of regex searches. The
      output of the first matching regex is returned along with the matching
      regex's dictionary key. The return is data is contained in a tuple, with
      the key as the first element.

      Parameters:
         text :   text string applied to a list of regex searches.
      """
      match_all = itr.starmap( lambda key,cre: (key,cre.search(text)),
                               self._searches.items() )
      try:
         return itr.ifilter(op.itemgetter(1), match_all).next()
      except StopIteration, e:
         return ('',None)
