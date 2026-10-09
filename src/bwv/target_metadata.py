"""One description of an object for offers, retries and remembered intentions."""
from copy import deepcopy
import hashlib

from .object_details import known_objects, public_object
from .positions import object_position
from .spells import SPELLS


def object_index(raw):
    return {o['object_id']:public_object(o) for o in known_objects(raw.get('surroundings') or {})}


def describe_target(entry, session, creature_handle, metadata):
    result={'kind':entry['kind']}
    if 'object_handle' in entry:
        identity=hashlib.sha256(f"{session}:{creature_handle}:{entry['object_handle']}".encode()).hexdigest()[:24]
        result.update(deepcopy(metadata.get(identity,{})),object_id=identity)
    for key in ('distance_engine_units','on_fire','distance_to_god_hand_engine_units',
                'pickup_possible','pickup_reason','player_temple'):
        if key in entry:result[key]=entry[key]
    if entry.get('bubble_spell') in SPELLS:result['miracle']=SPELLS[entry['bubble_spell']]
    result.update(object_position(entry))
    result['name']=public_object(result)['name']
    return result


def remembered_target(value):
    """Keep meaning, never native addresses, leases or a whole target catalog."""
    if not isinstance(value,dict):return {}
    result={k:deepcopy(value[k]) for k in ('object_id','kind','type','name','species','miracle','town_id',
             'player_temple','position','location') if k in value}
    for key in ('projectile','destination'):
        if key in value:
            child=remembered_target(value[key])
            if child:result[key]=child
    from .model_projection import model_objects
    return model_objects(result)
