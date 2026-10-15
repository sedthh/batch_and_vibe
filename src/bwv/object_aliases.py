"""Player-facing names for object searches; engine identities stay unchanged."""
import re

from .world_actions import WORLD_KINDS


_GROUPS = (
    (('vortex',), ('portal', 'island_portal', 'island_vortex', 'vortices', 'whirlpool')),
    (('teleport',), ('teleporter', 'teleport_miracle', 'teleport_gate')),
    (('totem', 'town_centre'), ('town_center', 'totem_pole', 'village_center', 'village_centre')),
    (('storage',), ('warehouse', 'storehouse', 'storage_pit', 'village_store')),
    (('workshop',), ('scaffold_maker', 'scaffolding_maker', 'scaffold_creator',
                    'scaffolding_creator', 'scaffold_workshop', 'scaffolding_workshop')),
    (('house',), ('home_building', 'dwelling', 'hut')),
    (('field',), ('wheat_field', 'crop_field', 'farm_field')),
    (('cemetery',), ('graveyard',)),
    (('nursery',), ('creche', 'crèche', 'daycare', 'day_care', 'kindergarten')),
    (('temple',), ('citadel',)),
    (('villager',), ('person', 'people', 'human')),
    (('tree',), ('bush', 'shrub', 'sapling')),
    (('rock',), ('stone', 'boulder')),
    (('animal',), ('wildlife', 'livestock', 'beast')),
    (('mushroom',), ('fungus', 'fungi', 'toadstool')),
    (('toy',), ('ball', 'football', 'beach_ball', 'plaything', 'teddy_bear')),
    (('miracle_bubble',), ('bubble', 'miracle_ball', 'miracle_sphere', 'spell_bubble')),
    (('miracle_dispenser',), ('spell_dispenser', 'miracle_stand', 'spell_stand', 'pedestal', 'miracle_pedestal')),
    (('food_pile',), ('food', 'grain', 'grain_pile', 'food_heap', 'grain_heap')),
    (('storage','field','tree','food_pile'), ('food_source', 'place_to_eat', 'feeding_place')),
    (('wood_pile',), ('wood', 'woodpile', 'timber', 'lumber', 'wood_heap')),
    (('fishing_area',), ('fishing_spot', 'fishing_ground', 'fish', 'fishes')),
    (('animal_group',), ('herd', 'flock')),
    (('bonfire',), ('campfire', 'fire_pit')),
    (('scaffolding',), ('building_site', 'construction_site')),
)
_KINDS = {kind: (kind,) for kind in (*WORLD_KINDS, 'god_hand')}
for _kinds, _aliases in _GROUPS:
    for _name in (*_kinds, *_aliases):
        if _name in _aliases or len(_kinds)==1 or _kinds==('totem','town_centre'):
            _KINDS[_name] = _kinds

_SPECIES_ALIASES = {'cattle':'cow','cows':'cow','sheep':'sheep','wolves':'wolf',
                    'ponies':'horse','pony':'horse','horses':'horse',
                    'tortoises':'tortoise'}
_FIRE_ALIASES = frozenset(('fire', 'fires', 'flame', 'flames', 'blaze', 'blazes',
                         'burning', 'on_fire', 'alight', 'ablaze',
                         'burning_object', 'burning_objects',
                         'object_on_fire', 'objects_on_fire'))

def _key(name):
    return re.sub(r'[\s_-]+','_',name.strip().casefold())


def matching_kinds(name):
    """None means all; unknown names remain unmatched rather than guessing."""
    if name is None:
        return None
    key = _key(name)
    if key not in _KINDS and key.endswith('ies') and key[:-3] + 'y' in _KINDS:
        key = key[:-3] + 'y'
    if key not in _KINDS and key.endswith('s') and key[:-1] in _KINDS:
        key = key[:-1]
    if key not in _KINDS and key.endswith('es') and key[:-2] in _KINDS:
        key = key[:-2]
    return _KINDS.get(key, (key,))


def kind_matches(requested, observed):
    kinds = matching_kinds(requested)
    return kinds is None or observed in kinds


def target_matches(requested, target):
    if requested is not None:
        key=_key(requested)
        if key in ('actor','actors','important_person','important_people'):
            return target['kind']=='villager' and (target.get('person') or {}).get('is_actor') is True
        if key in ('corpse','corpses','body','bodies','dead_person','dead_people'):
            return target['kind']=='villager' and (target.get('person') or {}).get('life_status')=='dead'
        if key in _FIRE_ALIASES:
            return target.get('on_fire') is True
    if kind_matches(requested,target['kind']):return True
    if requested is not None and target.get('kind')=='object' and target.get('engine_class'):
        label=re.sub(r'(?<=[a-z0-9])(?=[A-Z])','_',target['engine_class'])
        if _key(requested).removesuffix('s')==_key(label).removesuffix('s'):return True
    if requested is not None and target.get('species'):
        key=_key(requested)
        if key.endswith('_creature') or key.endswith('_creatures'):
            if target['kind'] != 'creature':return False
            key=key.removesuffix('s').removesuffix('_creature')
        key=_SPECIES_ALIASES.get(key,key.removesuffix('s'))
        if key==target['species'].replace(' ','_'):return True
    if requested is None or target['kind']!='miracle_bubble':return False
    from .spells import SPELLS
    key=re.sub(r'[\s_-]+','_',requested.strip().casefold())
    for suffix in ('_miracle_bubble','_miracle_ball','_miracle_sphere','_spell_bubble',
                   '_miracle','_spell','_bubble','_ball','_sphere'):
        if key.endswith(suffix):key=key[:-len(suffix)];break
    if key=='healing':key='heal'
    return key in SPELLS.values() and target.get('miracle')==key


OBJECT_ALIAS_CONTRACT = """Object search accepts familiar names, spaces, hyphens and plurals:
vortex = portal/island portal/whirlpool; teleport = teleporter/teleport miracle.
These are different objects: vortexes link islands one way; teleports link places
on the same island two ways. Use find_objects or inspect_surrounding to describe
them, even without an action offer. Only action_targets supplies native action offers.
totem = town centre/town center; storage = warehouse/storehouse; house = dwelling/hut;
workshop = scaffold/scaffolding maker, creator or workshop (the building that makes scaffolding);
field = wheat field/crop field; cemetery = graveyard;
nursery = creche/crèche/daycare/day care/kindergarten;
temple = citadel; villager = person/people;
miracle bubble = bubble/miracle ball/miracle sphere/spell bubble;
miracle dispenser = miracle stand/spell stand/spell dispenser/pedestal/miracle pedestal.
food pile = food/grain/food heap; wood pile = wood/timber/lumber; fishing area = fishing spot/fish;
tree = bush/shrub/sapling; rock = stone/boulder; mushroom = fungus/fungi/toadstool;
toy = ball/football/beach ball/plaything/teddy bear; animal = wildlife/livestock/beast.
Species filters include cattle/cows, sheep, wolves, horses and the observed species names.
food_source/place to eat searches stores, fields, trees and food piles; action=eat
then limits results to offered feeding routines. Actors/important people, corpses/
dead people and burning objects filter observed flags, never inferred conditions.
fire/fires, flame/flames, blaze/blazes, burning, on fire, alight and ablaze search
objects reported as on_fire=true. Results keep the burning object's actual type
and usable ID; there is no separate fire object. Missing fire readings do not match.
animal group = herd/flock; bonfire = campfire; scaffolding = building/construction site.
Totem/town-centre searches include both engine object types. Results retain their
actual type and distinct IDs; choose the intended object, then use its compatible
object_id in do_action. Aliases are search filters, not action target IDs.
"""
