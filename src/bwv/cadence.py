"""Cheap adaptive scheduling; no model calls, engine writes or guessed sensors."""
from dataclasses import asdict, dataclass, fields
import json
import math
import random
from .sleep import sleep_state
from .llm_context import scheduling_context


@dataclass(frozen=True)
class CadenceSettings:
    active_seconds: float = 30
    settlement_seconds: float = 60
    quiet_seconds: float = 120
    distant_seconds: float = 240
    jitter_percent: float = 20
    quiet_settle_seconds: float = 5

    def __post_init__(self):
        values = [self.active_seconds,self.settlement_seconds,self.quiet_seconds,self.distant_seconds]
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 5<=v<=3600 for v in values):
            raise ValueError('Invalid cadence intervals')
        if values != sorted(values):
            raise ValueError('Cadence intervals must increase from active to distant')
        for value,limit in ((self.jitter_percent,50),(self.quiet_settle_seconds,60)):
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=limit:
                raise ValueError('Invalid cadence variation')

    @classmethod
    def parse(cls, data, previous=None):
        if type(data) is not dict or not data.keys()<={f.name for f in fields(cls)}:
            raise ValueError('Unknown cadence setting')
        return cls(**{**asdict(previous or cls()),**data})


def activity_tier(frame, context):
    """Absence of a sensor is not evidence that the player is far away or idle.

    A future calibrated adapter may supply attention: {verified, hand_far,
    player_idle}. hand_far must account for both world distance and camera view.
    There is deliberately no raw-distance/zoom heuristic here without live QA.
    """
    context=scheduling_context(context)
    raw=(frame or {}).get('observation') or {}
    attention=raw.get('attention') or {}
    if attention.get('verified') is True:
        if attention.get('hand_far') is True:return 'distant','verified_hand_far'
        if attention.get('player_idle') is True:return 'distant','verified_player_idle'
    nearby=context.get('surroundings') or {}
    hand_distance=(context.get('god_hand') or {}).get('distance_engine_units')
    hand_near=(type(hand_distance) in (int,float) and math.isfinite(hand_distance)
               and 0<=hand_distance<=40)
    if ((raw.get('training') or {}).get('attention_active') is True
            or context.get('feedback') or context.get('learning_events')
            or hand_near or nearby.get('god_hand_nearby')
            or nearby.get('other_creatures_nearby',0)>0):
        return 'active','interaction_or_creature_activity'
    if any(nearby.get(key,0)>0 for key in (
            'people_nearby','important_people_nearby','homes_nearby','houses_nearby')):
        return 'settlement','nearby_people_or_homes'
    if (raw.get('surroundings') or {}).get('available') is True:
        return 'quiet','quiet_known_surroundings'
    return 'settlement','surroundings_or_attention_unknown'


def routine_signature(context, system):
    context=scheduling_context(context)
    # Small body/drive drift and exact native-plan changes must not cause calls.
    meaningful={k:v for k,v in context.items() if k not in (
        'instinct','instinct_motivations_engine_units','body_engine_units',
        'feedback','learning_events','health_changes','received_effects','action_results')}
    if 'hunger_motivation_engine_units' in meaningful:
        meaningful['hunger_motivation_engine_units']=round(meaningful['hunger_motivation_engine_units']*2)/2
    body=context.get('body_engine_units') or {}
    if body:meaningful['body_engine_units']={k:round(v,1) for k,v in body.items()}
    return json.dumps([system,meaningful],sort_keys=True,allow_nan=False)


def events(context):
    context=scheduling_context(context)
    return {(key,event['event_id']) for key in
            ('feedback','learning_events','health_changes','received_effects','action_results')
            for event in context.get(key,[]) if isinstance(event,dict) and event.get('event_id')}


