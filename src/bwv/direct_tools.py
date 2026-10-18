"""Small model-facing tool menu over the existing request-bound action executor."""
from copy import deepcopy
from dataclasses import replace
import json
import re

from .creature_tools import CreatureToolSession, ToolArgumentError, ToolContextUnavailable, schema
from .expressions import GESTURE_ACTIONS, HELD_GESTURES, expression_from_speech
from .world_actions import NEARBY_TARGET_DISTANCE, KNOWN_TARGET_DISTANCE, HELD_MIRACLE_ACTIONS, WORLD_ACTIONS
from .positions import checked_position, position_target
from .movement_pace import checked_pace
from .object_aliases import target_matches
from .object_details import known_objects, public_object, lookup_order
from .tool_docs import tool_documentation
from .model_projection import model_turn, model_objects, group_self
from .request_compaction import generated_section
from .object_inspection import InspectionView, SECTIONS, brief_object, public_targets

EATING_RECIPES = {'storage':'eat_from_storage', 'field':'eat_from_field',
                  'tree':'eat_from_tree', 'food_pile':'eat_from_food_pile'}
FISHING_RECIPES = {'self':'fish', 'god_hand':'fish_to_hand', 'player_temple':'fish_home',
                   'storage':'fish_to_storage', 'worship_site':'fish_to_worship'}
HELD_RECIPES = {'put_down':('put_down',), 'eat':('eat_held',), 'discard':('discard_held',),
                'bring_home':('bring_home',), 'examine':('examine_held',),
                'throw_at':('throw_held_at',), 'cast_miracle':HELD_MIRACLE_ACTIONS}
MOVEMENT_RECIPES = ('approach_hand','approach_object','move_to','move_to_position')
# Per-page work, separate from the number of successful results requested.
TARGET_LOOKUPS_PER_PAGE = 16


def spell_name(action):
    if action=='cast_heal_self':return 'heal'
    if action in ('cast_water_tree','cast_water_field','cast_water_object'):return 'water'
    return action.removeprefix('cast_')


def observed_spell_name(spell):
    """Translate action recipe aliases to the sensor's miracle names."""
    spell = spell.replace('_pu1', '_power_up_1').replace('_pu2', '_power_up_2')
    if spell.startswith('megablast'):
        return spell.replace('megablast', 'explosion', 1)
    return {'spiritual_shield':'shield', 'creature_nice':'creature_compassion'}.get(spell, spell)


