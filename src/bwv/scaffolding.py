"""Public scaffold counts and current building previews."""
import math

from .towns import TRIBES


BUILDING_DESCRIPTIONS = {
    **{f'ABODE_{letter}': 'house' for letter in 'ABCDEF'},
    'ABODE_CRECHE': 'nursery', 'ABODE_GRAVEYARD': 'cemetery',
    'ABODE_STORAGE_PIT': 'storage', 'ABODE_WORKSHOP': 'workshop',
    'ABODE_WONDER': 'wonder', 'ABODE_TOTEM': 'totem',
    'ABODE_TOWN_CENTRE': 'town centre', 'ABODE_FIELD': 'field',
    'ABODE_SPELL_DISPENSER': 'miracle dispenser',
    'ABODE_FOOTBALL_PITCH': 'football pitch', 'ABODE_ARK': 'ark',
}


def scaffold_details(value):
    """Keep engine pointers, raw type codes and unverified strings private."""
    result = {}
    count = value.get('combined_scaffolds')
    if type(count) is int and 0 <= count <= 0xFFFFFFFF:
        result['combined_scaffolds'] = count
    scale = value.get('size_scale')
    if type(scale) in (int, float) and math.isfinite(scale) and 0 < scale <= 1e9:
        result['size_scale'] = round(scale, 4)
    for key in ('planned_building', 'alternative_building'):
        source = value.get(key)
        if not isinstance(source, dict) or source.get('type') not in BUILDING_DESCRIPTIONS.values():continue
        plan = {'type': source['type']}
        if source.get('tribe') in TRIBES:plan['tribe'] = source['tribe']
        if plan['type'] == 'house' and source.get('house_variant') in tuple('ABCDEF'):
            plan['house_variant'] = source['house_variant']
        count = source.get('required_scaffolds')
        if type(count) is int and 0 <= count <= 0xFFFFFFFF:plan['required_scaffolds'] = count
        scale = source.get('size_scale')
        if type(scale) in (int, float) and math.isfinite(scale) and 0 < scale <= 1e9:
            plan['size_scale'] = round(scale, 4)
        result[key] = plan
    return result
