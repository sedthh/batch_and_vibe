"""Detailed self inspection extends the same groups used in tick snapshots."""
from copy import deepcopy

from .creature_tools import inspect_self
from .llm_context import summarize_context, native_target_details, plan_label
from .object_details import public_object
from .world_actions import held_miracle_context


def inspect_self_frame(frame):
    raw = frame.get('observation') or {}
    context = frame['prompt']['context']
    compact = context if any(k in context for k in ('self', 'activity', 'events')) else summarize_context(context, raw)
    old = inspect_self(raw, context)
    creature = deepcopy(compact.get('self') or {})
    body = old.get('body_engine_units')
    if body:
        creature['body_engine_units'] = body
        for key in body: creature.pop(key + '_engine_units', None)
    traits = old.get('creature') or {}
    creature.update({k: v for k, v in traits.items() if k != 'god_alignment'})
    if 'position' in old: creature['position'] = old['position']
    if 'on_fire' in old: creature['on_fire'] = old['on_fire']
    held = raw.get('holding') or {}
    if held.get('holding') is True:
        creature['held_object'] = public_object(held)
    elif held.get('holding') is False:
        creature.pop('held_object', None)
    motivations = {k: deepcopy(held[k]) for k in ('drives', 'hunger_desire') if held.get(k) is not None}
    if motivations: creature['motivations'] = motivations
    learning = deepcopy(creature.pop('miracles', creature.get('learning', {})))
    if old.get('miracle_learning'): learning['knowledge'] = old['miracle_learning']
    if learning: creature['learning'] = learning
    bubble = held_miracle_context((frame.get('diagnostics') or {}).get('action_probe') or {})
    if bubble:
        creature['held_object'] = {**creature.get('held_object', {}), 'kind': 'miracle_bubble', 'miracle': bubble}
    activity = deepcopy(compact.get('activity') or {})
    native = old.get('native_activity') or {}
    if native.get('plan_name') and 'native_plan' not in activity:
        activity['native_plan'] = plan_label(native['plan_name'])
        if native.get('target_id') or native.get('target_kind'):
            target = {'kind': native.get('target_kind'), 'observed_object_id': native.get('target_id')}
            activity['native_plan_target'] = native_target_details(target, raw)
        if native.get('name_truncated'): activity['plan_label_incomplete'] = True
    if old.get('native_thought_text'): activity['native_thought_text'] = old['native_thought_text']
    events = {key: deepcopy(old[key]) for key in ('player_feedback', 'native_training_feedback') if key in old}
    result = {'self': creature, 'unavailable_readings': old['unavailable_readings']}
    if activity: result['activity'] = activity
    if events: result['events'] = events
    from .island import island_context
    island = island_context(raw.get('island'))
    if island: result['island'] = island
    from .god_hand import hand_contents
    hand = hand_contents(raw.get('god_hand', context.get('god_hand')))
    if 'god_alignment' in traits: hand['alignment'] = traits['god_alignment']
    if hand: result['god_hand'] = hand
    return result
