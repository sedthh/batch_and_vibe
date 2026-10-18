"""Bounded read-only portal lists. No game calls, profile scans or script execution."""
from copy import deepcopy
from pathlib import Path
import hashlib
import math
import struct

from .positions import position
from .surroundings import NearbyObject
from .vortex import PORTAL_TYPES, MAX_EXPORT_BYTES, parse_deposits

# LandscapeVortex constructor/deletion maintain this list; +88 is its next link.
VORTEX_LIST = 0xD38220
VORTEX_TYPES = {*PORTAL_TYPES} - {b'.?AVMagicTeleport@@'} | {b'.?AVLandscapeVortexVolc@@', b'.?AVLandscapeVortex@@'}


def _list(reader, address, allowed, kind):
    header = reader.read(address, 8)
    node, count = struct.unpack('<II', header)
    if count > 32:raise ValueError('Portal list is too large')
    checks, nodes, seen = [(address, header)], [], set()
    for _ in range(count):
        if not node or node in seen:raise ValueError('Portal list changed')
        runtime = kind(node)
        if runtime not in allowed:raise ValueError('Unexpected portal type')
        seen.add(node)
        link = reader.read(node + 0x88, 4)
        checks.extend(((node, reader.read(node, 4)), (node + 0x88, link)))
        nodes.append((node, runtime))
        node, = struct.unpack('<I', link)
    if node:raise ValueError('Portal count changed')
    return nodes, checks


def _filename(reader, pointer):
    # Original filename buffer is 256 bytes; never publish its local path.
    data = bytearray()
    for index in range(0, 256, 32):
        try:chunk = reader.read(pointer + index, 32)
        except OSError:
            # A valid short string may end immediately before an unreadable page.
            chunk = bytearray()
            for offset in range(32):
                char = reader.read(pointer + index + offset, 1)
                chunk.extend(char)
                if char == b'\0':break
        data.extend(chunk.split(b'\0', 1)[0])
        if b'\0' in chunk:
            if not data:raise ValueError('Empty portal export filename')
            return data.decode('mbcs')
    raise ValueError('Invalid portal export filename')


def _deposits(reader, obj, session):
    save = reader.word(obj + 0xEC)
    reader.require_type(save, b'.?AVVortexSave@@')
    if reader.read(save + 0x1C, 1) != b'\0':raise ValueError('Not a transfer writer')
    pointer = reader.word(save + 0x18)
    root = Path(reader.game_directory).resolve()
    filename = _filename(reader, pointer)
    path = Path(filename)
    if not path.is_absolute():path = root / path
    path = path.resolve()
    if path.name.casefold() != 'vortex.txt' or not path.is_relative_to(root):
        raise ValueError('Invalid portal export location')
    stat = path.stat()
    if stat.st_size > MAX_EXPORT_BYTES:raise ValueError('Vortex export is too large')
    identity = (session, obj, save, path, stat.st_mtime_ns, stat.st_size)
    cache = getattr(reader, '_vortex_export', None)
    if cache and cache[0] == identity:return deepcopy(cache[1])
    with path.open('rb') as stream:data = stream.read(MAX_EXPORT_BYTES + 1)
    after = path.stat()
    if ((stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or reader.word(obj + 0xEC) != save or reader.word(save + 0x18) != pointer):
        raise ValueError('Portal export changed while reading')
    result = parse_deposits(data)
    reader._vortex_export = identity, result
    return deepcopy(result)


def read_portals(reader, session, creature, player, creature_position, hand_position, kind):
    """Current landmarks near either viewpoint, independent of creature beliefs."""
    items, errors = [], 0
    for address, allowed in ((VORTEX_LIST, VORTEX_TYPES),
                             (player + 0xA58, {b'.?AVMagicTeleport@@'})):
        try:
            nodes, checks = _list(reader, address, allowed, kind)
            group = []
            for obj, runtime in nodes:
                if runtime not in PORTAL_TYPES:continue  # Volcanoes are not island entrances.
                x, z, altitude = struct.unpack('<iif', reader.read(obj + 0x14, 12))
                point = position({'x': x * 10 / 65536, 'z': z * 10 / 65536})
                if point is None or not math.isfinite(altitude):raise ValueError('Invalid portal position')
                distance = math.hypot(point['x'] - creature_position[0], point['z'] - creature_position[1])
                hand_distance = math.hypot(point['x'] - hand_position[0], point['z'] - hand_position[1]) if hand_position else None
                if runtime != b'.?AVMagicTeleport@@' and distance > 360 and (hand_distance is None or hand_distance > 360):continue
                metadata = {'route': 'same_island', 'direction': 'two_way', 'temporary': True}
                if runtime != b'.?AVMagicTeleport@@':
                    entrance = runtime == b'.?AVLandscapeVortexIn@@'
                    metadata = {'route': 'next_island', 'direction': 'one_way', 'role': 'entrance' if entrance else 'exit'}
                    if entrance:
                        metadata['accepting_objects'] = reader.word(obj + 0xE4) == 1
                        try:metadata['deposits'] = _deposits(reader, obj, session)
                        except (OSError, ValueError, AttributeError):
                            errors += 1
                            metadata['deposits_status'] = 'unavailable'
                identity = hashlib.sha256(f'{session}:{creature}:{obj}'.encode()).hexdigest()[:24]
                group.append(NearbyObject(identity, PORTAL_TYPES[runtime], round(distance, 1),
                    distance_to_god_hand_engine_units=round(hand_distance, 1) if hand_distance is not None else None,
                    position=point, lookup_handle=obj, portal=metadata))
            if any(reader.read(a, len(value)) != value for a, value in checks):
                raise ValueError('Portal list changed while reading')
            if allowed == {b'.?AVMagicTeleport@@'}:
                # The owner's native network supplies real destinations even
                # when its other gates are beyond the current local view.
                if not any(o.distance_engine_units <= 360 or
                        o.distance_to_god_hand_engine_units is not None and o.distance_to_god_hand_engine_units <= 360
                        for o in group):group=[]
                identities=[o.object_id for o in group]
                for obj in group:
                    obj.portal['connected_exit_ids']=[identity for identity in identities if identity != obj.object_id]
            items.extend(group)
        except (OSError, ValueError):errors += 1
    if not any(o.portal.get('deposits') is not None for o in items):
        reader._vortex_export = None
    return items, errors
