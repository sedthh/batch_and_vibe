"""Small model-facing tool menu over the existing request-bound action executor."""
from copy import deepcopy
from dataclasses import replace
import json
import re

from .creature_tools import CreatureToolSession, ToolArgumentError, ToolContextUnavailable, schema
from .expressions import GESTURE_ACTIONS
from .world_actions import NEARBY_TARGET_DISTANCE, KNOWN_TARGET_DISTANCE, HELD_MIRACLE_ACTIONS, WORLD_ACTIONS
from .positions import checked_position, position_target
from .movement_pace import checked_pace
from .object_aliases import target_matches
from .object_details import known_objects, public_object, lookup_order
from .tool_docs import tool_documentation
from .model_projection import model_turn, model_objects

EATING_RECIPES = {'storage':'eat_from_storage', 'field':'eat_from_field',
                  'tree':'eat_from_tree', 'food_pile':'eat_from_food_pile'}
HELD_RECIPES = {'put_down':('put_down',), 'eat':('eat_held',), 'discard':('discard_held',),
                'throw_at':('throw_held_at',), 'cast_miracle':HELD_MIRACLE_ACTIONS}
MOVEMENT_RECIPES = ('approach_hand','approach_object','move_to','move_to_position')


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
        self.selected_public_action=None
        self._object_metadata=None

    def bind_request(self, request):
        self.inner.bind_request(request)
        self.position_bounds=request.current.get('snapshot',{}).get('action_targets',{}).get('move_to_position',{}).get('map_size')
        self.actions=tuple(a for a in self.inner.action_candidates if a not in GESTURE_ACTIONS and a!='move_to_position')
        self.spells=tuple(dict.fromkeys(spell_name(a) for a in self.actions if a.startswith('cast_')))
        hidden=(*MOVEMENT_RECIPES,*EATING_RECIPES.values(),*(r for values in HELD_RECIPES.values() for r in values))
        self.menu=tuple(a for a in self.actions if not a.startswith('cast_') and a not in hidden)
        if any(a in self.actions for a in EATING_RECIPES.values()):self.menu+=('eat',)
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
        if 'cast_miracle' in self.held_menu:result.append('cast_held_miracle')
        return result

    def project_request(self, request):
        from .conversation import model_system_prompt
        # Engine recipes and all target leases stay in the debugger/host. The
        # model gets a small schema menu, then inspects targets only when needed.
        current=model_turn(request.current)
        # Gesture descriptions live only in the fixed system prompt. The host
        # still checks whether a returned cue can execute against this offer.
        capabilities={}
        if self.menu:capabilities['actions']=list(self.menu)
        if self.held_menu:capabilities['held_object_actions']=list(self.held_menu)
        if self.can_move:
            capabilities['movement_destinations']=[name for name,available in (
                ('object_id_or_target_id',bool(self.object_movement_recipe)),
                ('god_hand','approach_hand' in self.actions),
                ('coordinates',bool(self.position_bounds and 'move_to_position' in self.inner.action_candidates))) if available]
        if self.spells:
            learning=current.get('snapshot',{}).get('self',{}).get('miracles',{})
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
                message['content']+='\n\n'+tool_documentation(self.definitions())
            if message.get('role') in ('user','assistant'):
                try:message['content']=json.dumps(model_turn(json.loads(message['content'])),ensure_ascii=False)
                except (ValueError,TypeError):pass
            messages.append(message)
        if messages and messages[-1].get('role')=='user':
            messages[-1]['content']=json.dumps(current,ensure_ascii=False)
        return replace(request,current=current,messages=tuple(messages))

    def definitions(self):
        result=[deepcopy(d) for d in self.inner.definitions() if d['name'] not in ('search_actions','do_action','inspect_surrounding')]
        action={'type':['string','null'],'enum':[None,*self.search_actions],'maxLength':64}
        miracle={'type':['string','null'],'enum':[None,*self.spells],'maxLength':64}
        center={'type':['string','null'],'enum':[None,'creature','god_hand'],'maxLength':16}
        result.append(schema('inspect_surrounding','Inspect within 360 units of creature (default) or god_hand. Start offset=0; follow next_offset for more. Pages retain the same object list for this request. Includes detailed people, objects and linked towns; optional action/miracle returns compatible targets for this page. Inspection never moves or acts.',
            {'kind':{'type':['string','null'],'maxLength':64,
                     'description':'Type, species or synonym: people, cattle, bushes, stones, toys, fungi, grain, wood, bubbles, warehouse. Fire/flames/blaze/burning filter objects reported as on fire, keeping their actual type and ID. Null means all types.'},
             'limit':{'type':'integer','minimum':1,'maximum':10},
             'offset':{'type':'integer','minimum':0,'maximum':1024},
             'action':action,'miracle':miracle,'center':center}))
        result.append(schema('find_targets','Find action-compatible known objects relative to center (creature by default, or god_hand). Optional object_id looks up a remembered identity. Use returned object_id directly in action tools; target_id is a request-local fallback for objects without an identity. Bounded known objects, not the whole island. Null action defaults to move_to; null scope means known, near and far. Does not move or act.',
            {'kind':{'type':['string','null'],'maxLength':64,
                     'description':'Type, species or synonym; null searches every compatible kind. For places to eat use action=eat with kind=null or food_source. Fire/flames/blaze/burning filter objects reported as on fire, keeping their actual type and ID. Other examples: people, cattle, rocks/stones, mushrooms/fungi, toys, miracle bubbles.'},
             'limit':{'type':'integer','minimum':1,'maximum':10},
             'scope':{'type':['string','null'],'enum':[None,'known','distant'],'maxLength':16},
             'action':action,'miracle':miracle,'center':center,
             'object_id':{'type':['string','null'],'maxLength':64}}))
        if self.menu:
            result.append(schema('do_action','Select ONE world action for this reply. target accepts an observed object_id directly: the backend resolves its current action capability without another model lookup. A fresh compatible target_id also works. eat chooses storage, field, tree or food-pile feeding from the object. fish uses target=self and finds the nearest fishing spot; move elsewhere first to fish there. cast_miracle needs an offered miracle name; other actions use miracle=null. target=self is only for offered self actions/casts. Pickup acts on a world object. Movement uses move_to; held items use use_held_object. Selection is not execution or success.',
                {'action':{'type':'string','enum':list(self.menu),'minLength':1,'maxLength':64},
                 'target':{'type':['string','null'],'minLength':1,'maxLength':64},'miracle':miracle}))
        if self.held_menu:
            result.append(schema('use_held_object','Select one action using the object currently in your hand: put_down, discard, eat, throw_at or cast_miracle. For put_down/discard/eat use target=null (current held object). put_down places gently; discard tosses it away without aiming, with engine-controlled direction and landing. throw_at and cast_miracle accept an observed destination object_id directly, or a fresh compatible target_id; cast_miracle also accepts self if offered. find_targets(action=throw_at or cast_held_miracle) can find compatible destinations when needed. The held bubble determines its spell and is consumed even if the cast fails. Empty hands alone do not prove eating. Shares the one physical action limit with all other action tools.',
                {'action':{'type':'string','enum':list(self.held_menu)},
                 'target':{'type':['string','null'],'minLength':1,'maxLength':64}}))
        if self.can_move:
            destinations=[]
            if self.object_movement_recipe:
                for key in ('object_id','target_id'):
                    destinations.append({'type':'object','properties':{key:{'type':'string','minLength':1,'maxLength':96}},
                                         'required':[key],'additionalProperties':False})
            specials=(['god_hand'] if 'approach_hand' in self.actions else [])+(['player_temple'] if 'move_to' in self.actions else [])
            if specials:
                destinations.append({'type':'object','properties':{'special':{'type':'string','enum':specials}},
                                     'required':['special'],'additionalProperties':False})
            if self.position_bounds and 'move_to_position' in self.inner.action_candidates:
                destinations.append({'type':'object','properties':{axis:{'type':'number','exclusiveMinimum':0,
                    'exclusiveMaximum':self.position_bounds[axis]} for axis in ('x','z')},
                    'required':['x','z'],'additionalProperties':False})
            result.append(schema('move_to','Move to exactly one destination: object_id (resolved by the backend), fresh movement target_id, special god_hand/player_temple, or explicit x/z coordinates. Prefer IDs for objects. Come to me means the live god_hand; auto runs toward it. Else auto uses native pace. Coordinates are fixed locations, never inferred from an object you can target by ID. Movement does not pick up or interact; arrival is confirmed separately. Shares the one physical action limit.',
                {'destination':{'anyOf':destinations},
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

    def _recipes(self, action, miracle):
        if action=='move_to':return [self.object_movement_recipe] if self.object_movement_recipe and miracle is None else []
        if action=='eat':return [a for a in EATING_RECIPES.values() if a in self.actions] if miracle is None else []
        if action=='throw_at':return ['throw_held_at'] if 'throw_held_at' in self.actions and miracle is None else []
        if action=='cast_held_miracle':
            return [a for a in self.actions if a in HELD_MIRACLE_ACTIONS] if miracle is None else []
        if action=='cast_miracle':
            return [a for a in self.actions if a.startswith('cast_') and spell_name(a)==miracle]
        return [action] if action in self.actions and miracle is None else []

    def _targets(self, action):
        value=self.inner.action_targets.get(action)
        if value is None:return []
        targets=deepcopy(value.get('objects',value.get('destinations',[value])))
        if self._object_metadata is None:
            frame=self.supplier() or {}
            surroundings=(frame.get('observation') or {}).get('surroundings') or {}
            self._object_metadata={o['object_id']:public_object(o) for o in known_objects(surroundings)}
        metadata=self._object_metadata
        for target in targets:
            # Preserve native capability metadata; supplement only descriptions.
            for key in ('species','physical_traits','person','details','town_id','on_fire'):
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
        if name=='inspect_surrounding':args.setdefault('offset',0)
        if args.keys()!=props.keys():raise ToolArgumentError('unexpected_or_missing_argument')
        for key,value in args.items():
            rule=props[key]
            if key=='destination':continue  # Validated as an exclusive union by _move.
            if 'enum' in rule and value not in rule['enum']:raise ToolArgumentError('value_not_offered',parameter=key)
            if value is None and 'null' in rule.get('type',[]):continue
            if rule['type']=='integer':
                if type(value) is not int or not rule['minimum']<=value<=rule['maximum']:raise ToolArgumentError('invalid_limit',parameter=key)
            elif not isinstance(value,str) or not rule.get('minLength',0)<=len(value.strip())<=rule.get('maxLength',96):
                raise ToolArgumentError('invalid_argument_type_or_length',parameter=key)
        if 'type' in args:args['kind']=args.pop('type')
        return args

    def _already_selected(self):
        return {'status':'action_already_selected','action':self.selected_public_action,'executed':False}

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
                if obj:
                    try:
                        for recipe in self._recipes_for_object(recipes,obj):
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
        return added

    def _move(self, args):
        destination=args['destination'];pace=args['pace']
        if type(destination) is not dict:raise ToolArgumentError('invalid_destination')
        keys=set(destination)
        try:checked_pace('move_to',pace)
        except ValueError as error:raise ToolArgumentError(str(error),parameter='pace') from error
        if keys=={'x','z'}:
            if 'move_to_position' not in self.inner.action_candidates:raise ToolArgumentError('coordinates_not_offered')
            try:point=checked_position(destination,self.position_bounds)
            except ValueError as error:raise ToolArgumentError('invalid_map_coordinates') from error
            if self.inner.selected_action is not None:return self._already_selected()
            self.inner.selected_action='move_to_position'
            self.inner.selected_target=position_target(point);self.inner.selected_pace=pace
            self.selected_public_action='move_to'
            return {'status':'selected_pending_final_reply','action':'move_to','position':point,
                    'pace':pace,'executed':False,'arrival_confirmed':False}
        if keys not in ({'object_id'},{'target_id'},{'special'}):raise ToolArgumentError('choose_one_destination')
        value=next(iter(destination.values()))
        if not isinstance(value,str) or not 0<len(value.strip())<=96:raise ToolArgumentError('invalid_destination')
        if self.inner.selected_action is not None:return self._already_selected()
        if keys=={'special'}:
            if value=='god_hand':return (yield from self._select('move_to',['approach_hand'] if 'approach_hand' in self.actions else [],'god_hand',pace))
            if value=='player_temple':return (yield from self._select('move_to',self._recipes('move_to',None),'player_temple',pace))
            raise ToolArgumentError('unknown_special_destination')
        recipe=self.object_movement_recipe
        if not recipe:return {'status':'not_offered','executed':False}
        if keys=={'object_id'}:
            try:
                targets=(yield from self._reserve(value,recipe)) if self.target_resolver and recipe=='move_to' else [t for t in self._targets(recipe) if t.get('object_id')==value]
            except ToolContextUnavailable:raise
            except (OSError,ValueError,TimeoutError) as error:
                return {'status':'target_lookup_failed','reason':str(error),'executed':False}
            if not targets:return {'status':'object_not_currently_approachable','executed':False}
            value=targets[0]['target_id']
        return (yield from self._select('move_to',[recipe],value,pace))

    @staticmethod
    def _target_key(obj):
        return obj.get('object_id') or obj.get('target_id')

    def _recipes_for_object(self, recipes, obj):
        return [r for r in recipes if r not in EATING_RECIPES.values() or EATING_RECIPES.get(obj.get('kind'))==r]

    def _inspect(self, args, frame, recipes):
        raw=frame.get('observation') or {};surroundings=raw.get('surroundings') or {}
        center=args['center'] or 'creature';distance='distance_to_god_hand_engine_units' if center=='god_hand' else 'distance_engine_units'
        key=(args['kind'],center,args['action'],args['miracle'])
        # Freeze only the page membership/descriptions, never scene validity or
        # native execution eligibility. Every call still validates its request.
        if args['offset']==0 or key not in self.inspection_pages:
            objects={o['object_id']:deepcopy(o) for o in known_objects(surroundings)
                if target_matches(args['kind'],o) and type(o.get(distance)) in (int,float) and o[distance]<=NEARBY_TARGET_DISTANCE}
            targets=[]
            for recipe in recipes:
                for target in self._targets(recipe):
                    if not target_matches(args['kind'],target):continue
                    if center=='god_hand' and type(target.get(distance)) not in (int,float):continue
                    if target.get(distance,0)>NEARBY_TARGET_DISTANCE:continue
                    identity=self._target_key(target)
                    targets.append(target)
                    objects.setdefault(identity,deepcopy(target))
            items=sorted(objects.items(),key=lambda item:lookup_order(item[1],distance,explicit_kind=args['kind'] is not None))
            self.inspection_pages[key]={'items':items,'targets':targets,'towns':deepcopy(surroundings.get('towns',[]))}
        cached=self.inspection_pages[key];start=args['offset'];end=start+args['limit']
        page=cached['items'][start:end];identities={identity for identity,_ in page}
        errors=[]
        for identity,obj in page:
            if not recipes or not obj.get('object_id') or any(self._target_key(t)==identity for t in cached['targets']):continue
            for recipe in self._recipes_for_object(recipes,obj):
                try:cached['targets'].extend((yield from self._reserve(identity,recipe)))
                except ToolContextUnavailable:raise
                except (OSError,ValueError,TimeoutError) as error:errors.append(str(error))
        targets=[deepcopy(t) for t in cached['targets'] if self._target_key(t) in identities]
        result={'available':surroundings.get('god_hand_available' if center=='god_hand' else 'available') is True,
            'center':center,'objects':[public_object(obj) for _,obj in page],
            'matching_known_objects':len(cached['items']),'offset':start,
            'next_offset':end if end<len(cached['items']) else None,
            'visibility_verified':False,'executed':False}
        town_ids={o.get('town_id') for o in result['objects'] if o.get('town_id')}
        towns=[deepcopy(t) for t in cached['towns'] if t.get('town_id') in town_ids]
        if towns:result['towns']=towns
        context=frame['prompt']['context']
        hand=context.get('god_hand') or (context.get('surroundings') or {}).get('god_hand_nearby')
        if hand and target_matches(args['kind'],{'kind':'god_hand'}):result['god_hand']=deepcopy(hand)
        if targets:result['action_targets']=targets
        if errors:result['target_lookup']={'status':'lookup_failed','reasons':errors}
        return self._search_status(result,args,recipes,targets,nearby=True)

    def _find(self, args, frame, recipes):
        center=args['center'] or 'creature';distance='distance_to_god_hand_engine_units' if center=='god_hand' else 'distance_engine_units'
        def matches(obj):
            return (target_matches(args['kind'],obj) and (not args['object_id'] or obj.get('object_id')==args['object_id'])
                and (center=='creature' or type(obj.get(distance)) in (int,float))
                and (args['scope']!='distant' or obj.get(distance,0)>NEARBY_TARGET_DISTANCE))
        targets=[t for recipe in recipes for t in self._targets(recipe) if matches(t)]
        raw=frame.get('observation') or {}
        objects=[o for o in known_objects(raw.get('surroundings') or {}) if matches(o) and self._recipes_for_object(recipes,o)]
        objects.sort(key=lambda o:lookup_order(o,distance,explicit_kind=args['kind'] is not None))
        errors=[];lookups=0
        for obj in objects:
            if len(targets)>=args['limit'] or lookups>=args['limit']:break
            if any(t.get('object_id')==obj['object_id'] for t in targets):continue
            lookups+=1
            for recipe in self._recipes_for_object(recipes,obj):
                try:targets.extend(t for t in (yield from self._reserve(obj['object_id'],recipe)) if matches(t))
                except ToolContextUnavailable:raise
                except (OSError,ValueError,TimeoutError) as error:errors.append(str(error))
        targets.sort(key=lambda t:lookup_order(t,distance,explicit_kind=args['kind'] is not None))
        result={'action':args['action'],'scope':args['scope'] or 'known','center':center,
            'coverage':'bounded_sample_of_creature_known_objects_not_entire_island',
            'nearby_radius_engine_units':NEARBY_TARGET_DISTANCE,'maximum_search_distance_engine_units':KNOWN_TARGET_DISTANCE,
            'reachability_verified':False,'executed':False}
        if targets:
            result['action_targets']=targets[:args['limit']]
            result['more_offered_targets']=max(0,len(targets)-args['limit'])
        if errors:result['target_lookup']={'status':'lookup_failed','reasons':errors}
        return self._search_status(result,args,recipes,targets,nearby=False)

    @staticmethod
    def _search_status(result,args,recipes,targets,*,nearby):
        if args['action']:
            result['action']=args['action']
            if args['miracle']:result['miracle']=args['miracle']
            if not targets:
                result['status']=('action_unavailable' if not recipes else 'no_compatible_target_nearby' if nearby else 'no_matching_known_target')
                result['reason']=('This action is not available for this request.' if not recipes else
                    'No compatible target on this inspection page. Continue next_offset or use find_targets for farther known objects.' if nearby else
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
        if name not in ('move_to','use_held_object','do_action','inspect_surrounding','find_targets'):
            if name not in {d['name'] for d in self.definitions()}:raise ToolArgumentError('tool_not_offered')
            inner_arguments=arguments
            if name=='find_objects' and isinstance(arguments,dict) and 'type' in arguments:
                inner_arguments={**arguments,'kind':arguments['type']}
                inner_arguments.pop('type')
            result=self.inner.call(name,inner_arguments)
            if name=='inspect_self' and 'god_alignment' in result.get('creature',{}):
                result.setdefault('god_hand',{})['alignment']=result['creature'].pop('god_alignment')
        else:
            args=self._validate_arguments(name,arguments)
            if name=='move_to':result=yield from self._move(args)
            elif name=='use_held_object':
                recipes=[r for r in HELD_RECIPES[args['action']] if r in self.actions]
                result=yield from self._select('use_held_object:'+args['action'],recipes,args['target'])
            else:
                action=args['action']
                if name=='find_targets' and action is None:action='move_to';args['action']=action
                if (action=='cast_miracle') != (args['miracle'] is not None):
                    raise ToolArgumentError('specify_miracle_only_with_cast_miracle',parameter='miracle')
                recipes=self._recipes(action,args['miracle'])
                if name=='do_action':
                    result=yield from self._select(action,recipes,args['target'])
                    if args['miracle']:result['miracle']=args['miracle']
                else:
                    frame=self.supplier()
                    result=yield from (self._inspect(args,frame,recipes) if name=='inspect_surrounding' else self._find(args,frame,recipes))
        result=model_objects(result)
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
                    'For come here/come to me, select move_to with destination={"special":"god_hand"}. '
                    'Use move_to with destination={"object_id":"the observed ID"} or destination={"x":X,"z":Z} for explicit coordinates. '
                    'Select the requested supported action before answering. If it is unavailable, explain that honestly '
                    'instead of promising to move. The bridge can interrupt sleep for movement.')
        if not any(re.search(pattern,spoken,re.I) for pattern in (future,casting,acknowledgement)):
            return None
        return ('No action has been selected. Your draft promises to cast, but text cannot cast a miracle. '
                'Use inspect_surrounding and do_action with an offered spell and compatible target. '
                'If the requested target or spell is unavailable, state that honestly; you may explain '
                'and select a useful supported alternative. Do not repeat an unsupported casting promise.')
    def finalize_reply(self, result):return self.inner.finalize_reply(result)
