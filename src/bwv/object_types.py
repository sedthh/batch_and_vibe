"""Shared world-object categories and subtype metadata; no game calls."""
from .animal_types import ANIMAL_TYPES
from .building_types import BUILDING_TYPES
from .held_objects import RUNTIME_HELD_DETAILS
from .vortex import PORTAL_TYPES
import re


def engine_class_name(runtime_type):
    """A bounded RTTI label, not an invented gameplay category or capability."""
    if not isinstance(runtime_type, bytes):return None
    match = re.fullmatch(rb'\.\?AV([A-Za-z][A-Za-z0-9_]{0,55})@@', runtime_type)
    return match[1].decode('ascii') if match else None


OBJECT_KINDS = {b'.?AVTree@@':'tree', b'.?AVVillager@@':'villager',
    b'.?AVSpecialVillager@@':'villager', b'.?AVAbode@@':'abode', b'.?AVPileFood@@':'food_pile',
    b'.?AVPileWood@@':'wood_pile', b'.?AVCreature@@':'creature', b'.?AVRock@@':'rock',
    b'.?AVField@@':'field', b'.?AVBonfire@@':'bonfire', b'.?AVScaffold@@':'scaffolding',
    b'.?AVFragment@@':'debris', b'.?AVOneOffSpellSeed@@':'miracle_bubble',
    b'.?AVSpellDispenser@@':'miracle_dispenser',
    b'.?AVFishFarm@@':'fishing_area', b'.?AVFlock@@':'animal_group',
    **{name:'animal' for name in ANIMAL_TYPES}, **BUILDING_TYPES, **PORTAL_TYPES}

TOY_TYPES = {b'.?AVBall@@', b'.?AVFootball@@'}
# GMobileStaticInfo's mesh IDs, shared by ground and carried toys.
TOY_MESH_TYPES = {399: 'ball', 400: 'bowling_ball', 401: 'cuddly_toy',
                  402: 'die', 403: 'skittle'}
OBJECT_SUBTYPES = frozenset((*RUNTIME_HELD_DETAILS.values(), *TOY_MESH_TYPES.values()))
# Feature::IsMushroom identifies entry 54 in the verified FeatureInfo table.
FEATURE_MUSHROOM_INFO = 0xCC99A0 + 54 * 0x124


def object_kind(reader, address, runtime_type, type_reader):
    if runtime_type in TOY_TYPES:
        return 'toy'
    expected = {b'.?AVMobileObject@@': b'.?AVGMobileObjectInfo@@',
                b'.?AVMobileStatic@@': b'.?AVGMobileStaticInfo@@',
                b'.?AVFeature@@': b'.?AVGFeatureInfo@@'}.get(runtime_type)
    if expected is None:
        return None
    info = reader.word(address + 0x28)
    if not info or type_reader(info) != expected:
        return None
    if runtime_type == b'.?AVMobileObject@@':
        if reader.word(info + 0x104) in (18, 19):
            return 'mushroom'
    elif runtime_type == b'.?AVMobileStatic@@':
        if reader.word(info + 0x120) in TOY_MESH_TYPES:
            return 'toy'
    elif info == FEATURE_MUSHROOM_INFO:
        return 'mushroom'
    return None


def object_subtype(reader, address, runtime_type, type_reader):
    """Describe observed identity without changing its action category."""
    if runtime_type != b'.?AVMobileStatic@@':
        return RUNTIME_HELD_DETAILS.get(runtime_type)
    info = reader.word(address + 0x28)
    if not info or type_reader(info) != b'.?AVGMobileStaticInfo@@':
        return None
    mesh = reader.word(info + 0x120)
    if reader.word(address + 0x28) != info or reader.word(info + 0x120) != mesh:
        raise ValueError('Object subtype changed while reading')
    return TOY_MESH_TYPES.get(mesh)
