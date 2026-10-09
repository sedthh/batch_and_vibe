"""Public portal descriptions and the game's transfer export, parsed as data."""
from collections import Counter
from copy import deepcopy
import csv
import re

PORTAL_TYPES = {b'.?AVLandscapeVortexIn@@': 'vortex',
                b'.?AVLandscapeVortexOut@@': 'vortex',
                b'.?AVMagicTeleport@@': 'teleport'}
MAX_EXPORT_BYTES = 2 * 1024 * 1024


def portal_details(value):
    if not isinstance(value, dict):return {}
    route = value.get('route')
    if route == 'next_island' and value.get('role') in ('entrance', 'exit'):
        result = {'route': route, 'direction': 'one_way', 'role': value['role']}
        if type(value.get('accepting_objects')) is bool:
            result['accepting_objects'] = value['accepting_objects']
        if value.get('deposits_status') == 'unavailable':result['deposits_status'] = 'unavailable'
        deposits = value.get('deposits')
        if value['role'] == 'entrance' and isinstance(deposits, dict):
            clean = {'status': 'recorded_for_transfer'}
            for group, names in (
                    ('objects', ('trees', 'animals', 'villagers', 'resource_piles', 'other_objects', 'miracle_bubbles')),
                    ('resources', ('food', 'wood')),
                    ('miracle_bubbles', None)):
                values = deposits.get(group)
                if not isinstance(values, dict):continue
                from .spells import SPELLS
                valid = names or (*SPELLS.values(), 'unidentified')
                values = {k:v for k,v in values.items() if k in valid and type(v) is int and 0 < v <= 10**12}
                if values:clean[group] = values
            for key in ('unread_records',):
                if type(deposits.get(key)) is int and 0 < deposits[key] <= MAX_EXPORT_BYTES:
                    clean[key] = deposits[key]
            result['deposits'] = clean
        return result
    if route == 'same_island':
        return {'route': route, 'direction': 'two_way', 'temporary': True}
    return {}


def parse_deposits(data):
    """Count complete export records. Never evaluate code or expose raw strings."""
    if len(data) > MAX_EXPORT_BYTES:raise ValueError('Vortex export is too large')
    objects, resources, miracles = Counter(), Counter(), Counter()
    unread = 0
    complete = data.rsplit(b'\n', 1)[0] if b'\n' in data else b''
    for line in complete.decode('ascii', errors='replace').splitlines():
        if not line.strip():continue
        match = re.fullmatch(r'([A-Z_]+)\((.*)\)', line.strip())
        if not match or len(line) > 4096:
            unread += 1
            continue
        command, body = match.groups()
        try:args = next(csv.reader([body], skipinitialspace=True, strict=True))
        except (csv.Error, StopIteration):
            unread += 1
            continue
        category = {'CREATE_NEW_TREE': ('trees', 7), 'CREATE_NEW_ANIMAL': ('animals', 5),
                    'CREATE_TOWN_VILLAGER': ('villagers', 4),
                    'CREATE_MOBILE_STATIC': ('other_objects', 7)}.get(command)
        if category and len(args) == category[1]:objects[category[0]] += 1
        elif command == 'CREATE_POT' and len(args) == 4:
            try:
                resource, amount = int(args[2]), int(args[3])
                if not 0 <= amount <= 10**9:raise ValueError('Invalid quantity')
            except ValueError:
                unread += 1
                continue
            objects['resource_piles'] += 1
            if resource in (0, 1):resources[('food', 'wood')[resource]] += amount
            else:unread += 1
        elif command == 'CREATE_ONE_SHOT_SPELL_PU' and len(args) == 2:
            from .spells import SPELLS
            spell = {'LIGHTNING_BOLT': 'lightning', 'HEALING': 'heal'}.get(args[1], args[1].lower())
            miracles[spell if spell in SPELLS.values() else 'unidentified'] += 1
            objects['miracle_bubbles'] += 1
        else:unread += 1
    result = {'status': 'recorded_for_transfer'}
    for key, counts in (('objects', objects), ('resources', resources), ('miracle_bubbles', miracles)):
        if counts:result[key] = dict(counts)
    if unread:result['unread_records'] = unread
    return result


def portal_summary(objects):
    """A small current landmark list, shared by both proximity views."""
    result = []
    for obj in objects:
        if obj.get('kind') not in ('vortex', 'teleport') or not obj.get('object_id'):continue
        metadata = portal_details(obj.get('portal'))
        if not metadata:continue
        result.append({'object_id': obj['object_id'], 'kind': obj['kind'], 'portal': metadata})
        if len(result) == 4:break
    return deepcopy(result)
