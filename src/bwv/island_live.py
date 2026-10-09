"""Read the engine's small player roster, alliances and hand controllers. No calls."""
from dataclasses import dataclass
import hashlib
import math
import struct
import time

from .island import IslandContext, display_name
from .pipe import snapshot
from .training_live import PLAYER_START, PLAYER_STRIDE, PLAYER_CREATURE, PLAYER_HANDS, CREATURE_OWNER, CREATURE_MENTAL
from .towns_live import PLAYER_NUMBER, NEUTRAL_PLAYER_INDEX

PLAYER_TYPE, PLAYER_NAME, PLAYER_COMPUTER, PLAYER_ALLIANCES = 0x8E0, 0x8E4, 0x944, 0x950
POSITION_SCALE = 10 / 65536


@dataclass(frozen=True)
class IslandSample:
    session: str
    creature_id: str | None
    context: IslandContext


def player_slot(reader, game, player):
    slot, remainder = divmod(player - game - PLAYER_START, PLAYER_STRIDE)
    if remainder or not 0 <= slot < 8:raise ValueError('Invalid island player link')
    reader.require_type(player, b'.?AVGPlayer@@')
    if reader.read(player + PLAYER_NUMBER, 1)[0] != slot:
        raise ValueError('Island player slot mismatch')
    return slot


def creature_ownership(reader, game, creature):
    owner = reader.word(creature + CREATURE_OWNER)
    if not owner:return None, None
    slot = player_slot(reader, game, owner)
    neutral = reader.read(game + NEUTRAL_PLAYER_INDEX, 1)[0]
    if neutral >= 8:raise ValueError('Invalid neutral player slot')
    if slot == neutral:return None, None
    registered = reader.word(owner + PLAYER_CREATURE)
    if (reader.word(creature + CREATURE_OWNER) != owner
            or reader.word(owner + PLAYER_CREATURE) != registered):
        raise ValueError('Creature ownership changed during read')
    return f'god_{slot}', 'registered_creature' if registered == creature else 'engine_only'


def point(reader, game, address, *, fixed=True):
    raw = reader.read(address, 12)
    if fixed:
        x, z, height = struct.unpack('<iif', raw)
        x, z = x * POSITION_SCALE, z * POSITION_SCALE
    else:x, height, z = struct.unpack('<fff', raw)
    width, depth = reader.word(game + 0x59C8), reader.word(game + 0x59C4)
    if (not all(math.isfinite(v) for v in (x, z, height)) or not 0 < width <= 1024
            or not 0 < depth <= 1024 or not 0 < x < width * 10 or not 0 < z < depth * 10):
        raise ValueError('Invalid island position')
    return {'x': round(x, 2), 'z': round(z, 2)}


def interface_position(reader, game, interface, slot):
    reader.require_type(interface, b'.?AVGInterface@@')
    status = reader.word(interface + 0x39C)
    reader.require_type(status, b'.?AVGInterfaceStatus@@')
    if reader.read(status + 0x28, 1)[0] != slot or reader.word(status + 0x2C) != interface:
        raise ValueError('Island hand owner mismatch')
    result = point(reader, game, status + 0xC8, fixed=False)
    if reader.word(interface + 0x39C) != status:raise ValueError('Island hand changed')
    return result


def player_hand(reader, game, player, slot, *, own_interface=None):
    if own_interface:return interface_position(reader, game, own_interface, slot)
    computer = reader.word(player + PLAYER_COMPUTER)
    if computer:
        reader.require_type(computer, b'.?AVGComputerPlayer@@')
        if reader.word(computer + 0x28) != player:raise ValueError('Computer player owner mismatch')
        if reader.word(computer + 0x1B8):
            # GComputerPlayer::GetHandPos reads PlayerActionState's current
            # MapCoords, not its desired position or the player's temple centre.
            hand = reader.word(computer + 0x34)
            reader.require_type(hand, b'.?AVPlayerActionState@@')
            if reader.word(hand + 0x6C) != computer or reader.word(hand + 0x4C) != slot:
                raise ValueError('Computer hand owner mismatch')
            result = point(reader, game, hand + 0x14)
            if (reader.word(player + PLAYER_COMPUTER) != computer or reader.word(computer + 0x34) != hand
                    or not reader.word(computer + 0x1B8)):
                raise ValueError('Computer hand changed')
            return result
    # Multiple interfaces can belong to one human/network player. Prefer the
    # active device exactly as GetInterfaceStatus does, not an inactive cursor.
    interfaces = struct.unpack('<18I', reader.read(player + PLAYER_HANDS, 72))
    devices = [(index, interface, reader.word(interface + 0x1BC))
               for index, interface in enumerate(interfaces) if interface]
    has_device = any(device for _, _, device in devices)
    for index, interface, device in devices:
        if device and reader.word(device + 0x1F4) != 1:continue
        if not device and has_device:continue
        result = interface_position(reader, game, interface, slot)
        if (reader.word(player + PLAYER_HANDS + index * 4) != interface
                or reader.word(interface + 0x1BC) != device):raise ValueError('Player interface changed')
        return result
    return None


