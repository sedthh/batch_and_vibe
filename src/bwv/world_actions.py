"""Descriptions of named native recipes; targets are supplied by the live bridge."""
import math
from .spells import SPELLS

HELD_MIRACLE_ACTIONS=tuple('use_held_miracle_'+suffix for suffix in ('aggressive','compassionate','playful','self'))

WORLD_ACTIONS = {
    'attack':'Approach and kick the selected object using native behavior. Can damage buildings or hurt living targets.',
    'poo_on':'Approach a selected target, including a field, turn away from it and try to poo there. This uses its position; it does not guarantee hitting or fertilizing the target.',
    'move_to':'Approach a selected known object using native pathfinding. Does not pick it up or interact with it. Walking/running speed and stopping distance are engine controlled; plan ending alone does not prove arrival.',
    'sit_down':'Sit down using native behavior.',
    'look_at_player':'Look toward the player camera.',
    'howl':'Play the native body howl animation if this creature has it. Not yet visually verified.',
    'laugh':'Play the native whole-body laugh animation if this creature has it. Not yet visually verified.',
    'cry':'Play the native body crying animation if this creature has it. Not yet visually verified.',
    'show_realization':'Play the native body aha/realization animation if this creature has it. Not yet visually verified.',
    'pray':'Play the native body prayer animation if this creature has it. Not yet visually verified.',
    'dance_with_villagers':'Approach a villager and start the native group dance. The engine recruits participants and finds space; a started plan does not confirm participation.',
    'fish':'Attempt fishing and eating at the fishing spot the engine finds nearest to you. Target is self, not a fishing-area ID. To fish elsewhere, move there first. Fish availability and success are engine controlled.',
    'eat_from_storage':'Approach a village store and eat from it using the native food-fetching routine. Food availability and outcome remain engine controlled.',
    'eat_from_field':'Approach a field and attempt to gather and eat its food. An unripe or empty field may provide nothing.',
    'eat_from_tree':'Approach a tree and attempt the native tree-eating routine. Does not guarantee edible food or successful eating.',
    'eat_from_food_pile':'Approach a food pile and eat from it using the native routine, without a separate pickup conversation turn.',
}
for _name in ('fireball','fireball_pu1','fireball_pu2','lightning','lightning_pu1','lightning_pu2',
        'megablast','megablast_pu1','megablast_pu2','heal','heal_pu1','teleport','forest','food','food_pu1',
        'lightning_storm','tornado','spiritual_shield','physical_shield','wood',
        'creature_freeze','creature_small','creature_big','creature_weak','creature_strong',
        'creature_fat','creature_thin','creature_invisible','creature_nice','creature_angry',
        'creature_hungry','creature_frightened','creature_tired','creature_ill','creature_thirsty','creature_itchy'):
    WORLD_ACTIONS['cast_'+_name]='Attempt '+_name.replace('_',' ')+' at the selected target through its native casting plan.'
WORLD_ACTIONS['cast_water_field']='Attempt to water a selected field. Native WaterField plan; effect requires live confirmation.'
WORLD_ACTIONS['cast_forest']='Attempt Forest at the selected target position, captured when the casting plan starts; it does not follow a moving target. The engine may reject the location or fail the cast.'
WORLD_ACTIONS['cast_wood']='Attempt Wood at the selected object, including a workshop, storage or building under construction. Completed buildings are also valid targets. The native spell controls resource creation and delivery; selection does not confirm success.'
WORLD_ACTIONS['cast_water_object']='Attempt Water on the selected object using the native fire-extinguishing plan. Does not prove the object is burning or that a fire was extinguished.'
for _name in HELD_MIRACLE_ACTIONS:
    WORLD_ACTIONS[_name]='Consume the miracle bubble currently held and attempt its native spell. May fail; does not grant mastery. Do not put the bubble down first.'
WORLD_ACTIONS['vomit']='Attempt native vomiting on the spot. This is a bodily action, not a decorative emoji; does not guarantee curing illness or changing hunger.'


def held_miracle_available(probe):
    value=probe.get('held_miracle',{})
    held=probe.get('held_actions',{})
    generation=value.get('held_generation')
    if (value.get('version')!=1 or value.get('verified') is not True
            or held.get('version')!=6 or held.get('kind')!='miracle_bubble' or held.get('verified') is not True
            or type(generation) is not int or not 0<generation<=0xffffffff or generation!=held.get('target_generation')
            or type(value.get('spell')) is not int or value['spell'] not in SPELLS
            or type(value.get('bubble_spell')) is not int or value['bubble_spell'] not in SPELLS):return None
    return value


