"""Discrete movement pace, never a model-supplied numeric engine speed."""
MOVEMENT_ACTIONS = ('approach_hand', 'approach_object', 'move_to', 'move_to_position')
PACE_FLAGS = {'auto': 0, 'walk': 0x10000000, 'run': 0x20000000}


def checked_pace(action, pace):
    if pace is not None and (not isinstance(pace, str) or pace not in PACE_FLAGS):
        raise ValueError('Pace must be auto, walk or run')
    if pace is not None and action not in MOVEMENT_ACTIONS:
        raise ValueError('Pace is only available for movement')
    return pace


def paced_packet(packet, probe, action, pace):
    import struct
    checked_pace(action, pace)
    if pace in (None, 'auto'):
        return packet
    if probe.get('movement_pace', {}).get('version') != 1:
        raise ValueError('This bridge does not support explicit movement pace')
    result = bytearray(packet)
    version = struct.unpack_from('<I', result, 4)[0]
    struct.pack_into('<I', result, 4, version | PACE_FLAGS[pace])
    return bytes(result)
