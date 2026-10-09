"""Read-only physical metadata for any typed Creature, without ownership assumptions."""
import math
import struct

PHYSICAL = 0x160
HEALTH = 0x48
STATS = {'strength': 0xC, 'fatness': 0x14, 'energy': 0x1C, 'exhaustion': 0x30,
         'warmth': 0x10, 'dehydration': 0x34, 'itchiness': 0x20}


def read_body_radius(reader, creature):
    # Same verified getter/layout contract as native/object_movement_live.h.
    reader.require_type(creature, b'.?AVCreature@@')
    table = reader.word(creature)
    if reader.word(table + 0x64) != 0x477F40:raise ValueError('Unknown creature radius getter')
    physical = reader.word(creature + PHYSICAL)
    reader.require_type(physical, b'.?AVCreaturePhysical@@')
    controller = reader.word(physical + 0x58)
    if reader.word(controller + 0x4834) != creature:raise ValueError('Radius controller mismatch')
    raw = reader.read(controller + 0x5228, 4)
    value, = struct.unpack('<f', raw)
    if not math.isfinite(value) or not 0 < value <= 100:raise ValueError('Invalid body radius')
    if (reader.word(creature) != table or reader.word(table + 0x64) != 0x477F40
            or reader.word(creature + PHYSICAL) != physical
            or reader.word(physical + 0x58) != controller
            or reader.word(controller + 0x4834) != creature
            or reader.read(controller + 0x5228, 4) != raw):
        raise ValueError('Body radius changed during read')
    return value


def read_physical_traits(reader, creature, own_radius=None):
    reader.require_type(creature, b'.?AVCreature@@')
    table = reader.word(creature)
    result, errors = {}, 0
    try:
        physical = reader.word(creature + PHYSICAL)
        reader.require_type(physical, b'.?AVCreaturePhysical@@')
    except (OSError, ValueError):physical = None; errors += 1
    fields = {'health': creature + HEALTH}
    if physical:fields.update({key: physical + offset for key, offset in STATS.items()})
    for name, address in fields.items():
        try:
            raw = reader.read(address, 4)
            number, = struct.unpack('<f', raw)
            minimum = -1e9 if name == 'warmth' else 0
            if not math.isfinite(number) or not minimum <= number <= 1e9:
                raise ValueError('Invalid physical stat')
            if reader.read(address, 4) != raw:raise ValueError('Physical stat changed during read')
            result[name + '_engine_units'] = round(number, 4)
        except (OSError, ValueError):errors += 1
    try:
        radius = read_body_radius(reader, creature)
        result['body_radius_engine_units'] = round(radius, 4)
        if own_radius is not None and math.isfinite(own_radius) and own_radius > 0:
            ratio = radius / own_radius
            result['size_compared_to_you'] = ('smaller_than_you' if ratio < .8 else
                'larger_than_you' if ratio > 1.2 else 'similar_size_to_you')
    except (OSError, ValueError):errors += 1
    if reader.word(creature) != table or (physical and reader.word(creature + PHYSICAL) != physical):
        raise ValueError('Creature body changed during read')
    return result, errors