class DirectCreatureToolSession:
    """Inspect/search objects and select actions; native IDs remain host-owned."""
    def __init__(self, supplier, *, memory=None, action_candidates=(), target_resolver=None,
                 async_target_resolver=None, async_validator=None):
        self.inner=CreatureToolSession(supplier,memory=memory,action_candidates=action_candidates)
        self.memory=memory
        self.supplier=supplier
        self.actions=self.spells=self.menu=()
        self.trace=[]
        self.position_bounds=None
        self.target_resolver=target_resolver
        self.async_target_resolver=async_target_resolver
        self.async_validator=async_validator
        self.held_menu=()
        self.inspection_pages={}
        self.target_searches={}
        self.selected_public_action=None
        self._object_metadata=None

    def bind_request(self, request):
        self.inner.bind_request(request)
        self.position_bounds=request.current.get('snapshot',{}).get('action_targets',{}).get('move_to_position',{}).get('map_size')
        self.actions=tuple(a for a in self.inner.action_candidates if a not in GESTURE_ACTIONS and a!='move_to_position')
        self.spells=tuple(dict.fromkeys(spell_name(a) for a in self.actions if a.startswith('cast_')))
        hidden=(*MOVEMENT_RECIPES,*EATING_RECIPES.values(),*FISHING_RECIPES.values(),
                *(r for values in HELD_RECIPES.values() for r in values))
        self.menu=tuple(a for a in self.actions if not a.startswith('cast_') and a not in hidden)
        if any(a in self.actions for a in EATING_RECIPES.values()):self.menu+=('eat',)
        if any(a in self.actions for a in FISHING_RECIPES.values()):self.menu+=('fish',)
        if self.spells:self.menu+=('cast_miracle',)
        self.held_menu=tuple(a for a,recipes in HELD_RECIPES.items() if any(r in self.actions for r in recipes))

    @property
    def object_movement_recipe(self):
        return next((a for a in ('move_to','approach_object') if a in self.actions),None)

    @property
    def can_move(self):
        return bool(self.object_movement_recipe or 'approach_hand' in self.actions) or bool(
            self.position_bounds and 'move_to_position' in self.inner.action_candidates)

    @property
    def search_actions(self):
        result=list(self.menu)
        if self.object_movement_recipe:result.append('move_to')
        if 'throw_at' in self.held_menu:result.append('throw_at')
        if 'cast_miracle' in self.held_menu:result.append('cast_miracle')
        return list(dict.fromkeys(result))

    def project_request(self, request):
        from .conversation import model_system_prompt
        # Engine recipes and all target leases stay in the debugger/host. The
        # model gets a small schema menu, then inspects targets only when needed.
        current=model_turn(request.current)
        current.pop('capabilities', None)
        # Gesture descriptions live only in the fixed system prompt. The host
        # still checks whether a returned cue can execute against this offer.
        capabilities={}
        if self.menu:capabilities['actions']=list(self.menu)
        if self.held_menu:capabilities['held_object_actions']=list(self.held_menu)
        if 'fish' in self.menu:
            capabilities['fishing_destinations']=[destination for destination,recipe in FISHING_RECIPES.items()
                                                   if recipe in self.actions]
        if self.can_move:
            capabilities['movement_destinations']=[name for name,available in (
                ('object_id_or_target_id',bool(self.object_movement_recipe)),
                ('god_hand','approach_hand' in self.actions),
                ('player_temple','move_to' in self.actions),
                ('coordinates',bool(self.position_bounds and 'move_to_position' in self.inner.action_candidates))) if available]
        if self.spells:
            learning=current.get('snapshot',{}).get('self',{}).get('learning',{})
            capabilities['miracle_attempts']={spell:
                'still_learning_may_fail' if observed_spell_name(spell) in learning.get('partially_learned',[]) else
                'learning_threshold_met' if observed_spell_name(spell) in learning.get('learning_threshold_met',[]) else
                'learning_unverified'
                for spell in self.spells}
        if capabilities:current['capabilities']=capabilities
        messages=[]
        for message in request.messages:
            message=dict(message)
            if message.get('role')=='system':
                # The legacy planner uses decision candidates; this facade uses
                # tools and deliberately removes candidates from observations.
                message['content']=model_system_prompt(message['content'])
                message['content']=generated_section(message['content'], 'tool-reference',
                    tool_documentation(self.definitions()))
            if message.get('role') in ('user','assistant'):
                try:message['content']=json.dumps(model_turn(json.loads(message['content'])),ensure_ascii=False)
                except (ValueError,TypeError):pass
            messages.append(message)
        if messages and messages[-1].get('role')=='user':
            messages[-1]['content']=json.dumps(current,ensure_ascii=False)
        return replace(request,current=current,messages=tuple(messages))

    def definitions(self):
        result=[deepcopy(d) for d in self.inner.definitions() if d['name'] not in ('search_actions','do_action','inspect_surrounding','find_objects','inspect_object')]
        action={'type':['string','null'],'enum':[None,*self.search_actions],'maxLength':64}
        miracle={'type':['string','null'],'enum':[None,*self.spells],'maxLength':64}
        center={'type':['string','null'],'enum':[None,'creature','god_hand'],'maxLength':16}
        result.append(schema('find_objects','Describe known objects without checking action compatibility. scope=nearby limits to 360 units, known (default) includes near and far, distant excludes nearby. center defaults to creature; god_hand uses the player hand. detail=brief (default) returns identities and essential observations; detailed adds available object details and linked towns. Start offset=0; follow next_offset. Pages retain their object list for this request; offset=0 refreshes it. Does not move or act.',
            {'type':{'type':['string','null'],'maxLength':64,
                     'description':'Type, species or synonym; null means all types. Fire/flames/blaze/burning means objects observed on fire. Canonical types and IDs are retained.'},
             'center':center,
             'scope':{'type':['string','null'],'enum':[None,'nearby','known','distant']},
             'detail':{'type':['string','null'],'enum':[None,'brief','detailed']},
             'offset':{'type':'integer','minimum':0,'maximum':4096},
             'limit':{'type':'integer','minimum':1,'maximum':20}}))
        result.append(schema('inspect_object','Inspect one or more known object, town or god IDs from the same current observation. Includes player-held objects and the leash attachment. The results list preserves requested order and identifies missing objects individually. sections=null returns all available details; selected sections retain identity and omit unrelated detail. For your own held item, also selects one random examine, shake or smell gesture (never stroke) when offered and no other action is selected; a held-item emoji in the final speech chooses its exact gesture instead; the examination result reports whether it was selected or skipped. This shares the one physical action limit. Other objects, including player-held objects, are read-only.',
            {'object_ids':{'type':'array','items':{'type':'string','minLength':1,'maxLength':96},'minItems':1,'maxItems':20},
             'sections':{'type':['array','null'],'items':{'type':'string','enum':list(SECTIONS)},
                         'maxItems':len(SECTIONS),
                         'description':'physical_traits: creature body; person: villager age, role and activity; details: resources, construction and scaffold previews; ownership: town control and god links; town_needs: storage-flag demands; belief: town belief and conversion references; miracles: bubble spell.'}}))
        result.append(schema('find_targets','Find action-compatible known objects relative to center (creature by default, or god_hand), with important objects first. Start offset=0 and follow next_offset until enough targets are found. A partial search is not proof that no compatible target exists. Optional object_id looks up a remembered identity. Use returned object_id directly in action tools; target_id is a request-local fallback for objects without an identity. Bounded known objects, not the whole island. Null action defaults to move_to; null scope means known, near and far. scope=nearby limits to 360 units; distant excludes it. source=held with action=cast_miracle searches destinations for the held bubble, with miracle=null; otherwise source=world (default) uses the learned spell. throw_at uses the held object. Does not move or act.',
            {'kind':{'type':['string','null'],'maxLength':64,
                     'description':'Type, species or synonym; null searches every compatible kind. For places to eat use action=eat with type=null or food_source. Fire/flames/blaze/burning filter objects reported as on fire, keeping their actual type and ID. Other examples: people, cattle, rocks/stones, mushrooms/fungi, toys (balls, dice, cuddly toys), miracle bubbles.'},
             'limit':{'type':'integer','minimum':1,'maximum':10},
             'offset':{'type':'integer','minimum':0,'maximum':4096},
             'scope':{'type':['string','null'],'enum':[None,'nearby','known','distant'],'maxLength':16},
             'action':action,'miracle':miracle,'center':center,
             'source':{'type':['string','null'],'enum':[None,'world','held']},
             'object_id':{'type':['string','null'],'maxLength':96}}))
        if self.menu:
            result.append(schema('do_action','Select ONE world action for this reply. target accepts an observed object_id directly: the backend resolves its current action capability without another model lookup. A fresh compatible target_id also works. eat chooses storage, field, tree or food-pile feeding from the object. fish uses target=self to catch and eat; target=god_hand catches and delivers to the hand ground position when execution starts; target=player_temple takes fish home; a storage or worship-site object_id delivers fish there. Delivery does not eat the catch. These are single native sequences; the engine finds the nearest fishing spot, so move elsewhere first to fish there. drink uses target=self: the engine finds natural water, approaches it and attempts drinking. Move near the desired river, pond or shore first; drink does not consume Water miracles or target an object ID. cast_miracle needs an offered miracle name; other actions use miracle=null. target=self is only for offered self actions/casts. Pickup acts on a world object. Movement uses move_to; held items use use_held_object. Selection is not execution or success.',
                {'action':{'type':'string','enum':list(self.menu),'minLength':1,'maxLength':64},
                 'target':{'type':['string','null'],'minLength':1,'maxLength':96},'miracle':miracle}))
        if self.held_menu:
            result.append(schema('use_held_object','Select one action using the object currently in your hand: put_down, discard, eat, bring_home, examine, throw_at or cast_miracle. For put_down/discard/eat/bring_home/examine use target=null (current held object). examine returns available held-item details and selects native physical examination in this single call, randomly choosing examine, shake or smell (never stroke). A held-item emoji in the final speech chooses its exact gesture instead, still as one action. All these gestures require something in your own hand. inspect_object on your own held item does the same. Details are observations, not proof of animation playback. inspect_self remains read-only. put_down places gently; discard tosses it away without aiming, with engine-controlled direction and landing. bring_home carries the held item to your native home and releases it there in one request; the bridge selects the native food, toy or general-object routine. It does not fetch an unheld object or supply a village store. No separate movement or put-down call is needed. throw_at and cast_miracle accept an observed destination object_id directly, or a fresh compatible target_id; cast_miracle also accepts self if offered. find_targets(action=throw_at or cast_miracle, source=held, miracle=null) can find compatible destinations when needed. The held bubble determines its spell and is consumed even if the cast fails. Empty hands alone do not prove eating or delivery home. Shares the one physical action limit with all other action tools.',
                {'action':{'type':'string','enum':list(self.held_menu)},
                 'target':{'type':['string','null'],'minLength':1,'maxLength':96}}))
        if self.can_move:
            targets=[{'type':'string','minLength':1,'maxLength':96}]
            if self.position_bounds and 'move_to_position' in self.inner.action_candidates:
                targets.append({'type':'object','properties':{axis:{'type':'number','exclusiveMinimum':0,
                    'exclusiveMaximum':self.position_bounds[axis]} for axis in ('x','z')},
                    'required':['x','z'],'additionalProperties':False})
            result.append(schema('move_to','Move to target: an observed object_id, a fresh movement target_id fallback, god_hand, player_temple, or explicit {x,z} coordinates when offered. Uses the same target IDs as other action tools. Prefer IDs for objects. Come to me means the live god_hand; auto runs toward it. Else auto uses native pace. Coordinates are fixed locations, never inferred from an object you can target by ID. Movement does not pick up or interact; arrival is confirmed separately. Shares the one physical action limit.',
                {'target':{'anyOf':targets},
                 'pace':{'type':['string','null'],'enum':[None,'auto','walk','run'],'maxLength':4}}))
        # Action semantics come from the same metadata as the engine recipe
        # catalog, not a second handwritten list in the system prompt.
        for definition in result:
            if definition['name']=='do_action':
                definition['description']+=' '+ ' '.join(a+': '+WORLD_ACTIONS[a] for a in self.menu if a in WORLD_ACTIONS)
            props=definition['parameters']['properties']
            if 'kind' in props:
                props['type']=props.pop('kind')
                definition['parameters']['required']=[('type' if key=='kind' else key)
                    for key in definition['parameters'].get('required',[])]
                definition['description']=definition['description'].replace('kind=', 'type=')
        return result

    def _recipes(self, action, miracle, source=None):
        if action=='fish':return [r for r in FISHING_RECIPES.values() if r in self.actions] if miracle is None else []
        if action=='move_to':return [self.object_movement_recipe] if self.object_movement_recipe and miracle is None else []
        if action=='eat':return [a for a in EATING_RECIPES.values() if a in self.actions] if miracle is None else []
        if action=='throw_at':return ['throw_held_at'] if 'throw_held_at' in self.actions and miracle is None else []
        if action=='cast_miracle' and source=='held':
            return [a for a in self.actions if a in HELD_MIRACLE_ACTIONS] if miracle is None else []
        if action=='cast_miracle':
            return [a for a in self.actions if a.startswith('cast_') and spell_name(a)==miracle]
        return [action] if action in self.actions and miracle is None else []

    def _targets(self, action):
        value=self.inner.action_targets.get(action)
        if value is None:return []
        targets=deepcopy(value.get('objects',value.get('destinations',[value])))
        return self._describe_targets(targets)

    def _describe_targets(self, targets):
        if self._object_metadata is None:
            frame=self.supplier() or {}
            surroundings=(frame.get('observation') or {}).get('surroundings') or {}
            self._object_metadata={o['object_id']:public_object(o) for o in known_objects(surroundings)}
        metadata=self._object_metadata
        for target in targets:
            # Preserve native capability metadata; supplement only descriptions.
            for key in ('object_type','species','physical_traits','person','details','town_id','on_fire','engine_class','portal'):
                if key in metadata.get(target.get('object_id'),{}):
                    target[key]=metadata[target['object_id']][key]
            target['name']=public_object(target)['name']
        return targets

    def _validate_arguments(self, name, arguments):
        self.inner.validate()
        definition=next((d for d in self.definitions() if d['name']==name),None)
        if definition is None:raise ToolArgumentError('tool_not_offered')
        if type(arguments) is not dict:raise ToolArgumentError('expected_argument_object')
        args=dict(arguments);props=definition['parameters']['properties']
        # Accept older local callers, but advertise one name to models.
        if 'type' in props and 'kind' in args and 'type' not in args:args['type']=args.pop('kind')
        for key,rule in props.items():
            if key not in args and 'null' in rule.get('type',[]):args[key]=None
        if name in ('find_objects','find_targets'):
            args.setdefault('offset',0);args.setdefault('limit',10)
        if args.keys()!=props.keys():raise ToolArgumentError('unexpected_or_missing_argument')
        for key,value in args.items():
            rule=props[key]
            if name=='move_to' and key=='target':continue  # Validated by _move.
            if 'enum' in rule and value not in rule['enum']:raise ToolArgumentError('value_not_offered',parameter=key)
            if value is None and 'null' in rule.get('type',[]):continue
            if 'array' in rule['type']:
                if not isinstance(value,list) or not rule.get('minItems',0)<=len(value)<=rule['maxItems']:
                    raise ToolArgumentError('invalid_list_length',parameter=key)
                item=rule['items']
                if any(not isinstance(v,str) or not item.get('minLength',0)<=len(v.strip())<=item.get('maxLength',96)
                       or ('enum' in item and v not in item['enum']) for v in value):
                    raise ToolArgumentError('invalid_list_value',parameter=key)
            elif rule['type']=='integer':
                if type(value) is not int or not rule['minimum']<=value<=rule['maximum']:raise ToolArgumentError('invalid_limit',parameter=key)
            elif not isinstance(value,str) or not rule.get('minLength',0)<=len(value.strip())<=rule.get('maxLength',96):
                raise ToolArgumentError('invalid_argument_type_or_length',parameter=key)
        if 'type' in args:args['kind']=args.pop('type')
        return args

    def _already_selected(self):
        return {'status':'action_already_selected','action':self.selected_public_action,'executed':False}

    def _examine_held(self, view, target=None):
        held = view.raw.get('holding') or {}
        identity = held.get('object_id') if held.get('holding') is True else None
        inspection = view.describe(identity) if identity else {
            'status':'held_details_unavailable', 'executed':False}
        if not identity and held.get('holding') is True:
            inspection['object'] = public_object(held)
        recipes = ['examine_held'] if 'examine_held' in self.actions else []
        # Match the inspected item to this turn's held capability. A newer
        # pickup must not receive the animation selected for an earlier item.
        bound = self.inner.action_targets.get('examine_held') or {}
        if recipes and view.held_generation != bound.get('held_generation'):
            result = {'status':'held_object_changed', 'executed':False}
        else:
            result = yield from self._select('use_held_object:examine', recipes, target)
        return {**result, 'inspection':inspection}

    def _select(self, public_action, recipes, target, pace=None):
        if self.inner.selected_action is not None:return self._already_selected()
        # Ordinary IDs work for every targeted action. Prefer the already bound
        # capability; reserve only missing ones. No extra game call for names,
        # self, held items, or legacy request-local references.
        if isinstance(target,str) and target not in ('self','player_temple','god_hand'):
            for recipe in recipes:
                found=next((t for t in self._targets(recipe) if t.get('object_id')==target
                            and self._recipes_for_object([recipe],t)),None)
                if found:
                    return (yield from self._select(public_action,[recipe],found['target_id'],pace))
            if self.target_resolver and not any(t.get('target_id')==target for r in recipes for t in self._targets(r)):
                frame=self.supplier() or {}
                obj=next((o for o in known_objects((frame.get('observation') or {}).get('surroundings') or {})
                          if o['object_id']==target),None)
                if recipes:
                    try:
                        for recipe in (self._recipes_for_object(recipes,obj) if obj else recipes):
                            targets=yield from self._reserve(target,recipe)
                            if targets:return (yield from self._select(public_action,[recipe],targets[0]['target_id'],pace))
                    except ToolContextUnavailable:raise
                    except (OSError,ValueError,TimeoutError) as error:
                        return {'status':'target_lookup_failed','reason':str(error),'executed':False}
        chosen=None
        if 'pick_up' in recipes:
            blocked=next((t for t in self._targets('pick_up') if t.get('target_id')==target and t.get('pickup_possible') is False),None)
            if blocked is not None:
                return {'status':'pickup_not_possible','executed':False,
                        'reason':blocked.get('pickup_reason','engine_pickup_not_allowed'),'target':blocked}
        for recipe in recipes:
            candidates=self._targets(recipe)
            if recipe in EATING_RECIPES.values():
                candidates=[t for t in candidates if EATING_RECIPES.get(t.get('kind'))==recipe]
                if target not in {t.get('target_id') for t in candidates}:continue
            if target=='player_temple':
                homes=[t['target_id'] for t in candidates if t['kind']=='temple' and t.get('player_temple') is True]
                if len(homes)==1:chosen=recipe;target=homes[0];break
            if target in self.inner._action_description(recipe)['target_options']:
                chosen=recipe;break
            if target=='self':
                own=[t['target_id'] for t in candidates if t['kind']=='self']
                if len(own)==1:chosen=recipe;target=own[0];break
        if chosen is None:return {'status':'invalid_target' if recipes else 'not_offered','executed':False}
        result=self.inner.call('do_action',{'action':chosen,'target':target})
        if result.get('status')=='selected_pending_final_reply':
            self.selected_public_action=public_action
            self.inner.selected_pace=pace
            if pace is not None:result['pace']=pace
            obj=next((t for t in self._targets(chosen) if t.get('target_id')==result.get('target')),None)
            if obj:result['selected_object']=public_object(obj)
            elif chosen=='approach_hand':result['selected_object']={'kind':'god_hand','name':'god hand'}
        return {**result,'action':public_action}

    def _reserve(self, identity, recipe):
        if self.target_resolver is None:return []
        added=[t for t in (yield (identity,recipe)) if t.get('object_id')==identity]
        for target in added:target['name']=public_object(target)['name']
        if added:
            key='destinations' if recipe=='throw_held_at' else 'objects'
            entries=self.inner.action_targets.setdefault(recipe,{key:[]})[key]
            existing={t['target_id'] for t in entries}
            entries.extend(deepcopy(t) for t in added if t['target_id'] not in existing)
        return self._describe_targets(added)

    def _move(self, args):
        target=args['target'];pace=args['pace']
        try:checked_pace('move_to',pace)
        except ValueError as error:raise ToolArgumentError(str(error),parameter='pace') from error
        if isinstance(target,dict):
            if set(target)!={'x','z'}:raise ToolArgumentError('choose_one_target')
            if 'move_to_position' not in self.inner.action_candidates:raise ToolArgumentError('coordinates_not_offered')
            try:point=checked_position(target,self.position_bounds)
            except ValueError as error:raise ToolArgumentError('invalid_map_coordinates') from error
            if self.inner.selected_action is not None:return self._already_selected()
            self.inner.selected_action='move_to_position'
            self.inner.selected_target=position_target(point);self.inner.selected_pace=pace
            self.selected_public_action='move_to'
            return {'status':'selected_pending_final_reply','action':'move_to','position':point,
                    'pace':pace,'executed':False,'arrival_confirmed':False}
        if not isinstance(target,str) or not 0<len(target.strip())<=96:raise ToolArgumentError('invalid_target')
        if self.inner.selected_action is not None:return self._already_selected()
        if target=='god_hand':
            recipes=['approach_hand'] if 'approach_hand' in self.actions else []
        else:
            recipes=self._recipes('move_to',None)
        # Reuse an already bound capability by ID, just like world/held actions.
        return (yield from self._select('move_to',recipes,target,pace))

    @staticmethod
    def _target_key(obj):
        return obj.get('object_id') or obj.get('target_id')

    def _recipes_for_object(self, recipes, obj):
        return [r for r in recipes
                if (r not in EATING_RECIPES.values() or EATING_RECIPES.get(obj.get('kind'))==r)
                and (r not in FISHING_RECIPES.values() or FISHING_RECIPES.get(obj.get('kind'))==r)]

    def _find_objects(self, args, frame):
        surroundings=(frame.get('observation') or {}).get('surroundings') or {}
        center=args['center'] or 'creature';scope=args['scope'] or 'known'
        distance='distance_to_god_hand_engine_units' if center=='god_hand' else 'distance_engine_units'
        key=(args['kind'],center,scope)
        if args['offset']==0 or key not in self.inspection_pages:
            objects=[o for o in known_objects(surroundings)
                if target_matches(args['kind'],o) and type(o.get(distance)) in (int,float)
                and (scope!='nearby' or o[distance]<=NEARBY_TARGET_DISTANCE)
                and (scope!='distant' or o[distance]>NEARBY_TARGET_DISTANCE)]
            objects.sort(key=lambda o:lookup_order(o,distance,explicit_kind=args['kind'] is not None))
            self.inspection_pages[key]={'objects':deepcopy(objects),'towns':deepcopy(surroundings.get('towns',[]))}
        cached=self.inspection_pages[key];start=args['offset'];end=start+args['limit']
        detailed=args['detail']=='detailed'
        describe=public_object if detailed else brief_object
        objects=[describe(o) for o in cached['objects'][start:end]]
        result={'objects':objects,'center':center,'scope':scope,'detail':'detailed' if detailed else 'brief',
                'matching_known_objects':len(cached['objects']),'offset':start,
                'next_offset':end if end<len(cached['objects']) else None,
                'coverage':'current_known_objects_not_entire_island','executed':False}
        available=surroundings.get('god_hand_available' if center=='god_hand' else 'available')
        if isinstance(available,bool):result['available']=available
        if detailed:
            town_ids={o.get('town_id') for o in objects if o.get('town_id')}
            towns=[deepcopy(t) for t in cached['towns'] if t.get('town_id') in town_ids]
            if towns:result['towns']=towns
        return result

    def _find(self, args, frame, recipes):
        center=args['center'] or 'creature';distance='distance_to_god_hand_engine_units' if center=='god_hand' else 'distance_engine_units'
        def matches(obj):
            return (target_matches(args['kind'],obj) and (not args['object_id'] or obj.get('object_id')==args['object_id'])
                and (center=='creature' or type(obj.get(distance)) in (int,float))
                and (args['scope']!='distant' or obj.get(distance,0)>NEARBY_TARGET_DISTANCE)
                and (args['scope']!='nearby' or (type(obj.get(distance)) in (int,float) and obj[distance]<=NEARBY_TARGET_DISTANCE)))
        offered={}
        for recipe in recipes:
            if recipe in ('fish_to_hand','fish_home'):continue  # Named destinations, not self-object search results.
            for target in self._targets(recipe):
                if matches(target):offered.setdefault(self._target_key(target),[]).append(target)
        key=(args['kind'],center,args['action'],args['miracle'],args['scope'],args['object_id'],args['source'])
        if args['offset']==0 or key not in self.target_searches:
            raw=frame.get('observation') or {}
            objects={o['object_id']:deepcopy(o) for o in known_objects(raw.get('surroundings') or {})
                     if matches(o) and self._recipes_for_object(recipes,o)}
            for identity,choices in offered.items():objects.setdefault(identity,deepcopy(choices[0]))
            self.target_searches[key]=sorted(objects.items(),key=lambda item:
                lookup_order(item[1],distance,explicit_kind=args['kind'] is not None))
        objects=self.target_searches[key]
        targets=[];errors=[];unavailable=[];lookups=0;cursor=args['offset']
        while cursor<len(objects) and len(targets)<args['limit']:
            identity,obj=objects[cursor]
            choices=offered.get(identity,[])
            if not choices and obj.get('object_id'):
                compatible=self._recipes_for_object(recipes,obj)
                if lookups+len(compatible)>TARGET_LOOKUPS_PER_PAGE:break
                for recipe in compatible:
                    lookups+=1
                    try:choices.extend(t for t in (yield from self._reserve(obj['object_id'],recipe)) if matches(t))
                    except ToolContextUnavailable:raise
                    except (OSError,ValueError,TimeoutError) as error:errors.append(str(error))
            cursor+=1
            usable=[t for t in choices if not (args['action']=='pick_up' and t.get('pickup_possible') is False)]
            if usable:targets.append(usable[0])
            elif choices and len(unavailable)<3:
                unavailable.append(deepcopy(choices[0]))
        result={'action':args['action'],'source':args['source'] or 'world','scope':args['scope'] or 'known','center':center,
            'coverage':'bounded_sample_of_creature_known_objects_not_entire_island',
            'nearby_radius_engine_units':NEARBY_TARGET_DISTANCE,'maximum_search_distance_engine_units':KNOWN_TARGET_DISTANCE,
            'reachability_verified':False,'executed':False,
            'matching_known_objects':len(objects),'offset':args['offset'],
            'searched_objects':max(0,cursor-args['offset']),
            'next_offset':cursor if cursor<len(objects) else None,'search_complete':cursor>=len(objects)}
        if targets:
            result['action_targets']=targets
            result['more_offered_targets']=sum(any(args['action']!='pick_up' or t.get('pickup_possible') is not False
                for t in offered.get(identity,())) for identity,_ in objects[cursor:])
        if unavailable:result['unavailable_targets']=unavailable
        if errors:result['target_lookup']={'status':'lookup_failed','reasons':errors}
        if not targets and result['next_offset'] is not None:
            result.update(status='search_incomplete',reason='No compatible target on this page. Continue with next_offset to check the remaining known objects.')
            return result
        if not targets and errors:
            result.update(status='target_lookup_failed',reason='Some targets could not be checked. Retry this page; this is not proof that no compatible target exists.')
            return result
        return self._search_status(result,args,recipes,targets,nearby=args['scope']=='nearby')

    @staticmethod
    def _search_status(result,args,recipes,targets,*,nearby):
        if args['action']:
            result['action']=args['action']
            if args['miracle']:result['miracle']=args['miracle']
            if not targets:
                result['status']=('action_unavailable' if not recipes else 'no_compatible_target_nearby' if nearby else 'no_matching_known_target')
                result['reason']=('This action is not available for this request.' if not recipes else
                    'No compatible target nearby. Use scope=known to check farther known objects.' if nearby else
                    'No matching action-compatible target in the current known-object sample. This does not establish absence elsewhere on the island.')
        return result

    def call(self, name, arguments):
        operation=self._call(name,arguments)
        try:
            lookup=next(operation)
            while True:
                try:result=self.target_resolver(*lookup)
                except (OSError,ValueError,TimeoutError) as error:lookup=operation.throw(error)
                else:lookup=operation.send(result)
        except StopIteration as result:return result.value
        finally:operation.close()

    async def call_async(self, name, arguments):
        await self.validate_async()
        if self.async_target_resolver is None:return self.call(name,arguments)
        # Share tool semantics with synchronous clients, yielding only native
        # lookups so observation ingestion can continue. No tool is replayed.
        operation=self._call(name,arguments)
        try:
            lookup=next(operation)
            while True:
                try:
                    result=await self.async_target_resolver(*lookup)
                    await self.validate_async()
                except (OSError,ValueError,TimeoutError) as error:lookup=operation.throw(error)
                else:lookup=operation.send(result)
        except StopIteration as result:return result.value
        finally:operation.close()

    def _call(self, name, arguments):
        # Descriptions are shared across recipes within one tool call only.
        # The next call reads the latest frame; no growing/session-stale cache.
        self._object_metadata=None
        # Accept the former local movement/single-inspection syntax without
        # advertising multiple shapes to the model.
        legacy_single=name=='inspect_object' and isinstance(arguments,dict) and 'object_id' in arguments
        supplied=arguments
        if legacy_single:
            if 'object_ids' in arguments:raise ToolArgumentError('choose_one_id_argument')
            arguments={**arguments,'object_ids':[arguments['object_id']]};arguments.pop('object_id')
        if name=='move_to' and isinstance(arguments,dict) and 'destination' in arguments and 'target' not in arguments:
            arguments=dict(arguments);old=arguments.pop('destination')
            if isinstance(old,dict) and len(old)==1 and next(iter(old)) in ('object_id','target_id','special'):
                if 'special' in old and old['special'] not in ('god_hand','player_temple'):
                    raise ToolArgumentError('unknown_special_destination')
                old=next(iter(old.values()))
            arguments['target']=old
        if name not in ('move_to','use_held_object','do_action','find_objects','inspect_object','find_targets','inspect_self'):
            if name not in {d['name'] for d in self.definitions()}:raise ToolArgumentError('tool_not_offered')
            result=self.inner.call(name,arguments)
        else:
            args=self._validate_arguments(name,arguments)
            if name=='inspect_self':
                from .self_inspection import inspect_self_frame
                result=inspect_self_frame(self.supplier())
            elif name=='inspect_object':
                view=InspectionView(self.supplier())
                entries=[{'object_id':identity,**view.describe(identity,args['sections'])} for identity in args['object_ids']]
                held=view.raw.get('holding') or {}
                own_id=held.get('object_id') if held.get('holding') is True else None
                own_entry=next((entry for entry in entries if own_id and entry['object_id']==own_id),None)
                if own_entry is not None:
                    examination=yield from self._examine_held(view)
                    examination.pop('inspection',None)  # Details are already in the requested section view.
                    own_entry['examination']=examination
                result=entries[0] if legacy_single else {'results':entries,'executed':False}
            elif name=='find_objects':result=self._find_objects(args,self.supplier())
            elif name=='move_to':result=yield from self._move(args)
            elif name=='use_held_object':
                if args['action']=='examine':
                    result=yield from self._examine_held(InspectionView(self.supplier()),args['target'])
                else:
                    recipes=[r for r in HELD_RECIPES[args['action']] if r in self.actions]
                    result=yield from self._select('use_held_object:'+args['action'],recipes,args['target'])
            else:
                action=args['action'];source=args.get('source') or 'world'
                if name=='find_targets' and action is None:action='move_to';args['action']=action
                if source=='held' and action not in ('cast_miracle','throw_at'):
                    raise ToolArgumentError('held_source_requires_cast_or_throw',parameter='source')
                if (action=='cast_miracle' and source!='held') != (args['miracle'] is not None):
                    raise ToolArgumentError('specify_miracle_only_for_learned_cast',parameter='miracle')
                recipes=self._recipes(action,args['miracle'],source)
                if name=='do_action':
                    target=args['target']
                    if action=='fish' and target in ('self','god_hand','player_temple'):
                        recipe=FISHING_RECIPES[target]
                        result=yield from self._select(action,[recipe] if recipe in recipes else [],'self')
                        result['destination']=target
                        if target!='self':
                            result.pop('selected_object',None)
                    else:result=yield from self._select(action,recipes,target)
                    if args['miracle']:result['miracle']=args['miracle']
                else:result=yield from self._find(args,self.supplier(),recipes)
        result=public_targets(model_objects(result))
        if name=='inspect_self':result['self']=group_self(result['self'])
        arguments=supplied
        self.trace.append({'tool':name,'arguments':deepcopy(arguments),'result':deepcopy(result)})
        return result

    def validate(self):self.inner.validate()
    async def validate_async(self):
        if self.async_validator is not None:await self.async_validator()
        else:self.validate()
    def refresh_memory(self, system):return self.inner.refresh_memory(system)
    def reply_correction(self, result):
        # Catch casting promises, including natural phrasing such as watering.
        # This only asks the model to correct itself; it never invents a command.
        if self.inner.selected_action is not None:return None
        spoken=' '.join(result.get(key) or '' for key in ('answer','say'))
        future=r"\b(?:I['’]ll|I will|I am going to|I['’]m going to)\s+(?:(?:try|attempt)\s+to\s+)?(?:cast|water|heal|summon)\b"
        casting=r"(?:^|[.!?]\s+)(?:Casting|Watering|Healing|Summoning)\b"
        acknowledgement=r"\b(?:water|heal|food|wood|forest|fireball|lightning|miracle)\b[^.!?]{0,100}\bcoming right up\b"
        movement=r"\b(?:I['’]m|I am)\s+(?:coming|on my way|heading (?:over|back)|walking|moving (?:to|towards?|back)|returning)\b"
        movement_future=r"\b(?:I['’]ll|I will|I['’]m going to|I am going to)\s+(?:come|walk|move|run|go|return|head|approach)\b"
        if any(re.search(pattern,spoken,re.I) for pattern in (movement,movement_future)):
            return ('No action has been selected. Your draft promises movement, but speech does not move you. '
                    'For come here/come to me, select move_to with target="god_hand". '
                    'Use move_to with target="the observed ID" or target={"x":X,"z":Z} for explicit coordinates. '
                    'Select the requested supported action before answering. If it is unavailable, explain that honestly '
                    'instead of promising to move. The bridge can interrupt sleep for movement.')
        if not any(re.search(pattern,spoken,re.I) for pattern in (future,casting,acknowledgement)):
            return None
        return ('No action has been selected. Your draft promises to cast, but text cannot cast a miracle. '
                'Use do_action with an offered spell and the known object_id directly; no extra lookup is needed. '
                'If no suitable ID is known, use find_targets to find one first. '
                'If the requested target or spell is unavailable, state that honestly; you may explain '
                'and select a useful supported alternative. Do not repeat an unsupported casting promise.')
    def finalize_reply(self, result):
        reply = self.inner.finalize_reply(result)
        if self.inner.selected_action == 'examine_held':
            # Inspection and its speech cue select one held-bound command, before
            # dispatch. An explicit cue never adds a second gesture or falls back
            # to a random one when its capability is unavailable.
            spoken = result.get('say')
            cue = expression_from_speech(result.get('answer') if spoken is None else spoken)
            if cue and cue['candidate'] in HELD_GESTURES:
                candidate = cue['candidate'] if cue['candidate'] in self.inner.action_candidates else None
                reply = {**reply, 'decision':{'candidate':candidate}}
        return reply
