"""Bounded conversation intentions; native capabilities are acquired at execution.

An intention is not a saved engine packet. Questions and automatic turns may
create one. The dispatcher still performs fresh identity and game-thread
checks. Proven pre-execution failures may retry; lost receipts never do.
"""
from copy import deepcopy
import hashlib
import math
import random
import time
import uuid

from .action_probe import dummy_decision, SELF_ACTIONS, self_catalog_available, HELD_ACTIONS, held_actions_available, throw_targets_available, pickup_targets_available
from .action_probe import MIRACLE_ACTIONS, miracle_actions_available
from .world_actions import WORLD_ACTIONS, HELD_MIRACLE_ACTIONS, world_offers_available, world_recipes_available
from .action_probe import priority_state
from .positions import map_bounds, checked_position, parse_position_target
from .target_metadata import object_index, describe_target
from .surroundings import SUMMARY_RADIUS


def retryable_before_execution(result):
    return (result.get('status') in ('conditions_changed','expired','validation_unavailable','transport_unavailable')
        or (result.get('status')=='rejected' and result.get('stage')=='before_native_queue'))


class PendingActions:
    def __init__(self, native_factory, source, *, clock=time.monotonic, lifetime=30, wake_factory=None):
        if type(lifetime) not in (int, float) or not math.isfinite(lifetime) or not 0 < lifetime <= 60:
            raise ValueError('Invalid action wait limit')
        self.native_factory, self.source, self.clock = native_factory, source, clock
        self.lifetime = lifetime
        self.wake_factory = wake_factory
        self.revision = 0
        self.pending = self.last = None
        self.inflight = None
        self.active_offer = None

    def _state(self, obs, *, max_age_ms=1000):
        frame = self.source()
        raw, diag = frame['observation'], frame['diagnostics']
        stamp, age = raw['observed_at'], diag['source_age_ms']
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (stamp, age)):
            raise ValueError('Invalid source timestamp')
        # from_observer already subtracts source_age_ms from observed_at.
        # Elapsed time since that timestamp includes the native sample age;
        # adding it again rejects fresh observations during normal polling.
        elapsed = (self.clock()-stamp)*1000
        if age < 0 or elapsed < 0:
            raise ValueError('Invalid source timestamp')
        age = max(age, elapsed)
        if age > max_age_ms:
            raise ValueError('Stale action observation')
        pid, epoch = raw['session'].split(':')
        probe = diag['action_probe']
        contract = probe.get('conversation_actions', {})
        handles = [contract.get(k) for k in ('creature_handle', 'mental_handle')]
        if (not pid.isdecimal() or not epoch.isdecimal()
                or any(type(v) is not int or not 0 < v <= 0xffffffff for v in handles)
                or contract.get('version') != 2 or contract.get('max_offer_age_ms') != 30000
                or probe.get('supported') is not True or probe.get('transport_ready') is not True
                or probe.get('action_lifecycle', {}).get('repeated_actions_enabled') is not True):
            raise ValueError('Repeated conversation-action bridge unavailable')
        identity = (raw['session'], hashlib.sha256(
            f"{raw['session']}:{handles[0]}:{handles[1]}".encode()).hexdigest()[:24])
        if identity != (obs.session, obs.creature_id):
            raise ValueError('Creature identity differs')
        return priority_state({'pid': int(pid), 'session': int(epoch), 'source_age_ms': age,
                'action_probe': probe}), identity

    def factory(self, obs, *, wake_result=None):
        # Selecting an intention uses the same context window as live chat.
        # Execution still requires the stricter fresh reading in advance(),
        # followed by a fresh native offer and game-thread revalidation.
        state, identity = self._state(obs, max_age_ms=1500)
        probe = state['action_probe']
        candidates = ['look_confused']
        if probe.get('wake_protocol') == 3:
            candidates.append('wake_up')
        if self_catalog_available(probe):candidates.extend(SELF_ACTIONS)
        movement = probe.get('movement', {})
        distance = movement.get('distance_to_hand_engine_units')
        token = probe['conversation_actions'].get('hand_target_generation')
        if (movement.get('verified') is True and type(distance) in (int, float)
                and math.isfinite(distance) and distance>0 and type(token) is int and 0 < token <= 0xffffffff):
            candidates.append('approach_hand')
        tree = probe.get('object_movement', {})
        object_token = None
        targets = {}
        bounds=map_bounds(probe)
        if bounds:
            candidates.append('move_to_position')
            targets['move_to_position']={'map_size':bounds}
        pickups=pickup_targets_available(probe, planning=True)
        pickup_metadata={t['object_handle']:t for t in pickups if 'object_handle' in t}
        metadata=object_index(self.source()['observation'])
        def target_context(entry):
            entry={**pickup_metadata.get(entry.get('object_handle'),{}),**entry}
            return describe_target(entry,obs.session,probe['conversation_actions']['creature_handle'],metadata)
        world_bindings={}
        for action in world_recipes_available(probe):
            if action not in candidates:candidates.append(action)
            targets[action]={'objects':[]}
        for entry in world_offers_available(probe, planning=True):
            action=entry['action'];target_id='world-'+uuid.uuid4().hex
            world_bindings[target_id]=(action,entry['token'])
            if action not in candidates:candidates.append(action)
            targets.setdefault(action,{'objects':[]})['objects'].append({
                'target_id':target_id,'kind':entry['kind'],'distance_engine_units':entry['distance_engine_units'],
                **target_context(entry),
                **({'player_temple':True} if entry.get('player_temple') else {})})
        held=probe.get('held_actions',{})
        held_token=held.get('target_generation') if held_actions_available(probe) else None
        throw_bindings={}
        pickup_bindings={}
        miracle_bindings={}
        for action in miracle_actions_available(probe):
            candidates.append(action)
            if action=='cast_water_tree':
                trees=[]
                for t in pickups:
                    if t['kind']!='tree':continue
                    target_id='water-'+uuid.uuid4().hex;miracle_bindings[target_id]=t['token']
                    trees.append({'target_id':target_id,'kind':'tree','distance_engine_units':t['distance_engine_units'],
                        **target_context(t)})
                targets[action]={'objects':trees}
        if pickups or (probe.get('target_lookup_protocol')==12 and probe.get('pickup_action',{}).get('verified') is True):
            candidates.append('pick_up');objects=[]
            for t in pickups:
                target_id='pickup-'+uuid.uuid4().hex;pickup_bindings[target_id]=t['token']
                objects.append({'target_id':target_id,'kind':t['kind'],
                    'distance_engine_units':t['distance_engine_units'],
                    **target_context(t)})
            targets['pick_up']={'objects':objects}
        if held_token is not None:
            # Held commands refer to the same original object, never a later pickup.
            held_candidates=list(held_actions_available(probe))
            if probe.get('hand_preparation_protocol')==1:
                candidates.extend(held_candidates)
            else:
                candidates=held_candidates
            target={'target_id':'held-'+uuid.uuid4().hex,'kind':held['kind'],'location':'in_creature_hand'}
            target['can_eat'] = 'eat_held' in held_candidates
            targets.update({action:deepcopy(target) for action in held_candidates})
            throwing=throw_targets_available(probe)
            if throwing or (probe.get('target_lookup_protocol')==12 and probe.get('throw_action',{}).get('verified') is True):
                candidates.append('throw_held_at');destinations=[]
                for t in throwing:
                    target_id='throw-'+uuid.uuid4().hex
                    throw_bindings[target_id]=t['token']
                    destinations.append({'target_id':target_id,'kind':t['kind'],
                        'distance_engine_units':t['distance_engine_units'],
                        **target_context(t)})
                targets['throw_held_at']={'projectile':deepcopy(target),'destinations':destinations}
        if tree.get('kind') == 'tree' and dummy_decision(state, 'approach_object')['candidate']:
            object_token = tree['target_generation']
            candidates.append('approach_object')
            # Request-local reference, deliberately distinct from inspection IDs.
            targets['approach_object'] = {'target_id':'tree-'+uuid.uuid4().hex,
                'kind':'tree', 'distance_engine_units':tree['distance_engine_units'],
                'reachability_verified':False}
        wake_after = None
        if wake_result is not None:
            wake_after = wake_result.get('wake_after_request')
            if (wake_result.get('queued') is not True or type(wake_after) is not int or wake_after < 0):
                raise ValueError('Unverified wake receipt; no follow-up action')
        offer=_IntentionOffer(self, identity, tuple(candidates), token, object_token, targets, wake_after, held_token,throw_bindings,pickup_bindings,miracle_bindings,world_bindings)
        self.active_offer=offer
        return offer

    def cancel(self, reason):
        self.revision += 1  # Also invalidate a provider response still in progress.
        self.inflight = None
        if self.pending is not None:
            self.last = {**self.pending['result'], 'status': reason, 'queued': False}
            self.pending = None

    def snapshot(self):
        result = deepcopy(self.last)
        if self.pending is not None:
            result['remaining_seconds'] = max(0, self.pending['expires']-self.clock())
        return result

    def _retarget(self, item, entries, token_key):
        """Try another compatible object without changing a requested miracle."""
        target=item['result'].get('target',{})
        if token_key=='throw_token':target=target.get('destination',{})
        # An explicit home destination must never retry against another temple.
        if target.get('player_temple'):return False
        kind=target.get('kind')
        if not kind or kind=='self' or item['retargets']>=2:return False
        item['tried_targets'].add(item.get(token_key))
        frame=self.source();raw=frame['observation']
        probe=frame['diagnostics']['action_probe']
        metadata=object_index(raw)
        owner=probe['conversation_actions']['creature_handle']
        choices=[(e,describe_target(e,raw['session'],owner,metadata)) for e in entries if e['kind']==kind
                 and e['token'] not in item['tried_targets']
                 and (token_key!='pickup_token' or e.get('pickup_possible') is not False)
                 and (token_key!='world_token' or e['action']==item['candidate'])]
        choices=[(e,d) for e,d in choices if not target.get('miracle') or target['miracle']==d.get('miracle')]
        if not choices:return False
        chosen,description=min(choices,key=lambda pair:(pair[0]['distance_engine_units'],pair[0]['token']))
        item[token_key]=chosen['token'];item['retargets']+=1
        replacement={**description,'target_id':'retry-'+uuid.uuid4().hex}
        if token_key=='throw_token':item['result']['target']['destination']=replacement
        else:item['result']['target']=replacement
        item['result']['retarget_count']=item['retargets']
        return True

    def _recover_rejection(self, obs, allowed):
        if self.pending is not None or self.inflight is None:return
        item,baseline=self.inflight
        if (not allowed or obs is None or (obs.session,obs.creature_id)!=item['identity']
                or self.clock()>=item['expires']):
            self.inflight=None;return
        try:state,_=self._state(obs)
        except (KeyError,TypeError,ValueError):return
        p=state['action_probe'];stamp=p.get('request_at_uptime_ms')
        if type(stamp) is not int or stamp<=baseline:return
        if p.get('requested_action')!=item['candidate'] or p.get('result_current_creature') is not True:
            self.inflight=None;return
        phase=p.get('result_phase')
        if phase not in ('finished','rejected'):return
        self.inflight=None
        if item['candidate'] in HELD_MIRACLE_ACTIONS:return  # never replay a consumable cast
        # These exact native outcomes precede SetAgenda. Never replay a command
        # with a missing receipt, a finished plan or uncertain visible effects.
        failures={'world_target_changed_before_dispatch':'world_token',
                  'world_target_or_learning_changed_before_dispatch':'world_token',
                  'pickup_target_changed_before_dispatch':'pickup_token',
                  'throw_target_changed_before_dispatch':'throw_token',
                  'miracle_or_target_changed_before_dispatch':'miracle_token'}
        key=failures.get(p.get('outcome'))
        # These pickup predicates are also evaluated before SetAgenda. A smaller
        # alternative may still work; do not repeat the explicitly rejected item.
        if item['candidate']=='pick_up' and p.get('outcome') in (
                'too_large_for_current_size','too_heavy_to_lift','engine_pickup_not_allowed'):
            key='pickup_token'
        if phase!='rejected' or key is None:return
        item['tried_targets'].add(item.get(key));item[key]=None
        self.pending=item
        self.last={**item['result'],'waiting_reason':'retrying_rejected_target'}

    def _prepare_hand(self, item, obs, probe):
        if item['candidate'] in (*HELD_ACTIONS,*HELD_MIRACLE_ACTIONS,'throw_held_at','wake_up'):return False
        held=probe.get('held_actions',{});token=held.get('target_generation')
        if item['drop_after'] is not None:
            stamp=probe.get('request_at_uptime_ms')
            if type(stamp) is not int or stamp<=item['drop_after']:
                self.last={**item['result'],'waiting_reason':'waiting_for_put_down'};return True
            if probe.get('requested_action')!='put_down' or probe.get('result_current_creature') is not True:
                self.cancel('cancelled_put_down_replaced');return True
            if token==0:
                item['drop_after']=None
                item['result']['hand_preparation']='empty_hand_observed'
                return False
            if probe.get('result_phase') in ('finished','rejected'):
                self.cancel('cancelled_put_down_not_empty');return True
            self.last={**item['result'],'waiting_reason':'waiting_for_empty_hand'};return True
        if token in (None,0):return False
        if probe.get('hand_preparation_protocol')!=1:return False
        if item['drop_attempts']>=2:
            self.cancel('cancelled_hand_preparation_limit');return True
        baseline=probe.get('request_at_uptime_ms')
        if type(baseline) is not int or 'put_down' not in held_actions_available(probe):
            self.last={**item['result'],'waiting_reason':'put_down_unavailable'};return True
        try:
            dispatcher=self.native_factory(obs,expected_held_generation=token,
                **({'player_command':True} if item.get('player_command') else {}))
            if 'put_down' not in dispatcher.offer()['candidates']:
                self.last={**item['result'],'waiting_reason':'put_down_unavailable'};return True
            result=dispatcher.dispatch({'candidate':'put_down'})
        except (OSError,ValueError,TimeoutError):
            self.cancel('not_confirmed_no_retry');return True
        if result.get('queued') is True:
            item['drop_after']=baseline;item['drop_attempts']+=1
            item['result']['hand_preparation']='put_down_requested'
            self.last={**item['result'],'waiting_reason':'waiting_for_empty_hand'}
        else:
            item['dispatch_retries']+=1
            if item['dispatch_retries']>3 or not retryable_before_execution(result):
                self.cancel('cancelled_put_down_failed')
            else:self.last={**item['result'],'waiting_reason':'put_down_retry','dispatch_check':result}
        return True

    def _prepare_dance(self, item, obs, probe):
        """One cue, one random choice; only fall back before native execution."""
        choose_group=item.pop('prefer_social_dance',False)
        if not choose_group and not item.get('social_dance_fallback'):return
        entries=[e for e in world_offers_available(probe)
                 if e['action']=='dance_with_villagers' and e['kind']=='villager'
                 and e['distance_engine_units']<=SUMMARY_RADIUS]
        if choose_group and entries:
            entry=min(entries,key=lambda e:e['distance_engine_units'])
            item['candidate']='dance_with_villagers';item['world_token']=entry['token']
            item['social_dance_fallback']=True
            item['result']['candidate']='dance_with_villagers'
            item['result']['dance_variant']='with_villagers'
            item['result']['target']={
                'kind':entry['kind'],'distance_engine_units':entry['distance_engine_units'],
                **describe_target(entry,obs.session,probe['conversation_actions']['creature_handle'],
                                  object_index(self.source()['observation']))}
        elif item.get('social_dance_fallback') and not any(e['token']==item['world_token'] for e in entries):
            if self._retarget(item,entries,'world_token'):return
            item['candidate']='dance';item['world_token']=None
            item['social_dance_fallback']=False
            item['result'].update(candidate='dance',dance_variant='solo')
            item['result'].pop('target',None)

    def advance(self, obs, *, allowed, temporary_unavailable=False):
        self._recover_rejection(obs, allowed)
        item = self.pending
        if item is None:
            return
        if (not allowed and temporary_unavailable and obs is not None
                and (obs.session, obs.creature_id) == item['identity']):
            if self.clock() >= item['expires']:
                self.cancel('expired')
            else:
                self.last = {**item['result'], 'waiting_reason':'waiting_for_fresh_observation'}
            return
        if not allowed or obs is None or (obs.session, obs.creature_id) != item['identity']:
            self.cancel('cancelled_control_changed')
            return
        if self.clock() >= item['expires']:
            self.cancel('expired')
            return
        try:
            state, _ = self._state(obs)
        except (KeyError, TypeError, ValueError):
            return  # Missing native data cannot grant permission or refresh expiry.
        probe = state['action_probe']
        # Consume a completed initial chat wake before a put-down prerequisite
        # creates a newer native receipt. Never confuse the two commands.
        if item['wake_after'] is not None:
            request = probe.get('request_at_uptime_ms')
            if type(request) is not int or request <= item['wake_after']:
                self.last = {**item['result'], 'waiting_reason':'waiting_for_wake_confirmation'}
                return
            if probe.get('requested_action') != 'wake_up':
                self.cancel('cancelled_wake_replaced')
                return
            if probe.get('result_phase') not in ('finished','rejected'):
                self.last = {**item['result'], 'waiting_reason':'waiting_for_wake_confirmation'}
                return
            if not (probe.get('result_phase') == 'finished' and probe.get('completion') == 'awake_observed'
                    and probe.get('outcome') == 'native_wake_observed'
                    and probe.get('result_current_creature') is True and probe.get('script_owned') is False):
                self.cancel('cancelled_wake_unconfirmed')
                return
            item['wake_after'] = None
        if self._prepare_hand(item, obs, probe):
            return
        self._prepare_dance(item, obs, probe)
        if item['candidate'] in WORLD_ACTIONS and not any(e['action']==item['candidate'] and e['token']==item['world_token']
                and (not item['result'].get('target',{}).get('player_temple') or e.get('player_temple') is True)
                for e in world_offers_available(probe)):
            if item['candidate'] in HELD_MIRACLE_ACTIONS:
                self.cancel('cancelled_world_target_or_learning_changed');return
            if not self._retarget(item, world_offers_available(probe), 'world_token'):
                self.cancel('cancelled_world_target_or_learning_changed');return
        if item['candidate'] in MIRACLE_ACTIONS:
            if item['candidate'] not in miracle_actions_available(probe):
                self.cancel('cancelled_miracle_unavailable');return
            if item['candidate']=='cast_water_tree' and item['miracle_token'] not in {t['token'] for t in pickup_targets_available(probe) if t['kind']=='tree'}:
                if not self._retarget(item, pickup_targets_available(probe), 'miracle_token'):
                    self.cancel('cancelled_miracle_target_changed');return
        if (item['candidate']=='pick_up' and item['pickup_token'] not in
                {t['token'] for t in pickup_targets_available(probe)}):
            if not self._retarget(item, pickup_targets_available(probe), 'pickup_token'):
                self.cancel('cancelled_pickup_target_changed');return
        if (item['candidate'] in (*HELD_ACTIONS,'throw_held_at') and
                (probe.get('held_actions',{}).get('target_generation') != item['held_token']
                 or not held_actions_available(probe)
                 or (item['candidate'] in HELD_ACTIONS and item['candidate'] not in held_actions_available(probe)))):
            self.cancel('cancelled_held_object_changed')
            return
        if (item['candidate']=='throw_held_at' and item['throw_token'] not in
                {t['token'] for t in throw_targets_available(probe)}):
            if not self._retarget(item, throw_targets_available(probe), 'throw_token'):
                self.cancel('cancelled_throw_target_changed');return
        if (item['candidate'] == 'approach_object' and
                (probe.get('object_movement', {}).get('target_generation') != item['object_token']
                 or probe.get('object_movement', {}).get('kind') != 'tree')):
            self.cancel('cancelled_target_changed')
            return
        if item['candidate'] == 'approach_hand':
            # This intention follows the selected god's live hand, not its
            # coordinates at inference time. Bind a fresh capability at dispatch.
            token=probe['conversation_actions'].get('hand_target_generation')
            if type(token) is not int or not 0 < token <= 0xffffffff:
                self.last={**item['result'],'waiting_reason':'hand_position_unavailable'}
                return
            item['hand_token']=token
        if item['candidate']=='wake_up':
            activity=probe.get('activity',{})
            if (activity.get('verified') is True and activity.get('sleep_in_sequence') is False
                    and activity.get('controller_mode') in (0,1) and activity.get('animation')==0
                    and activity.get('current_subaction')!=132):
                self.pending=None
                self.last={**item['result'],'status':'already_awake','queued':False}
                return
        if (item['candidate'] not in ('pick_up','wake_up','approach_hand','move_to_position',*WORLD_ACTIONS,*MIRACLE_ACTIONS) and self.wake_factory is not None
                and dummy_decision(state,'wake_up')['candidate']):
            if item['wake_attempts'] >= 2:
                self.cancel('cancelled_asleep_again')
                return
            baseline = probe.get('request_at_uptime_ms')
            if type(baseline) is not int or baseline < 0:
                self.last = {**item['result'], 'waiting_reason':'wake_state_unverified'}
                return
            try:
                dispatcher = self.wake_factory(obs)
                if item.get('player_command') and hasattr(dispatcher,'mark_player_command'):
                    dispatcher.mark_player_command()
                if dispatcher.offer()['candidates'] != ['wake_up']:
                    self.last = {**item['result'],'waiting_reason':'wake_not_offered'}
                    return
            except (OSError, ValueError, TimeoutError) as error:
                self.last = {**item['result'],'waiting_reason':'wake_capture_unavailable',
                             'error_type':type(error).__name__}
                return
            try:
                result = dispatcher.dispatch({'candidate':'wake_up'})
            except (OSError, ValueError, TimeoutError):
                self.cancel('wake_not_confirmed_no_retry')
                return
            if result.get('status') in ('validation_unavailable','transport_unavailable','conditions_changed','expired'):
                self.last = {**item['result'],'waiting_reason':result['status'],'wake_check':result}
                return
            item['wake_attempts'] += 1
            if result.get('queued') is not True:
                self.cancel('cancelled_wake_not_queued')
                return
            item['wake_after'] = baseline
            self.last = {**item['result'], 'waiting_reason':'waiting_for_wake_confirmation'}
            return
        if dummy_decision(state, item['candidate'])['candidate'] is None:
            self.last = {**item['result'], 'waiting_reason': probe.get('reason', 'native_unavailable')}
            return
        # Consume before any transport attempt. A timeout cannot resurrect it.
        self.pending = None
        result = {'status': 'native_offer_unavailable', 'queued': False}
        try:
            kwargs = ({'destination':item['destination']} if item['candidate']=='move_to_position' else {'wake_action':True} if item['candidate']=='wake_up' else
                {'world_action':item['candidate'],'expected_world_token':item['world_token']} if item['candidate'] in WORLD_ACTIONS else
                {'miracle_action':item['candidate'],'expected_miracle_target':item['miracle_token']} if item['candidate'] in MIRACLE_ACTIONS else
                {'expected_pickup_generation':item['pickup_token']} if item['candidate']=='pick_up' else
                {'expected_held_generation':item['held_token'],'expected_throw_generation':item['throw_token']}
                if item['candidate']=='throw_held_at' else
                {'expected_held_generation':item['held_token']} if item['candidate'] in HELD_ACTIONS else
                {'expected_object_generation':item['object_token']}
                if item['candidate'] == 'approach_object' else
                {'expected_hand_generation':item['hand_token'] if item['candidate'] == 'approach_hand' else None})
            if item.get('pace') is not None:kwargs['pace']=item['pace']
            if item.get('player_command'):kwargs['player_command']=True
            dispatcher = self.native_factory(obs, **kwargs)
            offer = dispatcher.offer()
        except (OSError, ValueError, TimeoutError):
            # Capture only reads. A brief availability race may keep waiting;
            # neither the intention deadline nor any native packet is renewed.
            self.pending = item
            self.last = {**item['result'], 'waiting_reason': 'native_offer_unavailable'}
            return
        else:
            if item['candidate'] not in offer['candidates']:
                self.pending = item
                self.last = {**item['result'], 'waiting_reason': 'native_offer_unavailable'}
                return
            if item['candidate'] in offer['candidates']:
                try:
                    current, _ = self._state(obs)
                except (OSError, ValueError, TimeoutError, KeyError, TypeError) as error:
                    self.pending = item
                    self.last = {**item['result'],'waiting_reason':'source_recheck_unavailable',
                                 'error_type':type(error).__name__}
                    return
                try:
                    # A fresh offer may bind a different hand target; do not
                    # silently reinterpret the player's delayed request.
                    if (item['candidate'] == 'approach_object' and
                            (current['action_probe'].get('object_movement', {}).get('target_generation') != item['object_token']
                             or current['action_probe'].get('object_movement', {}).get('kind') != 'tree')):
                        result = {'status': 'cancelled_target_changed', 'queued': False}
                    elif (item['candidate'] == 'approach_hand' and
                            current['action_probe']['conversation_actions'].get('hand_target_generation') != item['hand_token']):
                        self.pending=item
                        self.last={**item['result'],'waiting_reason':'refreshing_hand_target'}
                        return
                    elif self.clock() >= item['expires']:
                        result = {'status': 'expired', 'queued': False}
                    else:
                        result = dispatcher.dispatch({'candidate': item['candidate']})
                        if retryable_before_execution(result):
                            # No execution started: capture a new offer while
                            # preserving this intention's deadline and retry cap.
                            item['dispatch_retries'] += 1
                            if item['dispatch_retries'] > 3:
                                self.last = {**item['result'],'status':'cancelled_retry_limit','queued':False}
                                return
                            self.pending = item
                            self.last = {**item['result'], 'waiting_reason': result['status'], 'dispatch_check':result,
                                         'retry_count':item['dispatch_retries']}
                            return
                except (OSError, ValueError, TimeoutError, KeyError, TypeError):
                    result = {'status': 'not_confirmed_no_retry', 'queued': False}
        self.last = {**item['result'], **result}
        if result.get('queued') is True:
            baseline=probe.get('request_at_uptime_ms')
            if type(baseline) is int:
                self.inflight=(item,baseline)


