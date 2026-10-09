"""Bounded, read-only Villager metadata for the verified engine layout."""
from .nearby_people import ACTIVITIES, ROLES

TICKS_PER_YEAR = 0xD01A04


def read_person(reader, obj, tick, runtime_type):
    if runtime_type not in (b'.?AVVillager@@', b'.?AVSpecialVillager@@'):
        raise ValueError('Not a typed person')
    reader.require_type(obj, runtime_type)
    table = reader.word(obj)
    result, errors = {}, 0

    def stable(address, size):
        raw = reader.read(address, size)
        if reader.read(address, size) != raw:raise ValueError('Person metadata changed during read')
        return raw

    # Current state only. The following byte is a destination/continuation
    # state and must not be reported as an activity already happening.
    state = None
    try:
        state = stable(obj + 0x8C, 1)[0]
        if state in ACTIVITIES:result['current_activity'] = ACTIVITIES[state]
        if state in (13, 14):result['life_status'] = 'dying'
        elif state == 15:result['life_status'] = 'dead'
    except (OSError, ValueError):errors += 1
    try:
        scripted = bool(stable(obj + 0x25, 1)[0] & 4)
        result['script_controlled'] = scripted
        result['is_actor'] = scripted or state in (4, 204)
    except (OSError, ValueError):
        errors += 1
        if state in (4, 204):result['is_actor'] = True
    try:
        role = stable(obj + 0xF2, 1)[0]
        if role in ROLES:result['assigned_role'] = ROLES[role]
    except (OSError, ValueError):errors += 1
    try:
        info = reader.word(obj + 0x28)
        reader.require_type(info, b'.?AVGVillagerInfo@@')
        sex = int.from_bytes(stable(info + 0x1F8, 4), 'little')
        if reader.word(obj + 0x28) != info:raise ValueError('Person info changed during read')
        if sex in (0, 1):result['sex'] = ('male', 'female')[sex]
    except (OSError, ValueError):errors += 1
    try:
        # GetAge uses unsigned tick subtraction and integer game-year division.
        if reader.word(table + 0x8D0) != 0x5ECAF0:raise ValueError('Unknown age getter')
        born = int.from_bytes(stable(obj + 0xA0, 4), 'little')
        period = int.from_bytes(stable(TICKS_PER_YEAR, 4), 'little')
        if not 0 < period <= 0x7FFFFFFF:raise ValueError('Invalid game year duration')
        age = ((tick - born) & 0xFFFFFFFF) // period
        if age > 10000:raise ValueError('Invalid person age')
        result['age_years'] = age
    except (OSError, ValueError):errors += 1
    if reader.word(obj) != table:raise ValueError('Person type changed during read')
    return result, errors
