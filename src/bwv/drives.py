"""Creature motivation labels; these are not physical-condition measurements."""
from dataclasses import dataclass
import math

# Desire interface indices; enum names independently mapped to plain language.
DRIVES = ('impress', 'compassion', 'anger', 'play', 'hunger', 'fear', 'curiosity',
          'defecate', 'tiredness', 'idle_with_player', 'wander', 'vomit',
          'build_home', 'bring_objects_home', 'water', 'restore_health',
          'make_friends', 'get_player_attention', 'manifest_state', 'get_warmer',
          'get_colder', 'scratch', 'run_from_player', 'rest', 'obey_player',
          'illness', 'obey_creature', 'sadness', 'stay_near_home',
          'express_opinion_of_player', 'play_with_player', 'express_opinion_of_creature',
          'teach_friend', 'follow_player_desire', 'get_high', 'stay_at_home',
          'mental_illness', 'miss_friend', 'look_around', 'steal')


@dataclass(frozen=True)
class CreatureDrive:
    name: str
    value: float

    def __post_init__(self):
        if self.name not in DRIVES or type(self.value) not in (int,float) or not math.isfinite(self.value) or not 0 <= self.value <= 1000:
            raise ValueError('Invalid creature motivation')


def compact_drives(drives, limit=6):
    # Hunger already has a dedicated legacy compact field. Rounded, positive
    # motivations only; strongest first with stable ties. Full debug retains all.
    active = sorted((d for d in drives if d.name != 'hunger' and round(d.value,2)>0),
                    key=lambda d:(-round(d.value,2), d.name))[:limit]
    return {d.name:round(d.value,2) for d in active}
