"""Read the standard Object fire predicate without calling engine methods."""
import math
import struct


def read_object_fire(reader, obj):
    table = reader.word(obj)
    if reader.word(table + 0x298) != 0x402AA0:
        return None  # A different implementation needs its own verified layout.
    fire = reader.word(obj + 0x44)
    checks = [(obj, table), (obj + 0x44, fire)]
    burning = False
    if fire:
        owner = reader.word(fire + 0x1C)
        info = reader.word(obj + 0x28)
        if owner != obj or not info:
            raise ValueError('Object fire record mismatch')
        value_raw = reader.read(fire + 0x14, 4)
        threshold_raw = reader.read(info + 0xB4, 4)
        value, = struct.unpack('<f', value_raw)
        threshold, = struct.unpack('<f', threshold_raw)
        if not all(math.isfinite(v) and 0 <= v <= 1e9 for v in (value, threshold)):
            raise ValueError('Invalid object fire values')
        burning = value >= max(40.0, threshold)
        checks.extend(((fire + 0x1C, owner), (obj + 0x28, info)))
        if (reader.read(fire + 0x14, 4) != value_raw
                or reader.read(info + 0xB4, 4) != threshold_raw):
            raise ValueError('Object fire values changed during observation')
    if any(reader.word(address) != value for address, value in checks):
        raise ValueError('Object fire record changed during observation')
    return burning
