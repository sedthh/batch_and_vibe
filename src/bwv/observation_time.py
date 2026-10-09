"""Compact real-world receipt times, independent of simulated island daylight."""
from datetime import datetime, timezone, timedelta


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def turn_times(observed_at, now, *, received_at=None, question=True, wall_now=None):
    wall_now = wall_now or utc_now()
    result = {'sent_at': timestamp(wall_now)}
    if observed_at is not None and -.1 <= now-observed_at <= 1.5:
        result['observed_at'] = timestamp(wall_now-timedelta(seconds=max(0, now-observed_at)))
    if received_at is not None:
        result['message_received_at' if question else 'tick_received_at'] = received_at
    return result
