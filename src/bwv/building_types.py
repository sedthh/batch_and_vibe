"""Engine type metadata for buildings and fences; no game assets or code."""

BUILDING_TYPES = {
    b'.?AVAbode@@': 'house', b'.?AVStoragePit@@': 'storage',
    b'.?AVCreche@@': 'nursery', b'.?AVGraveyard@@': 'cemetery',
    b'.?AVWorkshop@@': 'workshop', b'.?AVWonder@@': 'wonder',
    b'.?AVTownCentre@@': 'town_centre', b'.?AVTotem@@': 'totem',
    b'.?AVWindmill@@': 'windmill', b'.?AVWorshipSite@@': 'worship_site',
    b'.?AVCitadel@@': 'temple', b'.?AVCitadelHeart@@': 'temple',
}
# MobileStatic::IsFence checks these GMobileStaticInfo subtype values.
FENCE_SUBTYPES = frozenset((56, 81, 82))


def building_kind(reader, address, runtime_type, type_reader):
    if runtime_type in BUILDING_TYPES:
        return BUILDING_TYPES[runtime_type]
    if runtime_type == b'.?AVMobileStatic@@':
        info = reader.word(address + 0x28)
        if type_reader(info) == b'.?AVGMobileStaticInfo@@':
            if reader.word(info + 0x120) in FENCE_SUBTYPES:
                return 'fence'
    return None
