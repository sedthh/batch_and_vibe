"""Bounded event/heartbeat preparation, separate from permission to deliver to AI."""
import json
import math


class ContextUpdates:
    def __init__(self, heartbeat=15, event_cooldown=2):
        if not 0 < event_cooldown <= heartbeat or not math.isfinite(heartbeat):
            raise ValueError('Invalid context update cadence')
        self.heartbeat, self.event_cooldown = heartbeat, event_cooldown
        self._identity = None
        self._last_signature = None
        self._last_time = None
        self._last_context = None
        self._last_system = None
        self._count = 0
        self.latest = None

    def update(self, observation, prompt, gate, now):
        identity = (observation.session, observation.creature_id)
        if identity != self._identity:
            self._identity = identity
            self._last_signature = self._last_time = self.latest = None
            self._last_context = self._last_system = None
        reason = None
        if not observation.connected:
            reason = 'disconnected'
        elif now - observation.observed_at > 1.5 or observation.observed_at > now + .1:
            reason = 'stale'
        else:
            for value, label in ((observation.in_temple, 'temple'), (observation.paused, 'paused'),
                (observation.cutscene, 'cutscene'), (observation.loading, 'loading'),
                (observation.script_owns_creature, 'scripted_creature'),
                (observation.player_has_control is False, 'control_locked'),
                (observation.creature_id is None, 'no_creature')):
                if value is True:
                    reason = label
                    break
        if reason:
            self._last_signature = self._last_time = None
            self._last_context = self._last_system = None
            return {'state': 'suspended', 'reason': reason, 'last_update': self.latest}
        context = json.loads(prompt.context_json)
        if not context:
            # Never send an empty packet. Rebase the next meaningful update so
            # a receiver cannot retain facts from before an empty observation.
            self._last_signature = self._last_time = None
            self._last_context = self._last_system = None
            return {'state': 'skipped_empty', 'reason': 'no_active_context',
                    'last_update': self.latest}
        # Continuous motivation changes ride the heartbeat, not an event flood.
        semantic = {k: v for k, v in context.items() if k not in (
            'hunger_motivation_engine_units', 'instinct_motivations_engine_units', 'body_engine_units')}
        # Aggregated nearby counts ignore distance jitter and equivalent object
        # replacements. Exact identities remain in full debug observations.
        signature = json.dumps([prompt.system, semantic], sort_keys=True)
        trigger = None
        if self._last_time is None:
            trigger = 'initial_or_resumed'
        elif now - self._last_time >= self.event_cooldown and signature != self._last_signature:
            trigger = 'context_changed'
        elif now - self._last_time >= self.heartbeat:
            trigger = 'heartbeat'
        if trigger:
            # Deltas require the same receiver/session. Initial/resumed and
            # heartbeat snapshots repair its baseline; species changes do too.
            if (trigger != 'context_changed' or self._last_context is None
                    or self._last_system != prompt.system):
                packet = {'snapshot': context}
            else:
                changed = {k: v for k, v in context.items() if self._last_context.get(k) != v}
                removed = sorted(self._last_context.keys() - context.keys())
                packet = {}
                if changed:
                    packet['changes'] = changed
                if removed:
                    packet['no_longer_observed'] = removed
            self._count += 1
            self.latest = {'number': self._count, 'prepared_at_monotonic': now,
                'trigger': trigger, 'context': context,
                'system': prompt.system, 'llm_input': packet,
                'delivery': 'preview_only_not_sent',
                'control_gate_allowed_at_preparation': gate.allowed,
                'block_reasons': list(gate.reasons), 'provider_calls': 0}
            self._last_time, self._last_signature = now, signature
            self._last_context, self._last_system = context, prompt.system
        return {'state': 'preview_prepared' if trigger else 'waiting',
                'heartbeat_seconds': self.heartbeat, 'event_cooldown_seconds': self.event_cooldown,
                'last_update': self.latest}
