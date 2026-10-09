"""Read native creature messages through verified selected-creature help records."""
import hashlib
import struct
import time
from dataclasses import dataclass

from .training_live import CREATURE_OWNER, CREATURE_MENTAL


# Message callback, localized text base, exclusive parameter bound. These are
# interoperability metadata for TrainingReader's exact executable fingerprint.
# The callback routes the selected creature's message to the game's help UI.
MESSAGE_LAYOUTS = {
    0: (0x4CA740, 462, 61, 'praise'),
    1: (0x4CA790, 523, 61, 'punishment'),
    4: (0x4CA880, 744, 40, 'praise'),
    5: (0x4CA8D0, 784, 40, 'punishment'),
    41: (0x4CAA80, 624, 40, 'instinct'),
}


@dataclass(frozen=True)
class NativeMessage:
    event_id: str
    kind: str
    text: str
    observed_at: float
    source: str = 'selected_creature_native_help'


@dataclass(frozen=True)
class NativeTextSample:
    session: str
    creature_id: str
    observed_at: float
    message: NativeMessage | None = None


def match_message(help_text, messages, game_tick):
    """Match the newest visible line to its owner-linked emission, never by prose."""
    if help_text.get('visible') is not True:
        return None
    lines = help_text.get('lines') or []
    ids = help_text.get('message_ids') or []
    tick = help_text.get('last_message_game_tick')
    if (not lines or not ids or type(tick) is not int or not 0 <= game_tick-tick <= 100
            or lines[0].get('ring_age') != 0):
        return None
    text = lines[0].get('text')
    # Parameterized UI templates need the engine's formatter; don't forward raw
    # placeholders or attempt to interpret pointer-sized formatting arguments.
    if (not isinstance(text, str) or not text.strip() or len(text) > 511
            or '%' in text or any(ord(c) < 32 for c in text)):
        return None
    matches = []
    for message in messages:
        layout = MESSAGE_LAYOUTS.get(message.get('message_kind'))
        argument = message.get('argument'); emitted = message.get('last_game_tick')
        if (layout and type(argument) is int and 0 <= argument < layout[2]
                and type(emitted) is int and emitted > 0 and 0 <= tick-emitted <= 2
                and message.get('pending') == 0 and ids[0] == layout[1]+argument):
            matches.append((message, layout[3], text.strip()))
    return matches[0] if len(matches) == 1 else None


def read_native_text(reader, observer):
    reader.ensure_running()
    started = time.monotonic()
    session = reader._session(observer)
    head = reader._head()
    game, tick, player, creature, _ = head
    reader.require_type(creature, b'.?AVCreature@@')
    if reader.word(creature+CREATURE_OWNER) != player:
        raise ValueError('Creature owner mismatch')
    mental = reader.word(creature+CREATURE_MENTAL)
    reader.require_type(mental, b'.?AVCreatureMental@@')
    identity = hashlib.sha256(f'{session}:{creature}:{mental}'.encode()).hexdigest()[:24]
    help_text = _help(reader, game)
    matched = match_message(help_text, _messages(reader, creature), tick)
    message = None
    now = time.monotonic()
    if matched:
        record, kind, text = matched
        callback, _, bound, _ = MESSAGE_LAYOUTS[record['message_kind']]
        table = 0x9D9CAC+record['message_kind']*296
        if reader.word(table) != callback or reader.word(table+4) != bound:
            raise ValueError('Unexpected creature help dispatch layout')
        key = hashlib.sha256(f'{session}:{identity}:{record["message_kind"]}:'
                             f'{record["argument"]}:{record["last_game_tick"]}'.encode()).hexdigest()[:24]
        message = NativeMessage(key, kind, text, now)
    if (reader._head() != head or reader.word(creature+CREATURE_OWNER) != player
            or reader.word(creature+CREATURE_MENTAL) != mental or _help(reader, game) != help_text
            or now-started > .75):
        raise ValueError('Creature text changed during capture')
    return NativeTextSample(session, identity, now, message)


def _wide(reader, address, limit=512):
    """Read at most one bounded UTF-16 string; do not return a truncated sentence."""
    if not address:
        return None
    data = bytearray()
    # Two-byte reads avoid crossing into an unreadable page after a terminator.
    for index in range(limit):
        unit = reader.read(address+index*2, 2)
        if unit == b'\0\0':
            text = data.decode('utf-16-le', errors='strict')
            return text or None
        data.extend(unit)
    raise ValueError('Native text exceeds capture bound')


def _help(reader, game):
    system = reader.word(game+0x25005C)
    reader.require_type(system, b'.?AVHelpSystem@@')
    text = reader.word(system+0x14)
    reader.require_type(text, b'.?AVHelpText@@')
    index = reader.word(text+0x94)
    message_ids = reader.read(system+0x584, 24)
    message_tick = reader.word(system+0x45D8)
    visible = reader.read(text+0xB8, 1)
    if index >= 6:
        raise ValueError('Invalid native text ring index')
    ring = reader.read(text+0x34, 96)
    rows = []
    for age in range(6):
        pointer, arg1, arg2, _ = struct.unpack_from('<4I', ring, ((index-age)%6)*16)
        if pointer:
            rows.append({'ring_age': age, 'text': _wide(reader, pointer),
                         'format_arguments': [arg1, arg2]})
    if (reader.word(game+0x25005C) != system or reader.word(system+0x14) != text
            or reader.word(text+0x94) != index or reader.read(text+0x34, 96) != ring
            or reader.read(system+0x584, 24) != message_ids
            or reader.word(system+0x45D8) != message_tick or reader.read(text+0xB8, 1) != visible):
        raise ValueError('Native text changed during capture')
    return {'visible': visible == b'\1',
            'message_ids': list(struct.unpack('<6I', message_ids)),
            'last_message_game_tick': message_tick,
            'lines': rows, 'attribution': 'unverified_game_help_text'}


def _messages(reader, creature):
    """Bounded creature-help history, including event tick and message parameter."""
    result = []
    remaining = 64
    for kind in range(42):
        slot = creature+0x220+kind*8
        head = reader.read(slot, 8)
        pointer, count = struct.unpack('<2I', head)
        if count > 64 or count > remaining:
            raise ValueError('Creature message history exceeds capture bound')
        seen = set()
        for _ in range(count):
            if not pointer or pointer in seen:
                raise ValueError('Invalid creature message list')
            seen.add(pointer)
            link = reader.read(pointer, 8)
            following, payload = struct.unpack('<2I', link)
            raw = reader.read(payload, 28)
            argument, _, _, _, _, tick, pending = struct.unpack('<7I', raw)
            result.append({'message_kind': kind, 'argument': argument,
                           'last_game_tick': tick, 'pending': pending})
            if reader.read(pointer, 8) != link or reader.read(payload, 28) != raw:
                raise ValueError('Creature message changed during capture')
            pointer = following
        remaining -= count
        if pointer or reader.read(slot, 8) != head:
            raise ValueError('Creature message list changed during capture')
    return result
