"""Shared world-object categories and subtype metadata; no game calls."""
from .animal_types import ANIMAL_TYPES
from .building_types import BUILDING_TYPES


OBJECT_KINDS = {b'.?AVTree@@':'tree', b'.?AVVillager@@':'villager',
    b'.?AVSpecialVillager@@':'villager', b'.?AVAbode@@':'abode', b'.?AVPileFood@@':'food_pile',
    b'.?AVPileWood@@':'wood_pile', b'.?AVCreature@@':'creature', b'.?AVRock@@':'rock',
    b'.?AVField@@':'field', b'.?AVBonfire@@':'bonfire', b'.?AVScaffold@@':'scaffolding',
    b'.?AVFragment@@':'debris', b'.?AVOneOffSpellSeed@@':'miracle_bubble',
    b'.?AVSpellDispenser@@':'miracle_dispenser',
    b'.?AVFishFarm@@':'fishing_area', b'.?AVFlock@@':'animal_group',
    **{name:'animal' for name in ANIMAL_TYPES}, **BUILDING_TYPES}

TOY_TYPES = {b'.?AVBall@@', b'.?AVFootball@@'}
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
        if 399 <= reader.word(info + 0x120) <= 403:
            return 'toy'
    elif info == FEATURE_MUSHROOM_INFO:
        return 'mushroom'
    return None
