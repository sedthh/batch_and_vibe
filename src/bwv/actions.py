"""Deterministic intent resolution against adapter-issued capabilities.

No model, pathfinding guess, memory address or engine call belongs here. A live
adapter must establish reachability, pickup permission and object incarnation.
Current belief-list observations cannot supply those capabilities.
"""
from dataclasses import dataclass
import json
import math
import re


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', value) is not None


@dataclass(frozen=True)
class ActionIntent:
    action: str
    kind: str
    selection: str = 'nearest_reachable'
    target_id: str | None = None

    def __post_init__(self):
        if self.action != 'pick_up' or not _identifier(self.kind):
            raise ValueError('Unsupported action intent')
        if self.selection not in ('nearest_reachable', 'specific'):
            raise ValueError('Unsupported target selection')
        if ((self.selection == 'specific') != (self.target_id is not None)
                or self.target_id is not None and not _identifier(self.target_id)):
            raise ValueError('Invalid target selector')

    def as_dict(self):
        target = {'kind': self.kind, 'selection': self.selection}
        if self.target_id is not None:
            target['id'] = self.target_id
        return {'action': self.action, 'target': target}


def parse_intent(payload) -> ActionIntent:
    """Strict structured input: unknown fields and guessed addresses are rejected."""
    if type(payload) is not dict or set(payload) != {'action', 'target'}:
        raise ValueError('Invalid action intent fields')
    target = payload['target']
    if type(target) is not dict:
        raise ValueError('Invalid target object')
    expected = {'kind', 'selection', 'id'} if target.get('selection') == 'specific' else {'kind', 'selection'}
    if set(target) != expected:
        raise ValueError('Invalid target fields')
    return ActionIntent(payload['action'], target['kind'], target['selection'], target.get('id'))


def decode_intent(text: str) -> ActionIntent:
    if not isinstance(text, str) or len(text) > 2048:
        raise ValueError('Action response exceeds limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate action response key')
            result[key] = value
        return result
    try:
        return parse_intent(json.loads(text, object_pairs_hook=unique))
    except (RecursionError, TypeError) as error:
        raise ValueError('Invalid action response') from error


@dataclass(frozen=True)
class PickupTarget:
    # Opaque tokens issued by the adapter, not process addresses or model input.
    object_id: str
    instance_id: str
    kind: str
    candidate: str
    distance_engine_units: float
    reachable: bool | None = None
    pickup_allowed: bool | None = None

    def __post_init__(self):
        if not all(_identifier(v) for v in (self.object_id, self.instance_id, self.kind, self.candidate)):
            raise ValueError('Invalid pickup capability identifiers')
        if (type(self.distance_engine_units) not in (int, float)
                or not math.isfinite(self.distance_engine_units) or self.distance_engine_units < 0):
            raise ValueError('Invalid pickup target distance')
        if any(v is not None and type(v) is not bool for v in (self.reachable, self.pickup_allowed)):
            raise ValueError('Invalid pickup eligibility')


def eligible_pickups(targets: tuple[PickupTarget, ...], candidates: tuple[str, ...]):
    """Unknown eligibility excludes an object; contradictory catalogs fail closed."""
    if (type(targets) is not tuple or len(targets) > 1024
            or type(candidates) is not tuple
            or any(not isinstance(t, PickupTarget) for t in targets)
            or len({t.object_id for t in targets}) != len(targets)
            or len({t.candidate for t in targets}) != len(targets)):
        raise ValueError('Invalid pickup capability catalog')
    return tuple(t for t in targets if t.reachable is True and t.pickup_allowed is True
                 and t.candidate in candidates)


@dataclass(frozen=True)
class ResolvedAction:
    intent: ActionIntent
    target: PickupTarget


@dataclass(frozen=True)
class Resolution:
    status: str
    action: ResolvedAction | None = None


def resolve_intent(intent: ActionIntent, offered: tuple[PickupTarget, ...],
                   current: tuple[PickupTarget, ...], offered_candidates: tuple[str, ...],
                   current_candidates: tuple[str, ...]) -> Resolution:
    """Select using current distances, confined to capabilities offered for this decision.

    Caller must first validate its control lease and current observation freshness.
    A resolved target is only a proposal, never an assertion of execution.
    """
    initial = eligible_pickups(offered, offered_candidates)
    available = eligible_pickups(current, current_candidates)
    identities = {(t.object_id, t.instance_id, t.kind, t.candidate) for t in initial}
    if not any(t.kind == intent.kind for t in initial):
        return Resolution('intent_not_offered')
    matches = [t for t in available if t.kind == intent.kind
               and (t.object_id, t.instance_id, t.kind, t.candidate) in identities
               and (intent.target_id is None or t.object_id == intent.target_id)]
    if not matches:
        return Resolution('no_suitable_target')
    chosen = min(matches, key=lambda t: (t.distance_engine_units, t.object_id, t.instance_id))
    return Resolution('resolved', ResolvedAction(intent, chosen))


def target_still_valid(action: ResolvedAction, targets, candidates) -> bool:
    """Validate the selected object, without silently substituting another tree."""
    chosen = action.target
    try:
        available = eligible_pickups(targets, candidates)
    except ValueError:
        return False
    return any((t.object_id, t.instance_id, t.kind, t.candidate) ==
               (chosen.object_id, chosen.instance_id, chosen.kind, chosen.candidate)
               for t in available)


def intent_options(targets, candidates):
    """A small affordance list for the model; target details stay in the backend."""
    return [ActionIntent('pick_up', kind).as_dict()
            for kind in sorted({t.kind for t in eligible_pickups(targets, candidates)})]


def target_options(kind: str, targets, candidates, limit: int = 5):
    """Bounded details for a deliberate selection, when a caller requests them."""
    if not _identifier(kind) or type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('Invalid target detail request')
    options = sorted((t for t in eligible_pickups(targets, candidates) if t.kind == kind),
                     key=lambda t: (t.distance_engine_units, t.object_id, t.instance_id))
    return [{'id': t.object_id, 'kind': t.kind,
             'distance_engine_units': round(t.distance_engine_units, 1)} for t in options[:limit]]
