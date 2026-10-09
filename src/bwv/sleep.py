"""Verified native sleep activity for scheduling, never permission to wake."""
import hashlib
import math


def sleep_state(source, now):
    """True covers entry/loop/exit and queued sleep; None means unverified.

    Native plan names alone describe intentions and cannot establish sleep.
    Bind the activity to the selected creature and preserve source freshness.
    """
    source = source or {}
    obs = source.get('observation') or {}
    probe = (source.get('diagnostics') or {}).get('action_probe') or {}
    stamp = obs.get('observed_at')
    if (type(stamp) not in (int, float) or not math.isfinite(stamp)
            or not -.1 <= now-stamp <= 1.5 or probe.get('supported') is not True):
        return None
    session = obs.get('session')
    epoch = probe.get('epoch')
    if (not isinstance(session, str) or type(epoch) is not int
            or session.rsplit(':', 1)[-1] != str(epoch)):
        return None
    contract = probe.get('conversation_actions') or {}
    handles = [contract.get(k) for k in ('creature_handle', 'mental_handle')]
    if any(type(v) is not int or not 0 < v <= 0xffffffff for v in handles):
        return None
    identity = hashlib.sha256(f'{session}:{handles[0]}:{handles[1]}'.encode()).hexdigest()[:24]
    if identity != obs.get('creature_id'):
        return None
    activity = probe.get('activity') or {}
    mode, animation = activity.get('controller_mode'), activity.get('animation')
    pending = activity.get('sleep_in_sequence')
    if (activity.get('verified') is not True or type(mode) is not int or not 0 <= mode <= 37
            or type(animation) is not int or animation < 0 or type(pending) is not bool):
        return None
    return pending or (animation == 29 and 2 <= mode <= 4)


def sleep_phase(source, now):
    """Model-facing phase; queued sleep must never imply already asleep."""
    if sleep_state(source, now) is not True:
        return None
    activity=source['diagnostics']['action_probe']['activity']
    if activity['animation']==29:
        phase={2:'falling_asleep',3:'asleep',4:'waking_up'}.get(activity['controller_mode'])
        if phase:return phase
    return 'preparing_to_sleep'