class _IntentionOffer:
    def __init__(self, owner, identity, candidates, token, object_token=None, targets=None, wake_after=None, held_token=None,throw_bindings=None,pickup_bindings=None,miracle_bindings=None,world_bindings=None):
        self.owner, self.identity, self.candidates, self.token = owner, identity, candidates, token
        self.object_token, self.targets = object_token, targets or {}
        self.wake_after = wake_after
        self.held_token = held_token
        self.throw_bindings=throw_bindings or {}
        self.pickup_bindings=pickup_bindings or {}
        self.miracle_bindings=miracle_bindings or {}
        self.world_bindings=world_bindings or {}
        self.revision = owner.revision
        self.expires = owner.clock()+owner.lifetime
        self.offered = self.used = False
        self.lookup_requested = False
        self.lookup = None
        self.lookup_cache = None
        self.player_command = False

    def mark_player_command(self):
        if self.offered or self.used:
            raise ValueError('Command origin must be set before offering actions')
        self.player_command=True

    def dispatch_expression(self, decision):
        result=self.dispatch(decision)
        if decision=={'candidate':'dance'} and result.get('status')=='waiting_for_native':
            # Roll once per new speech cue, never on a polling tick or retry.
            self.owner.pending['prefer_social_dance']=random.randrange(3)==0
        return result

    def offer(self):
        if self.offered or self.used:
            raise ValueError('Intention already offered')
        self.offered = True
        return {'candidates': list(self.candidates), 'action_targets':deepcopy(self.targets)}

    def resolve_object(self, object_id, action, lookup=None, *, refresh=False):
        operation=self._resolve_object(object_id,action,lookup,refresh=refresh)
        try:
            function,args,kwargs=next(operation)
            operation.send(function(*args,**kwargs))
        except StopIteration as result:
            return result.value
        finally:operation.close()

    async def resolve_object_async(self, object_id, action):
        import asyncio
        operation=self._resolve_object(object_id,action)
        try:
            function,args,kwargs=next(operation)
            result=await asyncio.to_thread(function,*args,**kwargs)
            operation.send(result)
        except StopIteration as result:
            return result.value
        finally:operation.close()

    def _resolve_object(self, object_id, action, lookup=None, *, refresh=False):
        """Extend this exact request with a freshly resolved native capability."""
        from .object_details import known_objects, public_object
        from .action_probe import lookup_target
        if (self.used or not self.offered or self.owner.active_offer is not self
                or self.owner.revision!=self.revision or self.owner.clock()>=self.expires):
            raise ValueError('Target lookup request expired')
        frame=self.owner.source();raw=frame['observation'];probe=frame['diagnostics']['action_probe']
        if (raw['session'],raw.get('creature_id'))!=self.identity:raise ValueError('Target lookup identity changed')
        obj=next((o for o in known_objects(raw.get('surroundings') or {}) if o['object_id']==object_id),None)
        if obj is None:raise ValueError('Object is no longer known')
        pid,epoch=map(int,raw['session'].split(':'))
        creature=probe['conversation_actions']['creature_handle']
        self.lookup_requested=True
        if lookup is not None:self.lookup=lookup
        # One focus lookup returns all action recipes for this object. Reuse it
        # for other recipes in the same request; dispatch always refreshes it.
        # Keep only the most recent object, never a growing world cache.
        if not refresh and self.lookup_cache and self.lookup_cache[0]==object_id:
            new_probe=self.lookup_cache[1]
        else:
            # Only the blocking pipe lookup leaves the event loop. Preparation,
            # identity checks and capability mutations stay on its owner thread.
            new_probe=yield (self.lookup or lookup_target,(pid,obj.get('lookup_handle')),
                {'expected_session':epoch,'expected_creature':creature})
            self.lookup_cache=(object_id,new_probe)
        if self.owner.active_offer is not self or self.owner.revision!=self.revision or self.used:
            raise ValueError('Target lookup request superseded')
        if self.owner.clock()>=self.expires:raise ValueError('Target lookup request expired')
        current=(self.owner.source() or {}).get('observation',{})
        if (current['session'],current.get('creature_id'))!=self.identity:
            raise ValueError('Target lookup identity changed')
        if action in WORLD_ACTIONS:
            entries=[e for e in world_offers_available(new_probe,planning=True) if e['action']==action]
            bindings=self.world_bindings;prefix='world';key='objects'
        elif action=='pick_up':
            entries=pickup_targets_available(new_probe,planning=True);bindings=self.pickup_bindings;prefix='pickup';key='objects'
        elif action=='throw_held_at':
            entries=throw_targets_available(new_probe);bindings=self.throw_bindings;prefix='throw';key='destinations'
        else:return []
        result=[]
        for e in entries:
            if e.get('object_handle')!=obj.get('lookup_handle'):continue
            target_id=prefix+'-'+uuid.uuid4().hex
            bindings[target_id]=(action,e['token']) if action in WORLD_ACTIONS else e['token']
            item={**public_object(obj),'target_id':target_id,'kind':e['kind'],
                  'distance_engine_units':e['distance_engine_units']}
            for name in ('pickup_possible','pickup_reason','player_temple','on_fire','distance_to_god_hand_engine_units'):
                if name in e:item[name]=e[name]
            self.targets.setdefault(action,{key:[]})[key].append(item)
            result.append(item)
        if result and action not in self.candidates:self.candidates+= (action,)
        return deepcopy(result)

    def dispatch(self, decision):
        if self.used or not self.offered:
            raise ValueError('No unused intention')
        # Several lookups may share one reserved native target slot. Reacquire
        # the selected identity last, so inspecting B cannot evict selected A.
        resolution_note=None
        if self.lookup_requested and type(decision) is dict:
            candidate=decision.get('candidate');target=decision.get('target')
            choices=self.targets.get(candidate,{})
            selected=next((t for t in choices.get('objects',choices.get('destinations',[]))
                if t.get('target_id')==target and t.get('object_id')),None)
            if selected:
                try:resolved=self.resolve_object(selected['object_id'],candidate,refresh=True)
                except (OSError,ValueError,TimeoutError) as error:
                    resolved=[];resolution_note=str(error)
                if resolved:decision={**decision,'target':resolved[0]['target_id']}
                else:
                    # No command was sent. Keep the intention so advance() can
                    # select another current compatible target using its normal
                    # bounded recovery path, instead of cancelling prematurely.
                    resolution_note=resolution_note or 'Selected object no longer supports this action'
        self.used = True
        if type(decision) is not dict or set(decision)-{'pace'} not in ({'candidate'},{'candidate','target'}):
            raise ValueError('Invalid action decision')
        candidate = decision['candidate']
        from .movement_pace import checked_pace
        pace=checked_pace(candidate,decision.get('pace'))
        throw_token=None;destination=None;pickup_token=None;pickup_object=None
        miracle_token=None
        world_token=None
        point=None
        if candidate=='move_to_position':
            point=checked_position(parse_position_target(decision.get('target')),
                self.targets.get(candidate,{}).get('map_size'))
        elif candidate in WORLD_ACTIONS:
            target_id=decision.get('target')
            # Speech cues may select only their existing, request-bound self target.
            from .expressions import EXPRESSION_GESTURES
            if target_id is None and candidate in EXPRESSION_GESTURES.values():
                choices=[t['target_id'] for t in self.targets.get(candidate,{}).get('objects',[]) if t['kind']=='self']
                if len(choices)==1:target_id=choices[0]
            if not isinstance(target_id,str) or self.world_bindings.get(target_id,(None,None))[0]!=candidate:
                raise ValueError('World action/target pair was not offered')
            world_token=self.world_bindings[target_id][1]
            pickup_object=next(t for t in self.targets[candidate]['objects'] if t['target_id']==target_id)
        elif candidate=='cast_water_tree':
            target_id=decision.get('target')
            if not isinstance(target_id,str) or target_id not in self.miracle_bindings:raise ValueError('Water target was not offered')
            miracle_token=self.miracle_bindings[target_id]
            pickup_object=next(t for t in self.targets[candidate]['objects'] if t['target_id']==target_id)
        elif candidate=='throw_held_at':
            target_id=decision.get('target')
            if not isinstance(target_id,str) or target_id not in self.throw_bindings:raise ValueError('Throw destination was not offered')
            throw_token=self.throw_bindings[target_id]
            destination=next(t for t in self.targets[candidate]['destinations'] if t['target_id']==target_id)
        elif candidate=='pick_up':
            target_id=decision.get('target')
            if not isinstance(target_id,str) or target_id not in self.pickup_bindings:raise ValueError('Pickup object was not offered')
            pickup_token=self.pickup_bindings[target_id]
            pickup_object=next(t for t in self.targets[candidate]['objects'] if t['target_id']==target_id)
        elif 'target' in decision:raise ValueError('Unexpected decision target')
        if candidate is None:
            return {'status': 'no_action', 'queued': False}
        if candidate not in self.candidates:
            raise ValueError('Action was not offered')
        owner = self.owner
        if owner.clock() >= self.expires:
            return {'status': 'expired', 'queued': False}
        if owner.revision != self.revision:
            return {'status': 'superseded', 'queued': False}
        owner.cancel('superseded')
        result = {'status': 'waiting_for_native', 'queued': False,
                  'intent_id': 'intent-'+uuid.uuid4().hex, 'candidate': candidate}
        if pace is not None:result['pace']=pace
        if candidate in self.targets:result['target'] = deepcopy(self.targets[candidate])
        if destination:result['target']={'destination':deepcopy(destination),'projectile':deepcopy(self.targets[candidate]['projectile'])}
        if pickup_object:result['target']=deepcopy(pickup_object)
        if point:result['target']={'position':point}
        if resolution_note:result['target_resolution_note']=resolution_note
        owner.pending = {'identity': self.identity, 'candidate': candidate,
                         'player_command':self.player_command,
                         'pace':pace,
                         'world_token':world_token, 'destination':point,
                         'miracle_token':miracle_token,
                         'expires': self.expires, 'hand_token': self.token,
                         'object_token':self.object_token, 'held_token':self.held_token, 'throw_token':throw_token,'pickup_token':pickup_token,'wake_after':self.wake_after,
                         'wake_attempts':int(self.wake_after is not None), 'result': result,
                         'retargets':0,'dispatch_retries':0,'drop_attempts':0,'drop_after':None,
                         'tried_targets':set()}
        owner.last = result
        return deepcopy(result)
