"""Compact evidence from the native action monitor, never inferred from speech."""
from .action_probe import ACTIONS
from .world_actions import WORLD_KINDS


def interrupted_results(probe, session):
    if not isinstance(probe,dict):return []
    entries=probe.get('interrupted_actions',[])
    if not isinstance(entries,list) or len(entries)>8:return []
    results=[]
    for entry in entries:
        if not isinstance(entry,dict):continue
        action=entry.get('action');stamp=entry.get('request_at_uptime_ms')
        replacement=entry.get('replacement_request_at_uptime_ms')
        if (action not in ACTIONS or type(stamp) is not int or stamp<=0
                or type(replacement) is not int or replacement<=stamp):continue
        results.append({'event_id':f'{session}:action:{stamp}:interrupted',
            'action':action,'status':'interrupted','evidence':'replaced_by_new_action'})
    return results


def action_result(probe, session):
    if not isinstance(probe, dict) or probe.get('result_current_creature') is not True:
        return None
    action = probe.get('requested_action')
    accepted = probe.get('request_at_uptime_ms')
    if (action not in ACTIONS
            or type(accepted) is not int or accepted <= 0):
        return None
    phase, outcome = probe.get('result_phase'), probe.get('outcome')
    if phase not in ('finished', 'rejected'):
        return None
    result = {'event_id': f'{session}:action:{accepted}', 'action': action}
    if probe.get('requested_target_kind') in WORLD_KINDS:
        result['requested_target'] = {'kind': probe['requested_target_kind']}
    if (phase == 'finished' and action == 'wake_up' and outcome == 'native_wake_observed'
            and probe.get('completion') == 'awake_observed' and probe.get('script_owned') is False):
        result.update(status='completed', evidence='awake_activity_observed')
    elif (phase == 'finished' and action == 'sleep_here' and outcome == 'native_sleep_observed'
            and probe.get('completion') == 'sleep_observed' and probe.get('script_owned') is False):
        result.update(status='completed', evidence='sleep_loop_observed')
    elif (phase == 'finished' and outcome == 'native_plan_changed_after_action'
            and probe.get('plan_observed') is True and probe.get('script_owned') is False):
        if action == 'approach_hand' and probe.get('completion') == 'reached_hand_target':
            result.update(status='completed', evidence='reached_requested_hand_position')
        elif action == 'move_to_position' and probe.get('completion') == 'reached_position_target':
            result.update(status='completed', evidence='reached_requested_map_position')
        elif action == 'move_to' and probe.get('completion') == 'reached_position_target':
            result.update(status='completed', evidence='reached_target_position_at_dispatch')
        elif action == 'pick_up' and probe.get('completion') == 'requested_object_held_observed':
            result.update(status='completed', evidence='requested_object_observed_in_hand')
        elif action == 'approach_object' and probe.get('completion') == 'reached_object_target':
            # A size-based stopping radius can overlap other nearby objects.
            # Report the observed plan transition, not a guessed destination hit.
            result.update(status='plan_ended', evidence='native_plan_ended')
        elif action.startswith('cast_'):
            result.update(status='plan_ended', evidence='casting_plan_ended')
        elif probe.get('completion') == 'body_animation_observed':
            result.update(status='completed', evidence='requested_body_animation_observed')
        else:
            result.update(status='plan_ended', evidence='native_plan_ended')
    elif outcome in ('external_script_control_observed', 'unexpected_script_flag'):
        result.update(status='interrupted', evidence='script_control')
    elif phase == 'rejected':
        result.update(status='rejected', evidence='native_guard')
        if action=='pick_up' and outcome in ('too_large_for_current_size',
                'too_heavy_to_lift','engine_pickup_not_allowed'):
            result.update(evidence='native_pickup_eligibility',reason=outcome)
    else:
        result.update(status='monitoring_ended', evidence='observation_window_ended')
    return result
