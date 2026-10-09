"""Player-hand contents, independent of the two surrounding-world views."""
from dataclasses import dataclass, asdict

from .object_details import public_object
from .spells import SPELLS


@dataclass(frozen=True)
class GodHandContext:
    contents: str | None = None
    held_object: dict | None = None
    activated_miracle: str | None = None
    detail_read_errors: int = 0


def hand_contents(value):
    """Expose only present facts; an unavailable reading is not an empty hand."""
    if isinstance(value, GodHandContext):value = asdict(value)
    if not isinstance(value, dict):return {}
    state = value.get('contents')
    if state not in ('empty', 'holding_object', 'activated_miracle'):return {}
    result = {'contents': state}
    if state == 'holding_object' and isinstance(value.get('held_object'), dict):
        result['held_object'] = public_object(value['held_object'])
    if state == 'activated_miracle' and value.get('activated_miracle') in SPELLS.values():
        result['activated_miracle'] = value['activated_miracle']
    return result
