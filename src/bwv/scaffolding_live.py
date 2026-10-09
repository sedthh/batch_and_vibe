"""Read Scaffold::GetScaffoldValue and its PlannedAbode previews, without calls."""
import math
import struct

from .scaffolding import BUILDING_DESCRIPTIONS, scaffold_details
from .towns import TRIBES

ABODE_INFO_BASE, ABODE_INFO_STRIDE, ABODE_INFO_COUNT = 0xC3C690, 0x1C8, 147


def read_scaffolding(reader, obj):
    reader.require_type(obj, b'.?AVScaffold@@')
    table = reader.word(obj)
    result, errors, count_raw = {}, 0, None

    def stable(address, size):
        value = reader.read(address, size)
        if reader.read(address, size) != value:raise ValueError('Scaffolding changed during read')
        return value

    def scale(address):
        value, = struct.unpack('<f', stable(address, 4))
        if not math.isfinite(value) or not 0 < value <= 1e9:raise ValueError('Invalid scaffold scale')
        return value

    try:
        # GetScaffoldValue rounds (GetScale() - .5) * 5. SetScaffoldValue is
        # the inverse: count * .2 + .5. This is a count, not percent built.
        if reader.word(table + 0x120) == 0x402520:
            count_raw = stable(obj + 0x50, 4)
            size, = struct.unpack('<f', count_raw)
            if not math.isfinite(size):raise ValueError('Invalid scaffold scale')
            count = int((size - .5) * 5 + .5)
            if size < .5 or not 0 <= count <= 0xFFFFFFFF:raise ValueError('Invalid scaffold count')
            result.update(size_scale=size, combined_scaffolds=count)
    except (OSError, ValueError):errors += 1

    # The primary preview is +6C; +70 is an alternative which the engine can
    # swap in as placement changes. Neither proves construction has happened.
    try:
        links = stable(obj + 0x6C, 8)
        plans = {}
        for key, planned in zip(('planned_building', 'alternative_building'), struct.unpack('<II', links)):
            if not planned:continue
            try:
                reader.require_type(planned, b'.?AVPlannedAbode@@')
                plan_table = reader.word(planned)
                # IsPlannedValid uses GameThing's deletion flag and GetInfo.
                if reader.word(plan_table + 0x2C) != 0x401810:continue
                flag = stable(planned + 0xA, 1)
                if flag[0] & 1:continue
                info_raw = stable(planned + 0x40, 4)
                info, = struct.unpack('<I', info_raw)
                if not info:continue
                index, remainder = divmod(info - ABODE_INFO_BASE, ABODE_INFO_STRIDE)
                if remainder or not 0 <= index < ABODE_INFO_COUNT:raise ValueError('Invalid planned building info')
                reader.require_type(info, b'.?AVGAbodeInfo@@')
                # GAbodeInfo::GetDescription is this fixed 48-byte field;
                # Find uses its following tribe and description, not slot order.
                metadata = stable(info + 0x10C, 0x50)
                description = metadata[0x1C:0x4C].split(b'\0', 1)[0].decode('ascii')
                kind = BUILDING_DESCRIPTIONS.get(description)
                if not kind:continue
                plan = {'type': kind, 'required_scaffolds': struct.unpack_from('<I', metadata)[0]}
                tribe, = struct.unpack_from('<I', metadata, 0x4C)
                if tribe < len(TRIBES):plan['tribe'] = TRIBES[tribe]
                if kind == 'house':plan['house_variant'] = description[-1]
                if reader.word(plan_table + 0x120) == 0x4050C0:
                    plan['size_scale'] = scale(planned + 0x2C)
                if (reader.word(planned) != plan_table or reader.read(planned + 0x40, 4) != info_raw
                        or reader.read(planned + 0xA, 1) != flag):
                    raise ValueError('Planned building changed during read')
                plans[key] = plan
            except (OSError, ValueError):errors += 1
        if reader.read(obj + 0x6C, 8) != links:raise ValueError('Scaffold plans changed during read')
        result.update(plans)
    except (OSError, ValueError):errors += 1
    if (reader.word(obj) != table or count_raw is not None
            and reader.read(obj + 0x50, 4) != count_raw):
        raise ValueError('Scaffold changed during read')
    return scaffold_details(result), errors
