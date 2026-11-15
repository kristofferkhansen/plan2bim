"""Give the plan its third dimension.

A plan holds no heights, so every height here comes from the project file. The rules:

- Wall mass stands from the floor to the underside of the plate above. Wall mass outside the net
  outline, that is the exterior walls, starts one plate thickness lower and so covers the edge of
  its own floor plate.
- The floor plate of a storey fills the net outline, the thresholds of its exterior doors and the
  closed spaces that have no room polygon. A stair or a lift shaft that comes up from the storey
  below leaves a hole in it.
- The floor plate of the storey above closes a storey. Where no storey stands above, a roof plate
  of the same thickness closes it at the same height.
- A door leaves the wall above its head. A window leaves the wall below its sill and above its head.
"""
from __future__ import annotations

import math

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .classify import Classified, Frame, Leaf
from .level import Level
from .model import Element, Model, Note, Piece, Space, pieces, polygons
from .project import Project
from .stairs import Stair

SASH = 50.0             # height of the bottom and top member of a window, as modelled
SLIVER = 3.0            # a strip of wall thinner than twice this is not split off at the net outline
ROOF_MIN = 150.0        # a roof strip narrower than twice this is a mismatch between two outlines, not a roof
JOINT = 5.0             # floor areas this close to each other are one floor
HAIR = 0.5              # a strip of wall thinner than twice this does not part a floor
FLOORED = ("room", "space", "stair", "swing", "gap")    # kinds of plan cell that people stand on
MATERIAL = {"tread": "stair", "landing": "stair", "post": "rail", "rail": "rail"}


def _thin(geom: BaseGeometry) -> bool:
    return geom.is_empty or geom.buffer(-SLIVER).is_empty


def _split(mass: BaseGeometry, inner: BaseGeometry) -> tuple[BaseGeometry | None, BaseGeometry | None]:
    """The part of a piece of wall outside the net outline, and the part inside it."""
    inside, outside = mass.intersection(inner), mass.difference(inner)
    if _thin(outside):
        return None, mass
    if _thin(inside):
        return mass, None
    return outside, inside


def _closed_leaf(lf: Leaf, rough: Polygon) -> BaseGeometry:
    """The leaf, shut: it lies in its opening. A leaf drawn as one line has no side, so the other side is tried too."""
    shut = [Frame(lf.hinge, lf.u).rect(0.0, lf.r, min(0.0, side), max(0.0, side))
            for side in (lf.body * lf.thick, -lf.body * lf.thick)]
    for leaf in shut:
        inside = leaf.intersection(rough)
        if inside.area >= 0.6 * leaf.area:
            return inside
    return shut[0]


def stair_name(project: Project, st: Stair) -> str:
    """The name of the element that holds a stair: how a check finds the stair in the model."""
    return f"Stair {st.name} from {project.level(st.level).name}, {st.risers} risers"


