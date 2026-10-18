"""Selected-creature held-object context; never the god player's hand."""
from dataclasses import dataclass
import math
from typing import Literal
from .spells import SPELLS
from .drives import CreatureDrive
from .held_objects import HELD_KINDS
from .object_types import OBJECT_SUBTYPES


@dataclass(frozen=True)
class HoldingContext:
    holding: bool | None = None
    item_id: str | None = None
    kind: str | None = None
    # This is an active engine plan, not proof its action completed.
    active_plan: Literal['eat', 'take_from_player', 'examine_held_object', 'plant_tree'] | None = None
    # Engine motivation units, NOT a normalized physiological hunger percentage.
    hunger_desire: float | None = None
    # A miracle-related engine plan, not a completed or successful cast.
    miracle_plan: str | None = None
    drives: tuple[CreatureDrive, ...] = ()
    object_type: str | None = None
    object_id: str | None = None
    details: dict | None = None
    detail_read_errors: int = 0
    species: str | None = None
    person: dict | None = None

    def __post_init__(self):
        from .object_details import details
        from .animal_types import ANIMAL_TYPES
        from .nearby_people import person_details
        if self.species is not None and (self.kind!='animal' or self.species not in ANIMAL_TYPES.values()):
            raise ValueError('Invalid held species')
        if self.person is not None and (self.kind!='villager' or person_details(self.person)!=self.person):
            raise ValueError('Invalid held person details')
        if self.details is not None and details(self.details)!=self.details:raise ValueError('Invalid held metadata')
        if self.object_id is not None and (self.holding is not True or not isinstance(self.object_id,str) or len(self.object_id)!=24):
            raise ValueError('Invalid held world identity')
        if (type(self.drives) is not tuple or len(self.drives)>40
                or any(not isinstance(d,CreatureDrive) for d in self.drives)
                or len({d.name for d in self.drives}) != len(self.drives)):
            raise ValueError('Invalid creature motivations')
        if self.holding is not None and type(self.holding) is not bool:
            raise ValueError('Invalid creature holding state')
        if self.item_id is not None and (not isinstance(self.item_id, str) or not 0 < len(self.item_id) <= 96):
            raise ValueError('Invalid held-object identity')
        if (self.holding is True) != (self.item_id is not None):
            raise ValueError('Held-object identity and holding state disagree')
        if self.kind not in (None, *HELD_KINDS) or (self.kind is not None and self.holding is not True):
            raise ValueError('Invalid held-object kind')
        if self.object_type is not None and (self.object_type not in OBJECT_SUBTYPES
                                            or self.holding is not True):
            raise ValueError('Invalid held-object detail')
        if self.active_plan not in (None, 'eat', 'take_from_player', 'examine_held_object', 'plant_tree'):
            raise ValueError('Invalid creature plan')
        if self.miracle_plan is not None and self.miracle_plan not in SPELLS.values():
            raise ValueError('Invalid creature miracle plan')
        if self.hunger_desire is not None and (type(self.hunger_desire) not in (int, float)
                or not math.isfinite(self.hunger_desire) or not 0 <= self.hunger_desire <= 1000):
            raise ValueError('Invalid hunger desire')
