"""Read the current selected god or creature's typed GAlignment value."""
import math
import struct


def read_alignment(reader, owner, offset):
    address=reader.word(owner+offset)
    reader.require_type(address,b'.?AVGAlignment@@')
    raw=reader.read(address+8,4)
    value,=struct.unpack('<f',raw)
    if not math.isfinite(value) or not -1<=value<=1:raise ValueError('Invalid alignment')
    if reader.word(owner+offset)!=address or reader.read(address+8,4)!=raw:
        raise ValueError('Alignment changed during read')
    return round(value,4)
