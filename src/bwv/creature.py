"""Sourced creature form and abilities; never inferred from an object handle."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal
from .profile_names import ProfileNames

Source = Literal["engine", "player_report", "reference"]


@dataclass(frozen=True)
class CreatureTrait:
    name: str
    value: float | str
    source: Source
    unit: str | None = None
    reference: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not 0 < len(self.name) <= 64:
            raise ValueError("Invalid creature trait name")
        if self.source not in ("engine", "player_report", "reference"):
            raise ValueError("Invalid creature trait source")
        if isinstance(self.value, str):
            if not 0 < len(self.value) <= 240:
                raise ValueError("Invalid creature trait description")
        elif type(self.value) not in (int, float) or not math.isfinite(self.value):
            raise ValueError("Invalid creature trait value")
        for value in (self.unit, self.reference):
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 240):
                raise ValueError("Invalid creature trait metadata")
        if self.source == "reference" and self.reference is None:
            raise ValueError("Reference traits require a source reference")


@dataclass(frozen=True)
class CreatureContext:
    species: str | None = None
    species_source: Literal["engine", "player_report", "unknown"] = "unknown"
    species_traits: tuple[CreatureTrait, ...] = ()
    individual_traits: tuple[CreatureTrait, ...] = ()
    names: ProfileNames = ProfileNames()
    alignment: float | None = None
    god_alignment: float | None = None
    trait_read_errors: int = 0

    def __post_init__(self):
        for value in (self.alignment,self.god_alignment):
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not -1<=value<=1):
                raise ValueError('Invalid alignment')
        if not isinstance(self.names, ProfileNames):
            raise ValueError('Invalid profile names')
        if self.species is not None and (not isinstance(self.species, str) or not 0 < len(self.species) <= 64):
            raise ValueError("Invalid creature species")
        if self.species_source not in ("engine", "player_report", "unknown"):
            raise ValueError("Invalid species source")
        if (self.species is None) != (self.species_source == "unknown"):
            raise ValueError("Species and its source must agree")
        for traits in (self.species_traits, self.individual_traits):
            if type(traits) is not tuple or len(traits) > 16 or any(not isinstance(t, CreatureTrait) for t in traits):
                raise ValueError("Invalid creature trait collection")
            if len({t.name for t in traits}) != len(traits):
                raise ValueError("Duplicate creature trait")
