"""Read only demands backed by the town's actual storage-flag objects."""
import math
import struct

from .towns import TOWN_NEEDS

TOWN_DESIRE = 0x34
DESIRE_TOWN = 0x160
VALUES_START = 0xC4
MODIFIERS_SIZE = 17 * 8
BASE_VALUES_START = 0x19C
BASE_VALUES_SIZE = 17 * 4
GET_RAW_DESIRE = 0x73E420
# Town::GetRawDesire reads three indexed floats; checked, never called.
GET_RAW_DESIRE_CODE = bytes.fromhex(
    '8b442404d984819c010000d8848108010000d88481c4000000c20400')
TOWN_FLAGS = 0x9B0
FLAG_CATEGORY = 0x5C
FLAG_TOWN = 0x64
# CreateTownDesireFlags indexes Town+9b0 by desire. These getters establish the
# flag category and owner layout without calling any game code.
FLAG_LAYOUT_CODE = (
    (0x73D80D, bytes.fromhex('8dbdb0090000')),
    (0x746DB0, bytes.fromhex('8b415c8d04c0c1e004053029da00c3')),
    (0x55DA70, bytes.fromhex('8b4164c3')),
)


def read_town_needs(reader, town):
    """Bounded reads once per cached town, not per building or frame.

    ProcessDesireFlags feeds GetRawDesire to its flags. Read those same demand
    components, including persistent/temporary adjustments, without the flag's
    visual smoothing or display clamp. Only categories for which the engine
    created a flag are included, even when a flag's demand is currently zero.
    Internal desires without flags never reach observations or tool results.
    """
    desire=town+TOWN_DESIRE
    reader.require_type(desire,b'.?AVTownDesire@@')
    table=reader.word(desire)
    if reader.word(desire+DESIRE_TOWN)!=town:
        raise ValueError('Town needs owner changed')
    flags_raw=reader.read(town+TOWN_FLAGS,17*4)
    flags=struct.unpack('<17I',flags_raw)
    indices=[]
    for index,flag in enumerate(flags):
        if not flag:continue
        reader.require_type(flag,b'.?AVTownDesireFlags@@')
        category,_,owner=struct.unpack('<3I',reader.read(flag+FLAG_CATEGORY,12))
        if category!=index or owner!=town:
            raise ValueError('Town storage flag identity changed')
        indices.append(index)
    # The process reader allows at most 256 bytes. Read the adjacent modifier
    # arrays together and the base array separately, omitting unrelated fields.
    raw=reader.read(town+VALUES_START,MODIFIERS_SIZE)
    modifiers=struct.unpack_from('<17f',raw,0)
    temporary=struct.unpack_from('<17f',raw,0x108-VALUES_START)
    base=struct.unpack('<17f',reader.read(town+BASE_VALUES_START,BASE_VALUES_SIZE))
    if any(not math.isfinite(values[i]) or abs(values[i])>1e9
           for i in indices for values in (base,modifiers,temporary)):
        raise ValueError('Invalid town need values')
    if (reader.word(desire)!=table or reader.word(desire+DESIRE_TOWN)!=town
            or reader.read(town+TOWN_FLAGS,17*4)!=flags_raw):
        raise ValueError('Town needs identity changed')
    scores={TOWN_NEEDS[i]:base[i]+temporary[i]+modifiers[i] for i in indices}
    if any(abs(v)>1e9 for v in scores.values()):raise ValueError('Invalid town need totals')
    return dict(sorted(((name,round(value,4)) for name,value in scores.items()),
                       key=lambda item:-item[1]))