def held_miracle_context(probe):
    value=held_miracle_available(probe)
    if value is None:return None
    result={'spell':SPELLS[value['bubble_spell']], 'cast_spell':SPELLS[value['spell']],
            'consumed_when_attempted':True,'cast_success_guaranteed':False}
    return result

OBJECT_TARGET_LIMIT=16
KNOWN_TARGET_LIMIT=2*OBJECT_TARGET_LIMIT
WORLD_TARGET_LIMIT=2048
NEARBY_TARGET_DISTANCE=360
KNOWN_TARGET_DISTANCE=20000
WORLD_KINDS=('self','tree','rock','villager','house','creature','temple','toy','food_pile','wood_pile','animal','miracle_bubble','field','miracle_dispenser','fishing_area','animal_group')
WORLD_KINDS += ('storage','nursery','cemetery','workshop','wonder','town_centre','totem','windmill','worship_site','fence','mushroom','bonfire','scaffolding')
WORLD_KINDS += ('object',)


def valid_target_metadata(entry):
    if 'pickup_possible' in entry and type(entry['pickup_possible']) is not bool:return False
    if 'pickup_reason' in entry and (entry.get('pickup_possible') is not False or
            entry['pickup_reason'] not in ('too_large_for_current_size',
                'too_heavy_to_lift','engine_pickup_not_allowed')):return False
    if 'bubble_spell' in entry:
        from .spells import SPELLS
        if (entry.get('kind')!='miracle_bubble' or type(entry['bubble_spell']) is not int
                or entry['bubble_spell'] not in SPELLS):return False
    if 'on_fire' in entry and type(entry['on_fire']) is not bool:return False
    if 'distance_to_god_hand_engine_units' in entry:
        distance=entry['distance_to_god_hand_engine_units']
        if type(distance) not in (int,float) or not math.isfinite(distance) or distance<0:return False
    if 'object_handle' in entry:
        handle=entry['object_handle']
        if type(handle) is not int or not 0<handle<=0xffffffff:return False
    return True


def world_recipes_available(probe):
    """Verified/learned recipes remain searchable even with no sampled targets."""
    contract=probe.get('world_actions',{})
    names=contract.get('available_recipes')
    if (probe.get('target_lookup_protocol')!=12 or contract.get('version')!=10
            or not isinstance(names,list) or len(names)>len(WORLD_ACTIONS)
            or any(not isinstance(name,str) or name not in WORLD_ACTIONS for name in names)):
        return ()
    return tuple(dict.fromkeys(name for name in names
        if name not in HELD_MIRACLE_ACTIONS or held_miracle_available(probe) is not None))


def world_offers_available(probe, *, planning=False):
    contract=probe.get('world_actions',{})
    if contract.get('version')!=10:return ()
    hand_full=probe.get('held_actions',{}).get('target_generation')!=0
    held_miracle=held_miracle_available(probe)
    offers=contract.get('offers')
    if not isinstance(offers,list) or len(offers)>WORLD_TARGET_LIMIT:return ()
    seen=set();homes=set();result=[]
    for entry in offers:
        if not isinstance(entry,dict):return ()
        token,distance=entry.get('token'),entry.get('distance_engine_units')
        if (type(token) is not int or not 0<token<=0xffffffff or token in seen
                or entry.get('action') not in WORLD_ACTIONS or entry.get('kind') not in WORLD_KINDS
                or type(distance) not in (int,float) or not math.isfinite(distance)
                or not (distance==0 if entry['kind']=='self' else 0<distance<=KNOWN_TARGET_DISTANCE)):
            return ()
        if 'player_temple' in entry:
            if type(entry['player_temple']) is not bool:return ()
            if entry['player_temple']:
                if entry['kind']!='temple' or entry['action'] in homes:return ()
                homes.add(entry['action'])
        if not valid_target_metadata(entry):return ()
        seen.add(token)
        # Older bridges advertised a fishing-area target that FishAndEat ignores.
        # Omit that offer rather than silently fishing somewhere else.
        if entry['action']=='fish' and entry['kind']!='self':continue
        if entry['action'] in HELD_MIRACLE_ACTIONS:
            if (held_miracle is None or type(entry.get('held_generation')) is not int
                    or type(entry.get('held_spell')) is not int
                    or entry.get('held_generation')!=held_miracle['held_generation']
                    or entry.get('held_spell')!=held_miracle['spell']):continue
        elif hand_full and not (planning and probe.get('hand_preparation_protocol')==1):continue
        result.append(dict(entry))
    return tuple(result)