def read_island(reader, observer=None):
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(snapshot(reader.pid) if observer is None else observer)
    head = reader._head()
    game, tick, player, creature, interface = head
    own_slot = player_slot(reader, game, player)
    neutral = reader.read(game + NEUTRAL_PLAYER_INDEX, 1)[0]
    if neutral >= 8 or own_slot == neutral:raise ValueError('Invalid island neutral player')
    identity = None
    if creature:
        reader.require_type(creature, b'.?AVCreature@@')
        if reader.word(creature + CREATURE_OWNER) != player:raise ValueError('Player creature owner mismatch')
        mental = reader.word(creature + CREATURE_MENTAL)
        reader.require_type(mental, b'.?AVCreatureMental@@')
        identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
    gods, checks, errors, complete = [], [], 0, True
    try:origin = point(reader, game, creature + 0x14) if creature else None
    except (OSError, ValueError):origin = None;errors += 1
    try:own_hand = player_hand(reader, game, player, own_slot, own_interface=interface)
    except (OSError, ValueError):own_hand = None;errors += 1
    for slot in range(8):
        if slot == neutral:continue
        address = game + PLAYER_START + slot * PLAYER_STRIDE
        try:
            player_slot(reader, game, address)
            state = reader.word(address + PLAYER_TYPE)
            checks.append((address + PLAYER_TYPE, struct.pack('<I', state)))
            # GetNextActivePlayer excludes unused slots (0) and neutral (3).
            if state == 0:continue
            if state not in (1, 2):raise ValueError('Unknown island player state')
        except (OSError, ValueError):errors += 1;complete = False;continue
        god = {'god_id': f'god_{slot}'}
        if slot == own_slot:god.update(is_player=True, relation_to_player='self')
        else:
            try:
                raw = reader.read(player + PLAYER_ALLIANCES + slot * 4, 4)
                alliance, = struct.unpack('<f', raw)
                if not math.isfinite(alliance):raise ValueError('Invalid alliance')
                # IsAllied uses >0; non-allied active players are native AI rivals.
                god['relation_to_player'] = 'ally' if alliance > 0 else 'enemy'
                checks.append((player + PLAYER_ALLIANCES + slot * 4, raw))
            except (OSError, ValueError):errors += 1
        try:
            raw = reader.read(address + PLAYER_NAME, 64)
            end = next((i for i in range(0, 64, 2) if raw[i:i+2] == b'\0\0'), 64)
            name = display_name(raw[:end].decode('utf-16-le'))
            if name:god['name'] = name
            checks.append((address + PLAYER_NAME, raw))
        except (OSError, ValueError):errors += 1
        try:
            from .alignment_live import read_alignment
            god['alignment'] = read_alignment(reader, address, 0x60)
        except (OSError, ValueError):errors += 1
        try:
            registered = reader.word(address + PLAYER_CREATURE)
            if registered:
                reader.require_type(registered, b'.?AVCreature@@')
                if reader.word(registered + CREATURE_OWNER) != address:raise ValueError('Registered creature owner mismatch')
                child = {'object_id': hashlib.sha256(f'{session}:{creature}:{registered}'.encode()).hexdigest()[:24]}
                from .creature_live import read_object_species
                species = read_object_species(reader, registered)
                if species:child['type'] = species
                if registered == creature:child['is_agent'] = True
                god['creature'] = child
                checks.append((registered + CREATURE_OWNER, struct.pack('<I', address)))
            checks.append((address + PLAYER_CREATURE, struct.pack('<I', registered)))
        except (OSError, ValueError):errors += 1
        try:
            hand = own_hand if slot == own_slot else player_hand(reader, game, address, slot)
            if hand:
                god['hand'] = {'position': hand}
                for field, center in (('distance', origin), ('distance_to_god_hand', own_hand)):
                    if center:god['hand'][field] = round(math.hypot(hand['x']-center['x'], hand['z']-center['z']), 1)
        except (OSError, ValueError):errors += 1
        gods.append(god)
    final = reader._head()
    if (head[:1] + head[2:] != final[:1] + final[2:]
            or reader.read(game + NEUTRAL_PLAYER_INDEX, 1)[0] != neutral
            or any(reader.read(a, len(b)) != b for a, b in checks)
            or creature and (reader.word(creature + CREATURE_MENTAL) != mental
                             or reader.word(creature + CREATURE_OWNER) != player)):
        raise ValueError('Island identity changed during read')
    if reader._session(snapshot(reader.pid)) != session:raise ValueError('Island session changed')
    if time.monotonic() - started > .75:raise ValueError('Island read exceeded freshness limit')
    return IslandSample(session, identity, IslandContext(bool(gods), tuple(gods), complete, errors))
