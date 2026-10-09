"""Nearby known objects; membership is not a claim of visual line of sight."""
from dataclasses import dataclass
import math
from .towns import TownContext

SUMMARY_RADIUS = 120


@dataclass(frozen=True)
class NearbyObject:
    object_id: str
    kind: str
    distance_engine_units: float
    town_id: str | None = None
    on_fire: bool | None = None
    distance_to_god_hand_engine_units: float | None = None
    position: dict | None = None
    species: str | None = None
    physical_traits: dict | None = None
    person: dict | None = None
    details: dict | None = None
    lookup_handle: int | None = None
    portal: dict | None = None
    owner_god_id: str | None = None
    owner_link: str | None = None

    def __post_init__(self):
        from .island import god_id
        if self.owner_god_id is not None and (self.kind != 'creature' or not god_id(self.owner_god_id)):
            raise ValueError('Invalid creature owner')
        if self.owner_link is not None and (not self.owner_god_id or
                self.owner_link not in ('registered_creature','engine_only')):
            raise ValueError('Invalid creature owner registration')
        from .nearby_creatures import physical_traits
        from .nearby_people import person_details
        from .object_details import details
        from .vortex import portal_details
        if self.portal is not None and (self.kind not in ('vortex', 'teleport')
                or portal_details(self.portal) != self.portal):
            raise ValueError('Invalid portal metadata')
        if self.details is not None and details(self.details) != self.details:
            raise ValueError('Invalid object details')
        if self.person is not None and (self.kind != 'villager'
                or not isinstance(self.person, dict) or person_details(self.person) != self.person):
            raise ValueError('Invalid nearby person metadata')
        if self.physical_traits is not None and (self.kind != 'creature'
                or not isinstance(self.physical_traits,dict)
                or physical_traits(self.physical_traits) != self.physical_traits):
            raise ValueError('Invalid nearby creature physical traits')
        if self.species is not None and (self.kind not in ('creature','animal') or not isinstance(self.species,str)
                or not 0<len(self.species)<=64):raise ValueError('Invalid nearby creature species')
        if not self.object_id or not self.kind or not math.isfinite(self.distance_engine_units) or self.distance_engine_units < 0:
            raise ValueError('Invalid nearby object')
        if self.on_fire is not None and type(self.on_fire) is not bool:
            raise ValueError('Invalid nearby fire state')
        if self.distance_to_god_hand_engine_units is not None and (
                not math.isfinite(self.distance_to_god_hand_engine_units) or self.distance_to_god_hand_engine_units<0):
            raise ValueError('Invalid hand distance')


@dataclass(frozen=True)
class SurroundingsContext:
    available: bool = False
    nearby_known_objects: tuple[NearbyObject, ...] = ()
    radius_engine_units: float = SUMMARY_RADIUS
    scanned_beliefs: int = 0
    matching_objects: int = 0
    visibility_verified: bool = False
    towns: tuple[TownContext, ...] = ()
    town_read_errors: int = 0
    fire_read_errors: int = 0
    god_hand_available: bool = False
    god_hand_objects: tuple[NearbyObject, ...] = ()
    hand_read_errors: int = 0
    creature_read_errors: int = 0
    person_read_errors: int = 0
    known_objects: tuple[NearbyObject, ...] = ()
    detail_read_errors: int = 0
    portal_read_errors: int = 0

    def __post_init__(self):
        if (type(self.nearby_known_objects) is not tuple or len(self.nearby_known_objects) > 1024
                or any(not isinstance(o, NearbyObject) for o in self.nearby_known_objects)
                or (not self.available and self.nearby_known_objects)
                or type(self.god_hand_objects) is not tuple or len(self.god_hand_objects)>1024
                or any(not isinstance(o,NearbyObject) for o in self.god_hand_objects)
                or (self.god_hand_objects and not self.god_hand_available)
                or len(self.towns)>8 or any(not isinstance(t,TownContext) for t in self.towns)):
            raise ValueError('Invalid surroundings context')
        if (type(self.known_objects) is not tuple or len(self.known_objects)>1024
                or any(not isinstance(o,NearbyObject) for o in self.known_objects)):
            raise ValueError('Invalid known objects')
