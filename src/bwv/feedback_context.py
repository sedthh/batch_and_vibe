"""Retain observed training feedback until a conversation accepts it.

Recent native plans supply temporal context, never an inferred reward target.
No engine writes, model calls, or persistence of world-state events occur here.
"""
from collections import OrderedDict, deque
from copy import deepcopy
import math

from .llm_context import plan_label


class FeedbackContext:
    def __init__(self):
        self.identity = None
        self.pending = OrderedDict()
        self.seen = OrderedDict()
        self.plans = deque(maxlen=64)
        self.current = None
        self.overflow = {'praise': 0, 'punishment': 0}
        self.pending_text = OrderedDict()
        self.seen_text = OrderedDict()
        self.text_overflow = 0
        self.pending_activity = OrderedDict()
        self.last_activity = None
        self.activity_serial = 0

    def ingest(self, frame, now):
        raw = frame['observation']
        identity = (raw.get('session'), raw.get('creature_id'))
        if not all(identity):
            return
        if identity != self.identity:
            self.__init__()
            self.identity = identity
        self.current = None
        self.plans = deque(((stamp, plan) for stamp, plan in self.plans
                            if 0 <= now-stamp <= 15), maxlen=64)
        plan = (frame['prompt']['context'].get('instinct') or {}).get('native_plan')
        target = (frame['prompt']['context'].get('instinct') or {}).get('intended_target') or {}
        from .world_actions import WORLD_KINDS
        object_id = target.get('observed_object_id')
        signature = (plan, target.get('kind'), object_id)
        if (isinstance(plan, str) and 0 < len(plan) <= 32 and target.get('kind') in WORLD_KINDS
                and isinstance(object_id, str) and len(object_id) == 24):
            if signature != self.last_activity:
                self.activity_serial += 1
                key = f'{identity[0]}:native-intention:{self.activity_serial}'
                self.pending_activity[key] = {'event_id':key, 'native_plan':plan_label(plan),
                    'intended_target':{'kind':target['kind'],'observed_object_id':object_id},
                    'status':'observed_intention_not_confirmed'}
                if len(self.pending_activity) > 16:
                    self.pending_activity.popitem(last=False)
            self.last_activity = signature
        else:
            self.last_activity = None
        if isinstance(plan, str) and 0 < len(plan) <= 32:
            label = plan_label(plan)
            self.plans.append((now, label))
        training = raw.get('training') or {}
        for event in training.get('feedback', ()):
            key, kind, stamp = (event.get(k) for k in ('event_id', 'kind', 'observed_at'))
            if (not isinstance(key, str) or not 0 < len(key) <= 96
                    or kind not in self.overflow or type(stamp) not in (int, float)
                    or not math.isfinite(stamp) or not -.1 <= now-stamp <= 30):
                continue
            if training.get('attention_active') is True and -.1 <= now-stamp <= .75:
                self.current = (kind, stamp)
            if key in self.seen:
                continue
            self.seen[key] = None
            if len(self.seen) > 512:
                self.seen.popitem(last=False)
            entry = {'event_id': key, 'kind': kind}
            amount = event.get('magnitude')
            if (type(amount) in (int, float) and math.isfinite(amount) and 0 < abs(amount) <= 1
                    and (amount > 0) == (kind == 'praise')):
                entry['signed_feedback_engine_units'] = round(amount, 4)
            behavior = event.get('behavior_id')
            if isinstance(behavior, str) and 0 < len(behavior) <= 96:
                entry['confirmed_training_behavior'] = behavior
            else:
                entry['training_reason'] = 'unknown'
            # Only associate plans sampled before/at feedback, never later plans
            # from a retained event first encountered on connection.
            plans = list(dict.fromkeys(p for t, p in self.plans if 0 <= stamp-t <= 15))[-6:]
            if plans:
                entry['recent_native_plans_not_confirmed_reasons'] = plans
            self.pending[key] = entry
            if len(self.pending) > 64:
                _, older = self.pending.popitem(last=False)
                self.overflow[older['kind']] += 1
        for message in (raw.get('experience') or {}).get('native_training_feedback', ()):
            key, kind, stamp, text = (message.get(k) for k in ('event_id', 'kind', 'observed_at', 'text'))
            if (message.get('source') != 'selected_creature_native_help'
                    or not isinstance(key, str) or not 0 < len(key) <= 96
                    or kind not in self.overflow or type(stamp) not in (int, float)
                    or not math.isfinite(stamp) or not -.1 <= now-stamp <= 1.5
                    or not isinstance(text, str) or not 0 < len(text.strip()) <= 511
                    or any(ord(c) < 32 for c in text) or key in self.seen_text):
                continue
            self.seen_text[key] = None
            if len(self.seen_text) > 512:
                self.seen_text.popitem(last=False)
            self.pending_text[key] = {'event_id': key, 'kind': kind, 'explanation': text}
            if len(self.pending_text) > 64:
                self.pending_text.popitem(last=False)
                self.text_overflow += 1

    def project(self, snapshot, now=None):
        result = deepcopy(snapshot)
        # The raw sensor repeats a short window; delivery belongs to this buffer.
        events = result.get('events', {})
        events.pop('player_feedback', None)
        events.pop('native_training_feedback', None)
        if self.pending_activity:
            events['native_activity_observed'] = deepcopy(list(self.pending_activity.values()))
        if self.pending:
            events['player_feedback'] = deepcopy(list(self.pending.values()))
        if any(self.overflow.values()):
            events['older_player_feedback_counts'] = {k: v for k, v in self.overflow.items() if v}
        if self.pending_text:
            events['native_training_feedback'] = deepcopy(list(self.pending_text.values()))
        if self.text_overflow:
            events['older_native_training_explanations_omitted'] = self.text_overflow
        if events:
            result['events'] = events
        else:
            result.pop('events', None)
        if self.current and (now is None or -.1 <= now-self.current[1] <= .75):
            result.setdefault('activity', {})['recent_player_feedback'] = self.current[0]
        return result

    def accepted(self, snapshot):
        events = snapshot.get('events') or {}
        for event in events.get('native_activity_observed', ()):
            self.pending_activity.pop(event.get('event_id'), None)
        for event in events.get('player_feedback', ()):
            self.pending.pop(event.get('event_id'), None)
        for kind, count in events.get('older_player_feedback_counts', {}).items():
            if kind in self.overflow:
                self.overflow[kind] = max(0, self.overflow[kind]-count)
        for event in events.get('native_training_feedback', ()):
            self.pending_text.pop(event.get('event_id'), None)
        self.text_overflow = max(0, self.text_overflow-events.get('older_native_training_explanations_omitted', 0))
