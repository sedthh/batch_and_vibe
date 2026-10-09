"""Public island identities, separate from changing positions and alignment."""
from dataclasses import asdict, dataclass
import json
import math
import re
import unicodedata

from .positions import position


@dataclass(frozen=True)
class IslandContext:
    available: bool = False
    gods: tuple[dict, ...] = ()
    roster_complete: bool = False
    read_errors: int = 0


def god_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'god_[0-7]', value) else None


def display_name(value):
    if (isinstance(value, str) and 0 < len(value.strip()) <= 32
            and not any(unicodedata.category(c).startswith('C') for c in value)):
        return value.strip()
    return None


def island_context(value):
    """Whitelist public fields; raw addresses and diagnostics never reach the model."""
    if isinstance(value, IslandContext):value = asdict(value)
    if not isinstance(value, dict) or value.get('available') is not True:return {}
    gods = []
    seen = set()
    from .creature_live import SPECIES
    for source in value.get('gods', ())[:8]:
        if not isinstance(source, dict):continue
        identity = god_id(source.get('god_id'))
        if not identity or identity in seen:continue
        seen.add(identity)
        item = {'god_id': identity}
        if source.get('is_player') is True:item['is_player'] = True
        name = display_name(source.get('name'))
        if name:item['name'] = name
        relation = source.get('relation_to_player')
        if relation in ('self', 'ally', 'enemy'):item['relation_to_player'] = relation
        if not item.get('is_player') and relation in ('ally', 'enemy'):
            item['label'] = f"{name or 'God ' + identity[4:]} ({relation})"
        alignment = source.get('alignment')
        if type(alignment) in (int, float) and math.isfinite(alignment) and -1 <= alignment <= 1:
            item['alignment'] = round(alignment, 4)
        creature = source.get('creature')
        if isinstance(creature, dict) and isinstance(creature.get('object_id'), str) and re.fullmatch(r'[a-f0-9]{24}', creature['object_id']):
            child = {'object_id': creature['object_id']}
            if creature.get('type') in SPECIES.values():child['type'] = creature['type']
            if creature.get('is_agent') is True:child['is_agent'] = True
            item['creature'] = child
        hand = source.get('hand')
        if isinstance(hand, dict) and (point := position(hand.get('position'))):
            item['hand'] = {'position': point}
            for field in ('distance', 'distance_to_god_hand'):
                distance = hand.get(field)
                if type(distance) in (int, float) and math.isfinite(distance) and distance >= 0:
                    item['hand'][field] = round(distance, 1)
        gods.append(item)
    if not gods:return {}
    result = {'gods': gods, 'roster_complete': value.get('roster_complete') is True}
    if result['roster_complete']:result['god_count'] = len(gods)
    # Scripted/unowned creatures need not appear in a player's registration slot.
    result['observed_registered_creature_count'] = sum('creature' in god for god in gods)
    return result


def island_roster(value):
    result = island_context(value)
    for god in result.get('gods', ()):
        god.pop('hand', None)
        god.pop('alignment', None)
    return result


def island_updates(value):
    """Only changing readings belong in each tick; identities live in the prompt."""
    gods = []
    for god in island_context(value).get('gods', ()):
        if god.get('is_player'):continue  # The existing god_hand carries these.
        item = {k: v for k, v in god.items() if k in ('god_id', 'label', 'alignment', 'hand')}
        if 'alignment' in item or 'hand' in item:gods.append(item)
    return {'other_gods': gods} if gods else {}


def with_island_roster(system, value):
    roster = island_roster(value)
    if roster:
        system += '\n\n## Current island gods and registered creatures (data)\n\n' + json.dumps(
            roster, ensure_ascii=False, separators=(',', ':'))
    return system


def inspect_god(value, identity):
    return next((g for g in island_context(value).get('gods', ()) if g['god_id'] == identity), None)


def registered_creature(value, identity):
    for god in island_context(value).get('gods', ()):
        creature = god.get('creature') or {}
        if creature.get('object_id') == identity:
            result = {'object_id': identity, 'kind': 'creature', 'owner_god_id': god['god_id'],
                      'owner_link': 'registered_creature'}
            if creature.get('type'):result['species'] = creature['type']
            return result
    return None
