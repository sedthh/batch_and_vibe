"""Small, validated public descriptions of other creatures."""
import math

BODY_FIELDS = ('health', 'energy', 'exhaustion', 'fatness', 'warmth', 'dehydration', 'itchiness', 'strength')
SIZE_LABELS = ('smaller_than_you', 'similar_size_to_you', 'larger_than_you')


def physical_traits(value):
    if not isinstance(value, dict):return {}
    result = {}
    for name in (*BODY_FIELDS, 'body_radius'):
        key = name + '_engine_units'
        number = value.get(key)
        minimum = -1e9 if name == 'warmth' else 0
        maximum = 100 if name == 'body_radius' else 1e9
        if (type(number) in (int, float) and math.isfinite(number)
                and minimum <= number <= maximum and (name != 'body_radius' or number > 0)):
            result[key] = round(number, 4)
    if value.get('size_compared_to_you') in SIZE_LABELS:
        result['size_compared_to_you'] = value['size_compared_to_you']
    return result


def creature_summary(objects):
    """At most four nearest profiles; full counts and inspection remain separate."""
    result = []
    for obj in objects:
        if obj.get('kind') != 'creature' or not obj.get('object_id'):continue
        traits = physical_traits(obj.get('physical_traits'))
        item = {'object_id': obj['object_id']}
        from .island import god_id
        if god_id(obj.get('owner_god_id')):item['owner_god_id'] = obj['owner_god_id']
        if item.get('owner_god_id') and obj.get('owner_link') in ('registered_creature','engine_only'):
            item['owner_link'] = obj['owner_link']
        if obj.get('species'):item['species'] = obj['species']
        for key in ('size_compared_to_you', 'health_engine_units'):
            if key in traits:item[key] = traits[key]
        if obj.get('on_fire') is True:item['on_fire'] = True
        if len(item) > 1:result.append(item)
        if len(result) == 4:break
    return result


def summary_profiles(value):
    if not isinstance(value, list):return []
    from .creature_live import SPECIES
    result = []
    for obj in value[:4]:
        if not isinstance(obj,dict):continue
        identity = obj.get('object_id')
        if not isinstance(identity,str) or not 0 < len(identity) <= 96:continue
        item = {'object_id':identity}
        from .island import god_id
        if god_id(obj.get('owner_god_id')):item['owner_god_id'] = obj['owner_god_id']
        if item.get('owner_god_id') and obj.get('owner_link') in ('registered_creature','engine_only'):
            item['owner_link'] = obj['owner_link']
        if obj.get('species') in SPECIES.values():item['species'] = obj['species']
        traits = physical_traits(obj)
        for key in ('size_compared_to_you','health_engine_units'):
            if key in traits:item[key] = traits[key]
        if obj.get('on_fire') is True:item['on_fire'] = True
        if len(item)>1:result.append(item)
    return result
