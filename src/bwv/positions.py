"""Model-facing map positions; portable objects deliberately keep ID-only targeting."""
import math
import re

POSITION_KINDS = frozenset(('house', 'creature', 'temple', 'storage', 'nursery',
    'cemetery', 'workshop', 'wonder', 'town_centre', 'totem', 'windmill', 'worship_site',
    'vortex', 'teleport'))


def position(value):
    if (not isinstance(value, dict) or set(value) != {'x', 'z'}
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 10240
                   for v in value.values())):
        return None
    return {k: round(value[k], 2) for k in ('x', 'z')}


def object_position(entry, radius=360):
    if entry.get('kind') not in POSITION_KINDS:
        return {}
    distances = [entry.get(k) for k in ('distance_engine_units', 'distance_to_god_hand_engine_units')]
    if not any(type(d) in (int, float) and math.isfinite(d) and 0 <= d <= radius for d in distances):
        return {}
    point = position(entry.get('position'))
    return {'position': point} if point else {}


def map_bounds(probe):
    contract=probe.get('position_movement', {})
    size=contract.get('map_size', {})
    if (contract.get('version') != 11 or contract.get('verified') is not True
            or not isinstance(size, dict) or set(size) != {'x','z'}
            or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 < v <= 10240
                   for v in size.values())):
        return None
    return dict(size)


def checked_position(value, bounds):
    point=position(value)
    if point is None or not bounds or any(not 0 < point[k] < bounds[k] for k in ('x','z')):
        raise ValueError('Destination is outside the current island map')
    return point


def position_target(point):
    return f"position:{point['x']:.2f}:{point['z']:.2f}"


def parse_position_target(value):
    if not isinstance(value,str) or not re.fullmatch(r'position:[0-9]{1,5}\.[0-9]{2}:[0-9]{1,5}\.[0-9]{2}',value):
        raise ValueError('Invalid coordinate destination')
    _,x,z=value.split(':')
    return {'x':float(x),'z':float(z)}