def assemble(project: Project, levels: dict[str, Level], cls: dict[str, Classified], stairs: list[Stair]) -> Model:
    model = Model(project.name)
    h = project.heights
    shafts_below: BaseGeometry = Polygon()
    for spec in sorted(project.levels, key=lambda lv: lv.elevation):
        key, level, c = spec.key, levels[spec.key], cls[spec.key]
        faces = c.arr.faces
        floor_top = spec.elevation
        under = floor_top - h.plate                 # underside of this storey's floor plate
        top = project.top_of(key) - h.plate         # underside of the plate that closes this storey
        head, sill = floor_top + h.door_head, floor_top + h.window_sill
        window_head = floor_top + h.window_head
        inner, gross = level.inner_all, level.gross_all
        deep: list[BaseGeometry] = []               # plan of everything that starts at `under`
        thresholds: list[BaseGeometry] = []

        for f, mass in c.solids():
            outside, inside = _split(mass, inner)
            parts = pieces(outside, under, top, "wall") + pieces(inside, floor_top, top, "wall")
            if parts:
                model.add(Element("wall", key, f"Wall {key}-{f.id}", parts, exterior=outside is not None, note=f.why))
            if outside is not None:
                deep.append(outside)

        for d in c.doors:
            host = model.add(Element("wall", key, f"Wall over door {key}-{d.id}", pieces(d.rough, head, top, "wall"),
                                     void=Piece(d.rough, floor_top, head, "void"), exterior=d.exterior))
            parts = [p for j in d.jambs for p in pieces(faces[j].poly, floor_top, head, "frame")]
            parts += [p for lf in d.leaves for p in pieces(_closed_leaf(lf, d.rough), floor_top, head, "leaf")]
            along = d.frame.extent(d.rough)
            model.add(Element("door", key, f"Door {key}-{d.id}", parts, host=host, exterior=d.exterior,
                              note="; ".join(d.notes), size=(along[1] - along[0], h.door_head)))
            thresholds.append(d.rough)

        for w in c.windows:
            low = under if w.exterior else floor_top
            strips = set(w.strips)
            parts = []
            for j in w.faces:
                if j in strips:
                    parts += pieces(faces[j].poly, sill, sill + SASH, "frame")
                    parts += pieces(faces[j].poly, sill + SASH, window_head - SASH, "glass")
                    parts += pieces(faces[j].poly, window_head - SASH, window_head, "frame")
                else:
                    parts += pieces(faces[j].poly, sill, window_head, "frame")
            joinery = unary_union([faces[j].poly for j in w.faces])
            host, most = -1, -1.0
            bays = polygons(w.bay)
            for n, bay in enumerate(bays, start=1):
                name = f"Wall at window {key}-{w.id}" + (f", part {n}" if len(bays) > 1 else "")
                index = model.add(Element("wall", key, name, pieces(bay, low, sill, "wall") + pieces(bay, window_head, top, "wall"),
                                          void=Piece(bay, sill, window_head, "void"), exterior=w.exterior))
                held = bay.intersection(joinery).area
                if held > most:                        # the window fills the part of the bay that holds its joinery
                    host, most = index, held
            model.add(Element("window", key, f"Window {key}-{w.id}", parts, host=host, exterior=w.exterior,
                              note="; ".join(w.notes), size=(w.s[1] - w.s[0], h.window_head - h.window_sill)))
            if w.exterior:
                deep.append(w.bay)

        for i, gap in enumerate(c.openings):
            host = model.add(Element("wall", key, f"Wall over opening {key}-{i}", pieces(gap, head, top, "wall"),
                                     void=Piece(gap, floor_top, head, "void")))
            thresholds.append(gap)
            sliding = [p for p in c.panels if p.intersection(gap).area > 0.5 * p.area]
            if sliding:
                clear = math.dist(gap.exterior.coords[1], gap.exterior.coords[2])    # `find_openings`: wall end to wall end
                model.add(Element("door", key, f"Sliding doors {key}-{i}",
                                  [q for p in sliding for q in pieces(p, floor_top, head, "leaf")], host=host,
                                  size=(clear, h.door_head)))
        screens = [p for p in c.panels if not any(p.intersection(gap).area > 0.5 * p.area for gap in c.openings)]
        if screens:                                    # panels that stand in no opening of a wall
            model.add(Element("door", key, f"Screens {key}", [q for p in screens for q in pieces(p, floor_top, head, "leaf")]))

        standing = unary_union([mass for _, mass in c.solids()])
        for i, col in enumerate(c.columns):            # a column in a wall: the wall keeps what is drawn as wall
            model.add(Element("column", key, f"Column {key}-{i}", pieces(col.difference(standing), floor_top, top, "column")))

        for i, b in enumerate(c.balconies):
            model.add(Element("slab", key, f"Balcony {key}-{i}", pieces(b.floor, under, floor_top, "balcony"), exterior=True))
            model.add(Element("railing", key, f"Balustrade {key}-{i}", pieces(b.rail, floor_top, floor_top + h.balustrade, "rail"),
                              exterior=True))

        # floor plate: the net outline is not always drawn out to the wall, so the cells themselves count too
        walked = [f.poly for f in faces if f.kind in FLOORED and f.in_gross]
        floor = unary_union([inner, *thresholds, *walked])
        floor = floor.buffer(JOINT, join_style="mitre").buffer(-JOINT, join_style="mitre").intersection(gross)
        mass = unary_union(deep).buffer(-HAIR, join_style="mitre").buffer(HAIR, join_style="mitre")
        floor = floor.difference(mass)
        holes = [(f"stair {st.name} from {project.level(st.level).name}", st.hole) for st in stairs
                 if (above := project.above(st.level)) is not None and above.key == key]
        shafts = [r for r in level.rooms if r.name.strip().lower() in project.input.shafts]
        holes += [(f"{r.number} {r.name}, which comes up from the storey below", r.poly) for r in shafts
                  if r.poly.intersection(shafts_below).area > 0.5 * r.poly.area]
        holes += [("cell marked with a cross", p) for p in c.shafts]
        if holes:
            model.holes[key] = holes
            floor = floor.difference(model.open_floor(key))
        floor = floor.buffer(-HAIR, join_style="mitre").buffer(HAIR, join_style="mitre")
        model.add(Element("slab", key, f"Floor {spec.name}", pieces(floor, under, floor_top, "plate")))
        shafts_below = unary_union([r.poly for r in shafts])    # a shaft goes on where the storey right under it has one

        # what closes the storey where no storey stands above
        above = project.above(key)
        if above is None:
            roof = gross
        else:
            covered = [levels[above.key].gross_all, *(b.floor for b in cls[above.key].balconies)]
            bare = gross.difference(unary_union(covered))
            roof = bare.buffer(-ROOF_MIN, join_style="mitre").buffer(ROOF_MIN, join_style="mitre").intersection(gross)
        model.add(Element("roof", key, f"Roof over {spec.name}", pieces(roof, top, top + h.plate, "roof"), exterior=True))

        model.spaces += [Space(key, r.number, r.name, r.poly, floor_top, top) for r in level.rooms]
        model.notes += [Note(f"{spec.name}: {n}", key) for n in level.notes]
        model.notes += [Note(f"{spec.name}: {n.text}", key, n.at) for n in c.notes]

    for st in stairs:
        upper = project.above(st.level)
        ceiling = upper.elevation - h.plate if upper else float("inf")   # underside of the plate the stair comes up through
        parts = []
        for q in st.prisms:
            parts += pieces(q.poly, q.z0, min(q.z1, ceiling), MATERIAL[q.part])
            if q.z1 > ceiling:                      # through the plate: only inside the hole left for the stair
                parts += pieces(q.poly.intersection(st.hole), max(q.z0, ceiling), q.z1, MATERIAL[q.part])
        model.add(Element("stair", st.level, stair_name(project, st), parts))
        model.notes += [Note(f"stair {st.name} from {st.level}: {n}", st.level) for n in st.notes]
    return model
