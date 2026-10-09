"""Typed, read-only getters reconstructed from the supported engine layout.

Each optional field fails independently; a missing getter never erases the object.
No virtual function is called from the observer.
"""
import math
import struct


def read_details(reader, obj, runtime_type):
    reader.require_type(obj, runtime_type)
    table = reader.word(obj)
    result, errors = {}, 0

    def stable(address, size=4):
        raw = reader.read(address, size)
        if reader.read(address, size) != raw:raise ValueError('Object detail changed')
        return raw

    def number(address):
        value, = struct.unpack('<f', stable(address))
        if not math.isfinite(value) or not 0 <= value <= 1e9:raise ValueError('Invalid object quantity')
        return round(value, 4)

    def optional(read):
        nonlocal errors
        try:read()
        except (OSError, ValueError):errors += 1

    def health():
        # Object::GetLife, shared by living and fixed world objects.
        if reader.word(table + 0x11C) == 0x402600:
            result['health_engine_units'] = number(obj + 0x48)
    optional(health)

    def resources():
        # Abode::GetResource. Storage subclasses with a different getter must
        # not be interpreted as this layout.
        if reader.word(table + 0x98) == 0x404D30:
            for key, offset in (('food_engine_units', 0xBC), ('wood_engine_units', 0xC0)):
                result[key] = int.from_bytes(stable(obj + offset), 'little')
        elif runtime_type in (b'.?AVPileFood@@', b'.?AVPileWood@@'):
            # Pot::JustGetResource stores its resource amount here.
            if reader.word(table + 0x94) == 0x66D390:
                key = 'food_engine_units' if runtime_type == b'.?AVPileFood@@' else 'wood_engine_units'
                result[key] = int.from_bytes(stable(obj + 0x70), 'little')
    optional(resources)

    if runtime_type == b'.?AVScaffold@@':
        def scaffold():
            nonlocal errors
            from .scaffolding_live import read_scaffolding
            metadata, failures = read_scaffolding(reader, obj)
            result.update(metadata)
            errors += failures
        optional(scaffold)

    from .building_types import BUILDING_TYPES
    if runtime_type in BUILDING_TYPES:
        def construction():
            if reader.word(table+0x880)!=0x4014F0:return
            # MultiMapFixed::GetPercentBuilt; independent of current health.
            built=number(obj+0x5C)
            if built>1:raise ValueError('Invalid construction fraction')
            complete=None
            if reader.word(table+0x890)==0x4016C0:
                complete=not bool(stable(obj+0x58,1)[0]&2) and built>=1
            result['construction_fraction']=built
            if complete is not None:result['construction_complete']=complete
        optional(construction)

        def repair():
            # GetPercentRepaired forwards to GetLife; only the verified getter
            # justifies treating building health as this normalized fraction.
            if reader.word(table+0x884)!=0x401500 or reader.word(table+0x11C)!=0x402600:return
            repaired=number(obj+0x48)
            if repaired>1:raise ValueError('Invalid repair fraction')
            result.update(repair_fraction=repaired,damaged=repaired<1)
        optional(repair)

    if runtime_type==b'.?AVWorshipSite@@':
        def worship_energy():
            # WorshipSite::Get[Total]ChantsAvailable, including its script
            # override. This is site-local prayer energy, not all-god mana.
            raw=stable(obj+0xF8,20)
            total,reserved=struct.unpack_from('<ff',raw)
            override,=struct.unpack_from('<I',raw,16)
            if override:total=available=1_000_000.0
            else:available=total-reserved
            if not all(math.isfinite(v) and 0<=v<=1e9 for v in (total,available)):
                raise ValueError('Invalid worship energy')
            result.update(prayer_energy_total_engine_units=round(total,4),
                          prayer_energy_available_engine_units=round(available,4))
        optional(worship_energy)

        def worship_food():
            if reader.word(table+0x98)!=0x77BD80:return
            raw=stable(obj+0xB4);pile,=struct.unpack('<I',raw)
            amount=0
            if pile:
                reader.require_type(pile,b'.?AVPileFood@@')
                pile_table=reader.word(pile)
                if reader.word(pile_table+0x94)!=0x66D390:return
                amount=int.from_bytes(stable(pile+0x70),'little')
                if reader.word(pile)!=pile_table:raise ValueError('Worship food changed')
            if reader.read(obj+0xB4,4)!=raw:raise ValueError('Worship food link changed')
            result['food_engine_units']=amount
        optional(worship_food)

    if runtime_type==b'.?AVCreature@@':
        def alignment():
            from .alignment_live import read_alignment
            result['alignment']=read_alignment(reader,obj,0x168)
        optional(alignment)

    if runtime_type == b'.?AVField@@':
        def field():
            info = reader.word(obj + 0x120)
            reader.require_type(info, b'.?AVGFieldInfo@@')
            maximum = number(info + 0x128)
            count = stable(obj + 0xCC, 1)[0]
            ripe = number(obj + 0xD0) >= number(info + 0x124)
            food = number(obj + 0xDC) if ripe else 0
            if reader.word(obj + 0x120) != info:raise ValueError('Field info changed')
            if maximum > 0:result['field_fullness_fraction'] = round(count / maximum, 4)
            result.update(field_ripe=ripe, ripe_food_engine_units=food)
        optional(field)
    if runtime_type in (b'.?AVVillager@@', b'.?AVSpecialVillager@@'):
        def conditions():
            result['poisoned'] = bool(stable(obj + 0xB4, 1)[0] & 2)
            info = reader.word(obj + 0x28)
            reader.require_type(info, b'.?AVGVillagerInfo@@')
            sex = int.from_bytes(stable(info + 0x1F8), 'little')
            if sex in (0, 1):result['pregnant'] = sex == 1 and bool(int.from_bytes(stable(obj + 0xF8, 2), 'little'))
        optional(conditions)
    if reader.word(obj) != table:raise ValueError('Object type changed')
    from .object_details import details
    return details(result), errors
