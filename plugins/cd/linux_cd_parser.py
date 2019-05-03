"""
    This module is a low-level reader and parser for audio CDs.
    It heavily relies on ioctls to the linux kernel.
    
    Original source for the code:
    http://www.carey.geek.nz/code/python-cdrom/cdtoc.py
    
    Source for all the magical constants and more infos on the ioctls:
    linux/include/uapi/linux/cdrom.h
    https://github.com/torvalds/linux/blob/master/include/uapi/linux/cdrom.h
"""


from __future__ import division


import fcntl
import logging
import os
import struct

from xl.main import common
from xl.trax import Track


logger = logging.getLogger(__name__)


def read_cd_index(device):
    """
        Reads a CD's index and parses it to Exaile's trax.
        
        @param device: a path to a CD device
        @return: an array of xl.trax.Track representing the disc's contents
    """
    toc_entries = __read_toc(device)
    return __parse_tracks(toc_entries, device)


def __read_toc(device):
    """
        Does all the I/O work on reading the disc table of contents (TOC)
        
        @param device: a path to a CD device
        @return: Array of toc entries. The last one is a dummy.
    """
    toc_entries = []
    fd = os.open(device, os.O_RDONLY)
    try:
        (start, end) = __read_toc_header(fd)

        # index of the end, i.e. the last toc entry which is an empty dummy
        CDROM_LEADOUT = 0xAA
        for toc_entry_index in range(start, end + 1) + [CDROM_LEADOUT]:
            toc_entry = __read_toc_entry(fd, toc_entry_index)
            toc_entries.append(toc_entry)
    finally:
        os.close(fd)
    return toc_entries


def __read_toc_header(fd):
    """ A wrapper for the `CDROMREADTOCHDR` ioctl """
    
    # struct cdrom_tochdr of 2 times u8
    FORMAT_cdrom_tochdr = 'BB'
    # u8 start: lowest track index (index of first track), probably always 1
    # u8 end: highest track index (index of last track), = number of tracks
    cdrom_tochdr = struct.pack(FORMAT_cdrom_tochdr, 0, 0)
    
    CDROMREADTOCHDR = 0x5305
    cdrom_tochdr = fcntl.ioctl(fd, CDROMREADTOCHDR, cdrom_tochdr)
    
    start, end = struct.unpack(FORMAT_cdrom_tochdr, cdrom_tochdr)
    
    return (start, end)


def __read_toc_entry(fd, toc_entry_num):
    """ A wrapper for the `CDROMREADTOCENTRY` ioctl """
    # value constant: Minute, Second, Frame: binary (not bcd here)
    CDROM_MSF = 0x02
    
    # struct cdrom_tocentry of 3 times u8 followed by an int and another u8 
    FORMAT_cdrom_tocentry = 'BBBiB'
    # u8 cdte_track: Track number. Starts with 1, which is used for the TOC and contains data.
    # u8 cdte_adr_ctrl: 4 high bits -> cdte_ctrl, 4 low bits -> cdte_adr
    # u8 cdte_format: should be CDROM_MSF=0x02 as requested before
    # int cdte_addr: see below
    # u8 cdte_datamode: ??? (ignored)
    cdrom_tocentry = struct.pack(FORMAT_cdrom_tocentry, toc_entry_num, 0, CDROM_MSF, 0, 0)
    
    CDROMREADTOCENTRY = 0x5306
    cdrom_tocentry = fcntl.ioctl(fd, CDROMREADTOCENTRY, cdrom_tocentry)
    
    cdte_track, cdte_adr_ctrl, cdte_format, cdte_addr, cdte_datamode = \
        struct.unpack(FORMAT_cdrom_tocentry, cdrom_tocentry)

    if cdte_format is not CDROM_MSF:
        raise OSError('Invalid syscall answer')

    # unused:
    # cdte_adr = cdte_adr_ctrl & 0x0f  # lower nibble
    
    cdte_ctrl = (cdte_adr_ctrl & 0xF0) >> 4  # higher nibble

    CDROM_DATA_TRACK = 0x04
    # data: `True` if this "track" contains data, `False` if it is audio
    is_data_track = bool(cdte_ctrl & CDROM_DATA_TRACK)
    
    # union cdrom_addr of struct cdrom_msf0 and int
    # struct cdrom_msf0 of 3 times u8 plus padding to match size of int
    FORMAT_cdrom_addr = 'BBB' + 'x' * (struct.calcsize('i') - 3)
    # u8 minute: Minutes from beginning of CD
    # u8 second: Seconds after `minute`
    # u8 frame: Frames after `frame`
    minute, second, frame = struct.unpack(
        FORMAT_cdrom_addr, struct.pack('i', cdte_addr))
    
    return (cdte_track, is_data_track, minute, second, frame)


def __parse_tracks(toc_entries, device):
    """ Parse the data from ioctl into xl.trax.Track """
    real_track_count = len(toc_entries) - 1  # ignore the empty dummy track at the end
    tracks = []
    for toc_entry_index in range(0, real_track_count):
        (track_index, is_data_track, _, _, _) = \
            toc_entries[toc_entry_index]
        if is_data_track:
            continue
        if track_index is not toc_entry_index + 1:
            logger.warn('Unexpected index found. %ith toc entry claims to be track number %i',
                        toc_entry_index, track_index)
        
        length = __calculate_track_length(toc_entries[toc_entry_index],
                                          toc_entries[toc_entry_index + 1])
        
        track_uri = "cdda://%d/#%s" % (track_index, device)
        track = Track(uri=track_uri, scan=False)
        track.set_tags(
            title="Track %d" % track_index,
            tracknumber=track_index,
            __length=length
        )
        
        tracks.append(track)
    return tracks


def __calculate_track_length(current_track, next_track):
    """ Calculate length of a single track from its data and the data of the following track """
    (_, _, begin_minute, begin_second, begin_frame) = current_track
    (_, _, end_minute, end_second, end_frame) = next_track
    
    length_minutes = end_minute - begin_minute
    length_seconds = end_second - begin_second
    length_frames = end_frame - begin_frame
    # 75 frames per second, see CD_FRAMES in cdrom.h file
    length = length_minutes * 60 + length_seconds + length_frames / 75
    return length