class AdaptiveUpdates:
    def __init__(self, settings=None, event_cooldown=10, *, uniform=None):
        self.settings=settings or CadenceSettings()
        if not 0<event_cooldown<=self.settings.active_seconds:
            raise ValueError('Invalid event cooldown')
        self.event_cooldown=event_cooldown
        self.uniform=uniform or random.uniform
        self._count=0;self.latest=None;self.identity=None
        self.reset()

    def reset(self):
        self.deadline=None;self.last_time=None;self.signature=None;self.last_system=None
        self.seen=set();self.signals=None;self.tier=None;self.reason=None
        self.candidate=None;self.candidate_since=None;self.anchor=None;self.interval=None
        self.sleeping=False
        self.health_sample=None;self.damage_until=None

    def _damage_active(self, context, observed_at, now):
        state=scheduling_context(context)
        health=(state.get('body_engine_units') or {}).get('health')
        valid=type(health) in (int,float) and math.isfinite(health) and health>=0
        previous=self.health_sample
        if valid and previous is not None:
            when,value=previous
            if 0<observed_at-when<=1.5 and health<value-1e-5:
                # Bridge consecutive hits without retaining old history as danger.
                self.damage_until=now+5
        if previous is None or observed_at>previous[0]:
            self.health_sample=(observed_at,health) if valid else None
        return (state.get('on_fire') is True or
                (self.damage_until is not None and now<self.damage_until))

    def _schedule(self, now):
        base=getattr(self.settings,(self.tier or 'settlement')+'_seconds')
        spread=self.settings.jitter_percent/100
        self.interval=base*self.uniform(1-spread,1+spread)
        self.anchor=now;self.deadline=now+self.interval

    def _select(self, tier, reason, now):
        interval=getattr(self.settings,tier+'_seconds')
        if self.tier is None or interval<getattr(self.settings,self.tier+'_seconds'):
            self.tier,self.reason=tier,reason;self.candidate=None
            if self.deadline is not None:
                anchor=self.anchor;self._schedule(anchor)
        elif tier!=self.tier:
            if self.candidate!=tier:self.candidate,self.candidate_since=tier,now
            if now-self.candidate_since>=self.settings.quiet_settle_seconds:
                self.tier,self.reason=tier,reason;self.candidate=None
                if self.deadline is not None:self._schedule(self.anchor)
        else:self.reason=reason;self.candidate=None

    def conversation_activity(self, now, context=None, system=None):
        """Reset from actual dispatch/completion, not merely typing a question."""
        self.last_time=now;self._schedule(now)
        if context is not None and context:
            self.signature=routine_signature(context,system)
            self.last_system=system;self.seen|=events(context)
            signals=scheduling_context(context)
            self.signals=(signals.get('leash'),signals.get('on_fire') is True)

    def update(self, observation, prompt, gate, now, *, source=None, busy=False):
        identity=(observation.session,observation.creature_id)
        if identity!=self.identity:self.reset();self.identity=identity
        context=json.loads(prompt.context_json)
        blocked=(not gate.allowed or not observation.connected or not observation.creature_id
                 or not -.1<=now-observation.observed_at<=1.5)
        if blocked or not context:
            self.reset()
            return self._status('suspended' if blocked else 'skipped_empty',now)
        sleeping = sleep_state(source, now)
        if sleeping is True or (self.sleeping and sleeping is None):
            self.reset()
            self.sleeping=True
            return self._status('suspended_sleep',now)
        if self.sleeping:
            # Reconcile from the current world after sleep, including changes
            # since a direct question; do not replay a backlog of sleeping ticks.
            self.reset()
        tier,reason=activity_tier(source,context)
        danger=self._damage_active(context,observation.observed_at,now)
        if danger and getattr(self.settings,tier+'_seconds')>self.settings.active_seconds:
            tier,reason='active','burning_or_receiving_damage'
        self._select(tier,reason,now)
        if busy:
            return self._status('conversation_in_progress',now)
        signature=routine_signature(context,prompt.system)
        event_ids=events(context)
        state=scheduling_context(context)
        signals=(state.get('leash'),state.get('on_fire') is True)
        trigger=None
        if self.last_time is None:trigger='initial_or_resumed'
        elif any(key == 'action_results' for key, _ in event_ids-self.seen):
            trigger='action_result'
        elif now-self.last_time>=self.event_cooldown and (
                event_ids-self.seen or signals!=self.signals or prompt.system!=self.last_system):
            trigger='important_event'
        elif self.deadline is not None and now>=self.deadline:
            if signature!=self.signature:trigger='adaptive_update'
            else:
                self._schedule(now)
                return self._status('skipped_unchanged',now)
        if trigger:
            self._count+=1
            self.latest={'number':self._count,'prepared_at_monotonic':now,'trigger':trigger,
                'context':context,'system':prompt.system,'llm_input':{'snapshot':context},
                'delivery':'preview_only_not_sent','control_gate_allowed_at_preparation':gate.allowed,
                'block_reasons':list(gate.reasons),'provider_calls':0}
            self.conversation_activity(now,context,prompt.system)
            # Bound deduplication while retaining recently observed event identities.
            if len(self.seen)>512:self.seen=set(event_ids)
        return self._status('preview_prepared' if trigger else 'waiting',now)

    def _status(self, state, now):
        return {'state':state,'tier':self.tier,'reason':self.reason,
            'interval_seconds':self.interval,'jitter_percent':self.settings.jitter_percent,
            'next_update_in_seconds':max(0,self.deadline-now) if self.deadline is not None else None,
            'event_cooldown_seconds':self.event_cooldown,'last_update':self.latest}
