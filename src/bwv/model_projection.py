"""Compact public object descriptions; engine categories remain host-owned."""
from copy import deepcopy
from dataclasses import replace
import json
import math

from .world_actions import WORLD_KINDS


def field_name(key, *, context=None, value=None):
    if context in ('surroundings', 'surroundings_near_god_hand'):
        # Keep the count and the optional individual creature profiles distinct.
        if key == 'other_creatures' and isinstance(value, (list, tuple)):
            return 'other_creature_details'
        key = key.removesuffix('_nearby')
    if key in ('hunger_motivation_engine_units', 'hunger_desire'):
        return 'hunger_motivation'
    return key.removesuffix('_engine_units')


def creature_condition(result):
    """The game's Damage/Hunger/Tiredness meters, as integer percentages.

    Apply only to creature body data, never building life or native desires.
    Engine readings and host-side scheduling/action logic stay unchanged.
    """
    for source, name, inverted in (('health', 'damage', True),
                                   ('energy', 'hunger', True),
                                   ('exhaustion', 'tiredness', False)):
        if source not in result:
            continue
        value = result.pop(source)
        if type(value) in (int, float) and math.isfinite(value):
            percent = (1 - value if inverted else value) * 100
            result[name] = max(0, min(100, round(percent)))
    return result


def model_objects(value, *, context=None, creature_body=False):
    """One type per object, without rewriting event kinds or tool schemas."""
    if isinstance(value, (list, tuple)):
        return [model_objects(item, context=context, creature_body=creature_body) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get('kind')
    is_creature = kind in ('creature', 'self') or context in ('self', 'creature', 'other_creatures')
    result = {field_name(key, context=context, value=item):model_objects(item, context=key,
                  creature_body=is_creature and key in ('physical_traits', 'details'))
              for key,item in value.items()}
    if creature_body or is_creature or context == 'body_engine_units':
        creature_condition(result)
    is_object = kind in (*WORLD_KINDS, 'object', 'god_hand', 'self', 'vortex', 'teleport') or (
        'species' in value and ('object_id' in value or context in ('creature', 'other_creatures')))
    if not is_object:
        return result
    species = value.get('species')
    detail = value.get('object_type')
    noun = species or detail or kind or 'creature'
    noun = noun.replace('_', ' ')
    if species and (kind == 'creature' or context in ('creature', 'other_creatures')):
        noun += ' creature'
    result['type'] = noun
    for key in ('kind', 'species', 'object_type'):
        result.pop(key, None)
    # Keep genuine distinguishing names, not generated repeats of other fields.
    name = result.get('name')
    duplicates = {str(v).replace('_', ' ').casefold() for v in
                  (kind, species, detail, noun) if v}
    miracle = value.get('miracle')
    # Ground objects name their spell directly; a held bubble also carries
    # casting/consumption metadata. Keep that metadata intact in the projection.
    spell = miracle.get('spell') if isinstance(miracle, dict) else miracle
    if isinstance(spell, str) and spell:
        duplicates.add((spell+' '+str(kind)).replace('_', ' ').casefold())
    if isinstance(name, str) and name.casefold() in duplicates:
        result.pop('name')
    if 'observed_object_id' in result:
        result['object_id'] = result.pop('observed_object_id')
    return result


def model_turn(value):
    """Transport timestamps and planner flags are diagnostic data, not context."""
    result = deepcopy(value)
    if isinstance(result, dict) and isinstance(result.get('snapshot'), dict):
        result['message_type'] = 'god_question' if result.get('god_question') else 'world_observation'
        result.pop('time', None)
        result.pop('conversation_only', None)
        result['snapshot'].pop('candidates', None)
        result['snapshot'].pop('action_targets', None)
        # Old saved turns can contain the former per-tick emoji dictionary.
        # The fixed system vocabulary is sufficient for current and past turns.
        capabilities = result.get('capabilities')
        if isinstance(capabilities, dict):
            capabilities.pop('gestures', None)
            if not capabilities:
                result.pop('capabilities')
        world = result['snapshot'].get('world', {})
        if 'god_alignment' in world:
            result['snapshot'].setdefault('god_hand', {})['alignment'] = world.pop('god_alignment')
            if not world:
                result['snapshot'].pop('world')
    return model_objects(result)


def model_snapshot(snapshot):
    """The current debugger preview uses the same shape as a sent observation."""
    return model_turn({'snapshot': snapshot})['snapshot']


def model_request(request):
    """Keep the same public observations when tool rounds are disabled."""
    messages=[]
    for original in request.messages:
        message=dict(original)
        if message.get('role') in ('user','assistant'):
            try:message['content']=json.dumps(model_turn(json.loads(message['content'])),ensure_ascii=False)
            except (ValueError,TypeError):pass
        messages.append(message)
    return replace(request,current=model_turn(request.current),messages=tuple(messages))
