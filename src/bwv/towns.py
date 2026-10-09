"""Town allegiance observations, separate from personal ownership or alignment."""
from dataclasses import asdict, dataclass
import math

TRIBES = ('celtic','african','aztec','japanese','indian','egyptian','greek','norse','tibetan')


@dataclass(frozen=True)
class TownBelief:
    god_id: str
    relation: str
    belief_engine_units: float


@dataclass(frozen=True)
class TownContext:
    town_id: str
    tribe: str | None
    control: str
    controlling_god_id: str | None
    beliefs: tuple[TownBelief, ...]
    neutral_belief_engine_units: float
    player_belief_cap_engine_units: float
    conversion_reference_engine_units: float | None = None
    additional_belief_to_match_reference_engine_units: float | None = None

    def __post_init__(self):
        if (not self.town_id or self.tribe not in (None,*TRIBES)
                or self.control not in ('player_god','other_god','unclaimed','unknown')
                or len(self.beliefs)>8):
            raise ValueError('Invalid town context')
        numbers = [b.belief_engine_units for b in self.beliefs]
        numbers += [self.neutral_belief_engine_units,self.player_belief_cap_engine_units,
                    self.conversion_reference_engine_units,self.additional_belief_to_match_reference_engine_units]
        if any(v is not None and (not math.isfinite(v) or not 0<=v<=1e9) for v in numbers):
            raise ValueError('Invalid town belief')


def town_details(town):
    """Model-facing fields only; belief entries cover the player and town owner."""
    return {k:v for k,v in asdict(town).items() if v is not None}


def compact_towns(towns):
    result=[]
    for town in towns[:4]:
        detail=town_details(town)
        # Caps and neutral mechanics belong in inspection, not every tick.
        detail.pop('player_belief_cap_engine_units',None)
        detail.pop('neutral_belief_engine_units',None)
        for b in detail['beliefs']:b['belief_engine_units']=round(b['belief_engine_units'],2)
        for key in ('conversion_reference_engine_units','additional_belief_to_match_reference_engine_units'):
            if key in detail:detail[key]=round(detail[key],2)
        result.append(detail)
    return result
