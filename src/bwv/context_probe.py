"""Opt-in, read-only diagnostics for native text and learning attribution.

These layouts have static evidence but require labelled live QA. Nothing in this
module is part of the model context or an engine action. Use TrainingReader's
executable fingerprint and read-only process handle; never scan arbitrary memory.
"""
import struct
import math
from dataclasses import asdict

from .training_live import CREATURE_MENTAL, CREATURE_OWNER, HAND_STATE, HAND_TARGET
from .experience_live import ACTION_INFO, ACTION_STRIDE, ACTION_NAME
from .native_text_live import _wide, _help, _messages
from .native_text_live import read_native_text
from .pipe import snapshot


def _learning(reader, mental):
    learning = mental+0x2AD8
    reader.require_type(learning, b'.?AVCreatureLearning@@')
    ring = learning+8
    head = reader.read(ring+0x20, 8)
    index, count = struct.unpack('<2I', head)
    if index >= 5 or count > 5:
        raise ValueError('Invalid learning context ring')
    rows = []
    for age in range(count):
        entry = ring+0x28+((index-age-1)%5)*0x70
        reader.require_type(entry, b'.?AVCreatureContext@@')
        reader.require_type(entry+8, b'.?AVCreaturePlan@@')
        raw = reader.read(entry, 0x70)
        action = struct.unpack_from('<I', raw, 0x20)[0]
        if action >= 328:
            raise ValueError('Invalid learning context action')
        info = ACTION_INFO+action*ACTION_STRIDE
        reader.require_type(info, b'.?AVCreatureActionInfo@@')
        name = reader.read(info+ACTION_NAME, 32).split(b'\0', 1)[0].decode('ascii')
        # Keep opaque scalars diagnostic-only until each meaning is established.
        words = {hex(offset): struct.unpack_from('<I', raw, offset)[0]
                 for offset in (0x10, 0x20, 0x38, 0x3C, 0x40, 0x44, 0x48, 0x4C, 0x50, 0x58)}
        rows.append({'ring_age': age, 'native_plan': name, 'diagnostic_words': words})
        if reader.read(entry, 0x70) != raw:
            raise ValueError('Learning context changed during capture')
    if reader.read(ring+0x20, 8) != head:
        raise ValueError('Learning ring changed during capture')
    return {'contexts': rows, 'attribution': 'not_confirmed_training_targets'}


def read_context_probe(reader):
    reader.ensure_running()
    head = reader._head()
    game, tick, player, creature, hand = head
    reader.require_type(creature, b'.?AVCreature@@')
    if reader.word(creature+CREATURE_OWNER) != player:
        raise ValueError('Selected creature owner mismatch')
    mental = reader.word(creature+CREATURE_MENTAL)
    reader.require_type(mental, b'.?AVCreatureMental@@')
    feedback = reader.read(mental+0x18C5C, 8)
    magnitude, feedback_tick = struct.unpack('<fI', feedback)
    if not math.isfinite(magnitude) or abs(magnitude) > 1 or feedback_tick > tick:
        raise ValueError('Invalid native feedback sample')
    result = {'game_tick': tick, 'feedback_tick': feedback_tick, 'feedback_magnitude': magnitude,
              'hand_state': reader.word(hand+HAND_STATE),
              'hand_targets_selected_creature': reader.word(hand+HAND_TARGET) == creature}
    for label, read in (('help', lambda: _help(reader, game)),
                        ('learning', lambda: _learning(reader, mental)),
                        ('creature_messages', lambda: _messages(reader, creature))):
        try:
            result[label] = read()
        except (OSError, ValueError) as error:
            result[label] = {'unavailable': type(error).__name__}
    if (reader._head() != head or reader.word(creature+CREATURE_OWNER) != player
            or reader.word(creature+CREATURE_MENTAL) != mental
            or reader.read(mental+0x18C5C, 8) != feedback):
        raise ValueError('Creature changed during context capture')
    try:
        result['attributed_text'] = asdict(read_native_text(reader, snapshot(reader.pid)))
    except (OSError, ValueError) as error:
        result['attributed_text'] = {'unavailable': type(error).__name__}
    return result
