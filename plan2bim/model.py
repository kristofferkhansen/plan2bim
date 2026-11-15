"""The building as a list of elements. Every piece of every element is a vertical prism."""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

MIN_AREA = 100.0        # mm2; a smaller scrap of plan is not worth a solid
HAIR = 0.5              # mm; a strip thinner than twice this is an accident of the drawing, not a thing
MITRE = 10.0            # corners keep their point down to an angle of about 12 degrees


@dataclass
class Piece:
    poly: Polygon
    z0: float
    z1: float
    material: str       # wall | plate | roof | frame | leaf | glass | stair | rail | column | balcony


@dataclass
class Element:
    kind: str           # wall | slab | roof | door | window | stair | column | railing
    level: str
    name: str
    pieces: list[Piece]
    void: Piece | None = None       # wall that hosts a door or window: the hole in it. `pieces` is what is left.
    host: int = -1                  # door, window: index of the wall it sits in
    exterior: bool = False
    note: str = ""


@dataclass
class Space:
    level: str
    number: str
    name: str
    poly: Polygon
    z0: float
    z1: float


@dataclass
class Model:
    name: str
    elements: list[Element] = field(default_factory=list)
    spaces: list[Space] = field(default_factory=list)
    # per storey: what its floor plate leaves open, and why
    holes: dict[str, list[tuple[str, BaseGeometry]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def open_floor(self, key: str) -> BaseGeometry:
        """Plan of what the floor plate of a storey leaves open."""
        return unary_union([plan for _, plan in self.holes.get(key, [])])

    def add(self, e: Element) -> int:
        """Index of the element in the model, or -1 for an element with nothing in it, which is not kept."""
        if not e.pieces and e.void is None:
            return -1
        self.elements.append(e)
        return len(self.elements) - 1


def polygons(geom: BaseGeometry | None) -> list[Polygon]:
    """The polygons of any geometry, without scraps, needles and spikes.

    Where one outline is cut out of another and their edges should coincide but miss by a fraction of a
    millimetre, a needle or a spike with no width is left. Nothing thinner than twice `HAIR` is kept.
    """
    if geom is None or geom.is_empty:
        return []
    clean = geom.buffer(-HAIR, join_style="mitre", mitre_limit=MITRE).buffer(HAIR, join_style="mitre", mitre_limit=MITRE)
    return [p for p in getattr(clean, "geoms", [clean]) if p.geom_type == "Polygon" and p.area >= MIN_AREA]


def pieces(geom: BaseGeometry | None, z0: float, z1: float, material: str) -> list[Piece]:
    return [Piece(p, z0, z1, material) for p in polygons(geom)] if z1 - z0 > 0.5 else []
