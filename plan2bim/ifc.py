"""The model as an IFC4 file.

Every solid in the file is an extrusion of a plan outline, as in the model itself. A wall with a door
or a window in it is written the way IFC holds it: the wall without the hole, the hole as an opening
that cuts it, and the door or window that fills the opening. What is left after the cut is the solid
of the model. All other elements are written as they are.

Lengths are millimetres and the coordinates are those of the reference drawing.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
import shapely
from shapely.geometry.polygon import orient

from .mesh import category
from .model import Element, Model, Piece, polygons
from .project import Project

SPACE = uuid.UUID("6f1d3c0a-2a53-4a43-9d7b-3f8b1c2e9a10")       # namespace: the same model gives the same ids
CLASS = {"wall": ("IfcWall", "NOTDEFINED"), "slab": ("IfcSlab", "FLOOR"), "roof": ("IfcSlab", "ROOF"),
         "door": ("IfcDoor", "DOOR"), "window": ("IfcWindow", "WINDOW"), "stair": ("IfcStair", "NOTDEFINED"),
         "column": ("IfcColumn", "COLUMN"), "railing": ("IfcRailing", "BALUSTRADE")}
COMMON = {"IfcWall": "Pset_WallCommon", "IfcSlab": "Pset_SlabCommon", "IfcDoor": "Pset_DoorCommon",
          "IfcWindow": "Pset_WindowCommon", "IfcColumn": "Pset_ColumnCommon", "IfcRailing": "Pset_RailingCommon",
          "IfcStair": "Pset_StairCommon"}
COLOUR = {      # category/material: red, green, blue, transparency; the colours of the viewer
    "walls/wall": (0.925, 0.914, 0.886, 0.0), "floors/plate": (0.812, 0.796, 0.761, 0.0),
    "roofs/roof": (0.498, 0.529, 0.557, 0.0), "doors/frame": (0.541, 0.431, 0.310, 0.0),
    "doors/leaf": (0.725, 0.553, 0.369, 0.0), "windows/frame": (0.984, 0.984, 0.984, 0.0),
    "windows/glass": (0.561, 0.765, 0.910, 0.6), "stairs/stair": (0.863, 0.839, 0.784, 0.0),
    "stairs/rail": (0.278, 0.314, 0.353, 0.0), "columns/column": (0.925, 0.914, 0.886, 0.0),
    "balconies/balcony": (0.812, 0.796, 0.761, 0.0), "balconies/rail": (0.278, 0.314, 0.353, 0.0),
}
REACH = 1.0             # mm; a hole reaches this far past the faces of its wall, so no face of one lies in a face of the other
GRID = 0.01             # mm; every coordinate in the file is a multiple of this


class Writer:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.f = f = ifcopenshell.file(schema="IFC4")
        self.points: dict[float, object] = {}
        self.styles: dict[str, object] = {}
        self.up = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
        units = f.create_entity("IfcUnitAssignment", Units=[
            f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE"),
            f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
            f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
            f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")])
        context = f.create_entity("IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3,
                                  Precision=0.01, WorldCoordinateSystem=self.axes(0.0))
        self.body = f.create_entity("IfcGeometricRepresentationSubContext", ContextIdentifier="Body", ContextType="Model",
                                    ParentContext=context, TargetView="MODEL_VIEW")
        self.root = f.create_entity("IfcProject", GlobalId=self.guid("project"), Name=project.name,
                                    RepresentationContexts=[context], UnitsInContext=units)

    def guid(self, *key: object) -> str:
        return ifcopenshell.guid.compress(uuid.uuid5(SPACE, "/".join(str(k) for k in (self.project.name, *key))).hex)

    def axes(self, z: float):
        """A placement with the axes of the drawing, `z` above its parent."""
        if z not in self.points:
            at = self.f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, float(z)))
            self.points[z] = self.f.create_entity("IfcAxis2Placement3D", Location=at)
        return self.points[z]

    def placement(self, parent=None, z: float = 0.0):
        return self.f.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=self.axes(z))

    def curve(self, ring):
        pts = [self.f.create_entity("IfcCartesianPoint", Coordinates=(round(x, 2), round(y, 2))) for x, y in ring.coords[:-1]]
        return self.f.create_entity("IfcPolyline", Points=[*pts, pts[0]])

    def solids(self, piece: Piece, floor: float, style: str | None) -> list:
        """The piece as extrusions. Its outline is moved onto the grid of the file first, so that what is
        written is the outline of an area still: plain rounding can make an outline cross itself."""
        items = []
        on_grid = shapely.set_precision(piece.poly, GRID)
        for part in getattr(on_grid, "geoms", [on_grid]):
            if part.geom_type != "Polygon" or part.is_empty:
                continue
            poly = orient(part, 1.0)
            outer = self.curve(poly.exterior)
            if poly.interiors:
                area = self.f.create_entity("IfcArbitraryProfileDefWithVoids", ProfileType="AREA", OuterCurve=outer,
                                            InnerCurves=[self.curve(r) for r in poly.interiors])
            else:
                area = self.f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=outer)
            item = self.f.create_entity("IfcExtrudedAreaSolid", SweptArea=area, Position=self.axes(round(piece.z0 - floor, 2)),
                                        ExtrudedDirection=self.up, Depth=round(piece.z1 - piece.z0, 2))
            if style is not None:
                self.f.create_entity("IfcStyledItem", Item=item, Styles=[self.style(style)])
            items.append(item)
        return items

    def style(self, key: str):
        if key not in self.styles:
            r, g, b, clear = COLOUR.get(key, (0.8, 0.8, 0.8, 0.0))
            look = self.f.create_entity("IfcSurfaceStyleRendering", Transparency=clear, ReflectanceMethod="NOTDEFINED",
                                        SurfaceColour=self.f.create_entity("IfcColourRgb", Red=r, Green=g, Blue=b))
            self.styles[key] = self.f.create_entity("IfcSurfaceStyle", Name=key, Side="BOTH", Styles=[look])
        return self.styles[key]

    def shape(self, items: list):
        rep = self.f.create_entity("IfcShapeRepresentation", ContextOfItems=self.body, RepresentationIdentifier="Body",
                                   RepresentationType="SweptSolid", Items=items)
        return self.f.create_entity("IfcProductDefinitionShape", Representations=[rep])

    def relate(self, kind: str, key: str, **attributes):
        return self.f.create_entity(kind, GlobalId=self.guid(kind, key), **attributes)


def _whole(e: Element) -> tuple[list[Piece], list[Piece]]:
    """A wall with a hole in it, as IFC holds it: the wall without the hole, and the hole to cut out of it.

    The model holds what is left of the wall: the plan of the hole, below the hole and above it. Where a
    wall is not like that, it is written as it is, and the hole beside it cuts nothing.
    """
    void = e.void
    assert void is not None
    plans = polygons(void.poly)
    hole = [Piece(p, void.z0, void.z1, "void") for p in plans]
    if not e.pieces or not plans:
        return e.pieces, hole
    z0, z1 = min(void.z0, *(p.z0 for p in e.pieces)), max(void.z1, *(p.z1 for p in e.pieces))
    left = sum(p.poly.area * (p.z1 - p.z0) for p in e.pieces)
    whole = sum(p.area for p in plans) * (z1 - z0)
    if abs(whole - left - sum(p.poly.area for p in hole) * (void.z1 - void.z0)) > 1e-6 * whole:
        return e.pieces, hole
    reach = [Piece(p.poly.buffer(REACH, join_style="mitre"), p.z0, p.z1, "void") for p in hole]
    return [Piece(p, z0, z1, e.pieces[0].material) for p in plans], reach


def write(path: Path, project: Project, model: Model) -> int:
    """Write the model to `path` and return the number of building elements in the file."""
    w = Writer(project)
    f = w.f
    site = f.create_entity("IfcSite", GlobalId=w.guid("site"), Name="Site", ObjectPlacement=w.placement(),
                           CompositionType="ELEMENT")
    building = f.create_entity("IfcBuilding", GlobalId=w.guid("building"), Name=project.name,
                               ObjectPlacement=w.placement(site.ObjectPlacement), CompositionType="ELEMENT")
    w.relate("IfcRelAggregates", "site", RelatingObject=w.root, RelatedObjects=[site])
    w.relate("IfcRelAggregates", "building", RelatingObject=site, RelatedObjects=[building])
    storeys = {}
    for spec in sorted(project.levels, key=lambda lv: lv.elevation):
        storeys[spec.key] = f.create_entity(
            "IfcBuildingStorey", GlobalId=w.guid("storey", spec.key), Name=spec.name, CompositionType="ELEMENT",
            ObjectPlacement=w.placement(building.ObjectPlacement, spec.elevation), Elevation=float(spec.elevation))
    w.relate("IfcRelAggregates", "storeys", RelatingObject=building, RelatedObjects=list(storeys.values()))

    def product(kind: str, key: str, level: str, pieces: list[Piece], style, **attributes):
        floor = project.level(level).elevation
        items = [item for p in pieces for item in w.solids(p, floor, style(p) if style else None)]
        return f.create_entity(kind, GlobalId=w.guid(kind, key), ObjectPlacement=w.placement(storeys[level].ObjectPlacement),
                               Representation=w.shape(items) if items else None, **attributes)

    made: dict[int, object] = {}
    openings: dict[int, object] = {}
    contained: dict[str, list] = {key: [] for key in storeys}
    common: dict[tuple[str, bool], list] = {}
    for i, e in enumerate(model.elements):
        kind, predefined = CLASS[e.kind]
        parts, hole = _whole(e) if e.void is not None else (e.pieces, [])
        made[i] = thing = product(kind, f"{i}/{e.name}", e.level, parts, lambda p, e=e: f"{category(e, p)}/{p.material}",
                                  Name=e.name, Description=e.note or None, PredefinedType=predefined)
        contained[e.level].append(thing)
        common.setdefault((kind, e.exterior), []).append(thing)
        if hole:
            openings[i] = cut = product("IfcOpeningElement", f"{i}/{e.name}", e.level, hole, None,
                                        Name=f"Opening in {e.name}", PredefinedType="OPENING")
            w.relate("IfcRelVoidsElement", f"{i}", RelatingBuildingElement=thing, RelatedOpeningElement=cut)
    for i, e in enumerate(model.elements):
        if e.host in openings:
            w.relate("IfcRelFillsElement", f"{i}", RelatingOpeningElement=openings[e.host], RelatedBuildingElement=made[i])
    for key, things in contained.items():
        if things:
            w.relate("IfcRelContainedInSpatialStructure", key, RelatingStructure=storeys[key], RelatedElements=things)
    for (kind, exterior), things in common.items():
        flag = f.create_entity("IfcPropertySingleValue", Name="IsExternal", NominalValue=f.create_entity("IfcBoolean", exterior))
        pset = w.relate("IfcPropertySet", f"{kind}/{exterior}", Name=COMMON[kind], HasProperties=[flag])
        w.relate("IfcRelDefinesByProperties", f"{kind}/{exterior}", RelatedObjects=things, RelatingPropertyDefinition=pset)

    rooms: dict[str, list] = {key: [] for key in storeys}
    for i, s in enumerate(model.spaces):
        parts = [Piece(p, s.z0, s.z1, "space") for p in polygons(s.poly)]
        rooms[s.level].append(product("IfcSpace", f"{i}/{s.number}", s.level, parts, None, Name=s.number or None,
                                      LongName=s.name or None, CompositionType="ELEMENT", PredefinedType="INTERNAL"))
    for key, spaces in rooms.items():
        if spaces:
            w.relate("IfcRelAggregates", f"spaces/{key}", RelatingObject=storeys[key], RelatedObjects=spaces)

    path.parent.mkdir(parents=True, exist_ok=True)
    f.write(str(path))
    return len(made)
