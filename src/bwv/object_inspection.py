"""Read-only inspection of one observation, shared by single and batch lookups."""
from copy import deepcopy

from .object_details import known_objects, public_object


SECTIONS = ('physical_traits', 'person', 'details', 'ownership', 'town_needs', 'belief', 'miracles')
IDENTITY = ('object_id', 'kind', 'object_type', 'species', 'name', 'engine_class',
            'position', 'distance_engine_units', 'distance_to_god_hand_engine_units', 'town_id', 'portal')
TOWN_IDENTITY = ('town_id', 'tribe', 'position')


def select_sections(result, sections):
    """Omitted sections mean full detail; identity is retained in every view."""
    if sections is None:
        return deepcopy(result)
    wanted = set(sections)
    out = {k: deepcopy(v) for k, v in result.items() if k in ('status', 'executed', 'object_id', 'held_by')}
    if 'object' in result:
        obj = result['object']
        keys = set(IDENTITY) | (wanted & {'physical_traits', 'person', 'details'})
        if 'physical_traits' in wanted: keys.add('on_fire')
        if 'miracles' in wanted: keys.add('miracle')
        if 'ownership' in wanted: keys.update(('owner_god_id', 'owner_link'))
        out['object'] = {k: deepcopy(v) for k, v in obj.items() if k in keys}
    if 'town' in result:
        town = result['town']
        keys = set(TOWN_IDENTITY)
        if 'town_needs' in wanted: keys.update(('needs', 'top_needs'))
        if 'ownership' in wanted: keys.update(('control', 'controlling_god_id'))
        if 'belief' in wanted:
            keys.update(k for k in town if 'belief' in k or 'conversion' in k)
        out['town'] = {k: deepcopy(v) for k, v in town.items() if k in keys}
    for key in ('god', 'owner_god', 'engine_owner_god', 'controlling_god'):
        if key in result:
            value = result[key]
            out[key] = deepcopy(value) if 'ownership' in wanted else {
                k: deepcopy(v) for k, v in value.items() if k in ('god_id', 'name', 'label')}
    return out


class InspectionView:
    """Index once per call. Never retain a world snapshot across calls."""
    def __init__(self, frame):
        from .god_hand import hand_contents
        self.raw = frame.get('observation') or {}
        probe = (frame.get('diagnostics') or {}).get('action_probe') or {}
        self.held_generation = (probe.get('held_actions') or {}).get('target_generation')
        self.objects = {o['object_id']: o for o in known_objects(self.raw.get('surroundings') or {})}
        self.towns = {t['town_id']: t for t in (self.raw.get('surroundings') or {}).get('towns', []) if t.get('town_id')}
        self.god_held = hand_contents(self.raw.get('god_hand')).get('held_object')
        if self.god_held and self.god_held.get('object_id'):
            self.objects[self.god_held['object_id']] = self.god_held
        held = self.raw.get('holding') or {}
        self.creature_held = dict(held) if held.get('holding') is True else None
        if self.creature_held:
            from .world_actions import held_miracle_context
            bubble = held_miracle_context(probe)
            if bubble: self.creature_held.update(kind='miracle_bubble', miracle=bubble['spell'])
        if self.creature_held and held.get('object_id'):
            self.objects[held['object_id']] = self.creature_held

    def describe(self, identity, sections=None):
        from .island import registered_creature, inspect_god
        obj = self.objects.get(identity)
        if obj is None:
            leash = self.raw.get('leash') or {}
            if leash.get('active') is True and leash.get('attachment') == 'object' and leash.get('target_id') == identity:
                obj = {'object_id': identity, 'kind': leash.get('target_kind') or 'object'}
        if obj is None:
            obj = registered_creature(self.raw.get('island'), identity)
        town = self.towns.get(obj.get('town_id') if obj else identity)
        god = inspect_god(self.raw.get('island'), identity)
        result = {'status': 'observed' if obj or town or god else 'object_not_currently_known', 'executed': False}
        if god: result['god'] = god
        if obj: result['object'] = public_object(obj)
        owner = inspect_god(self.raw.get('island'), obj.get('owner_god_id')) if obj else None
        if owner: result['owner_god' if obj.get('owner_link') == 'registered_creature' else 'engine_owner_god'] = owner
        if obj is self.god_held and self.god_held: result['held_by'] = 'player_god'
        if obj is self.creature_held and self.creature_held: result['held_by'] = 'creature'
        if town: result['town'] = deepcopy(town)
        controller = inspect_god(self.raw.get('island'), town.get('controlling_god_id')) if town else None
        if controller: result['controlling_god'] = controller
        return select_sections(result, sections)


def brief_object(obj):
    result = public_object(obj)
    return {k: v for k, v in result.items() if k in (*IDENTITY, 'on_fire', 'miracle', 'owner_god_id', 'owner_link')}


def public_targets(value):
    """Hide redundant request leases, keeping the fallback for anonymous targets."""
    if isinstance(value, list):
        return [public_targets(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {k: public_targets(v) for k, v in value.items()}
    if result.get('object_id'):
        result.pop('target_id', None)
    selected = result.get('selected_object')
    if isinstance(selected, dict) and selected.get('object_id') and isinstance(result.get('target'), str):
        result['target'] = result['selected_object']['object_id']
    return result
