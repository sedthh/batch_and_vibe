"""One public metadata projection for inspected, held and action-target objects."""
from copy import deepcopy
import math


def details(value):
    if not isinstance(value, dict):return {}
    result = {}
    for key in ('health_engine_units', 'food_engine_units', 'wood_engine_units',
                'field_fullness_fraction', 'ripe_food_engine_units'):
        number = value.get(key)
        if type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 1e9:
            result[key] = round(number, 4)
    for key in ('construction_fraction', 'repair_fraction'):
        number=value.get(key)
        if type(number) in (int,float) and math.isfinite(number) and 0<=number<=1:
            result[key]=round(number,4)
    for key in ('prayer_energy_available_engine_units','prayer_energy_total_engine_units'):
        number=value.get(key)
        if type(number) in (int,float) and math.isfinite(number) and 0<=number<=1e9:
            result[key]=round(number,4)
    alignment=value.get('alignment')
    if type(alignment) in (int,float) and math.isfinite(alignment) and -1<=alignment<=1:
        result['alignment']=round(alignment,4)
    for key in ('field_ripe', 'poisoned', 'pregnant', 'construction_complete', 'damaged'):
        if type(value.get(key)) is bool:result[key] = value[key]
    from .scaffolding import scaffold_details
    result.update(scaffold_details(value))
    return result


def public_object(obj):
    from .positions import object_position
    from .nearby_creatures import physical_traits
    from .nearby_people import person_details
    from .animal_types import ANIMAL_TYPES
    from .creature_live import SPECIES
    from .object_types import OBJECT_SUBTYPES
    result = {k:deepcopy(obj[k]) for k in ('object_id','kind','distance_engine_units',
        'distance_to_god_hand_engine_units','town_id','on_fire','miracle','object_type') if obj.get(k) is not None}
    if obj.get('kind')=='object':
        from .object_types import engine_class_name
        label=obj.get('engine_class')
        if isinstance(label,str) and engine_class_name(('.?AV'+label+'@@').encode('ascii',errors='replace'))==label:
            result['engine_class']=label
    result.update(object_position(obj))
    if obj.get('species') in (*SPECIES.values(), *ANIMAL_TYPES.values()):result['species'] = obj['species']
    if obj.get('kind') == 'creature':
        from .island import god_id
        owner = god_id(obj.get('owner_god_id'))
        if owner:result['owner_god_id'] = owner
        if owner and obj.get('owner_link') in ('registered_creature','engine_only'):
            result['owner_link'] = obj['owner_link']
        traits = physical_traits(obj.get('physical_traits'))
        if traits:result['physical_traits'] = traits
    if obj.get('kind') == 'villager':
        person = person_details(obj.get('person'))
        if person:result['person'] = person
    metadata = details(obj.get('details'))
    if metadata:result['details'] = metadata
    if obj.get('kind') in ('vortex', 'teleport'):
        from .vortex import portal_details
        portal = portal_details(obj.get('portal'))
        if portal:result['portal'] = portal
    # A canonical description, not an invented personal name or the search alias.
    kind=result.get('kind','object')
    name=kind.replace('_',' ')
    if result.get('object_type') in OBJECT_SUBTYPES:name=result['object_type'].replace('_',' ')
    if result.get('species'):name=result['species'].replace('_',' ')
    if kind=='miracle_bubble' and isinstance(result.get('miracle'),str):
        name=result['miracle'].replace('_',' ')+' miracle bubble'
    result['name']=name
    return result


def known_objects(surroundings):
    """Deduplicate the full known list and support older observer snapshots."""
    objects = {}
    for key in ('known_objects', 'nearby_known_objects', 'god_hand_objects'):
        for obj in surroundings.get(key, ()):
            if obj.get('object_id') and obj.get('kind') != 'debris':
                objects[obj['object_id']] = obj
    return list(objects.values())


def lookup_order(obj, distance_key='distance_engine_units', *, explicit_kind=False):
    """Generic discovery favors useful/urgent objects; explicit searches stay spatial."""
    kind = obj.get('kind')
    person = obj.get('person') or {}
    priority = (0 if kind == 'toy' else
                1 if obj.get('on_fire') is True else
                2 if kind == 'creature' else
                3 if person.get('life_status') in ('dying','dead') else
                4 if person.get('is_actor') is True else
                5 if kind in ('villager','temple','town_centre','totem','vortex','teleport') else
                6 if kind in ('storage','house','abode','nursery','cemetery','workshop','wonder','field','worship_site','windmill') else
                7 if kind in ('miracle_bubble','miracle_dispenser') else
                8 if kind in ('food_pile','wood_pile','mushroom','fishing_area','animal','animal_group') else
                9 if kind == 'scaffolding' else
                11 if kind in ('tree','rock') else 10)
    # Even a specific-type search should surface burning/dying/important objects
    # first; ordinary matches of that type remain ordered by distance.
    return (min(priority,5) if explicit_kind else priority, obj.get(distance_key,float('inf')), obj.get('object_id',''))
