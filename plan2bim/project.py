"""Project file: which drawings, which elevations, and every assumption the drawings do not contain.

The same definition is read from TOML, for a person, and from JSON, for a program. `parse` checks all of
it and reports every problem at once. `example/example.toml` shows all fields.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .errors import InputError, ProjectError

SCHEMA = 1                                          # version of the project file that this code reads
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0}        # drawing unit: millimetres in one unit
SIDES = ("N", "S", "E", "W")
KINDS = ("dogleg", "straight", "spiral")
KEY = re.compile(r"[A-Za-z0-9_.-]+")                # a storey key is part of file names and of block ids


@dataclass(frozen=True)
class LevelSpec:
    key: str            # short id used in file names and reports
    name: str           # storey name written to the model
    drawings: tuple[Path, ...]      # DWG or DXF: one drawing, or several that share their coordinates
    elevation: float    # top of structural floor, mm


@dataclass(frozen=True)
class Heights:
    """Vertical dimensions. A plan drawing holds none of them: all are assumptions."""
    roof: float                 # elevation of the top of the roof plate over the top storey
    plate: float = 300.0        # thickness of floor and roof plates
    door_head: float = 2100.0   # clear height of doors and of openings without a door
    window_sill: float = 900.0
    window_head: float = 2100.0
    landing: float = 200.0      # thickness of stair landings and of the stair under each tread
    balustrade: float = 1100.0  # height of balcony balustrades and stair guards


@dataclass(frozen=True)
class StairSpec:
    """What a plan cannot say about a stair. The treads themselves come from the drawing."""
    name: str
    kind: str                       # "dogleg" | "straight" | "spiral"
    levels: tuple[str, ...]         # storeys from which this stair climbs one storey
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)   # dogleg, straight: the shaft (x0, y0, x1, y1)
    at: tuple[float, float] = (0.0, 0.0)    # spiral: about where its centre is
    entry: str = ""                 # dogleg: side of the shaft with the storey landing (N, S, E or W)
    up: str = ""                    # dogleg: side of the flight that leaves the storey landing upwards;
                                    # straight: direction of climb
    start_deg: float = 0.0          # spiral: direction of the first riser, degrees from +x
    sweep_deg: float = 0.0          # spiral: total turn, positive counter-clockwise
    draw_level: str = ""            # storey whose drawing shows the whole stair (default: the storey itself)


@dataclass(frozen=True)
class Tolerances:
    grid: float = 0.01          # coordinates are rounded to this (mm); AutoCAD noise is ~1e-6
    on_line: float = 0.05       # "lies on a drawn line"
    snap: float = 2.0           # area outlines (BTA/BRA) are traced this loosely over the linework
    close: float = 4.0          # a line end this near another line was meant to touch it (smallest real step: 5)
    arc_step_deg: float = 3.0   # arcs become polylines with this step


@dataclass(frozen=True)
class Input:
    """The convention of the drawings: where the reader finds what.

    The defaults take two conventions of Norwegian drawings for facility management: the area layers
    `Area_NTA` and `Area_BTA` with a label block in each room, and the area layers `862 - Rom NTA` and
    `861 - Etasje BTA` with the room number and the room name as plain texts.
    A layer name can hold `*`, which stands for any text. A layer has one content: the first of `ignore`,
    `rooms`, `gross`, `inner`, `room_number_text`, `room_name_text` and `lines` that names it.
    """
    unit: str = "mm"                                # unit of the drawing coordinates: mm, cm or m
    lines: tuple[str, ...] = ("*",)                 # layers with the building lines
    rooms: tuple[str, ...] = ("Area_NTA", "862 - Rom NTA")     # layers with the room outlines and the room labels
    gross: tuple[str, ...] = ("Area_BTA", "861 - Etasje BTA")  # layers with the gross outline of each building block
    inner: tuple[str, ...] = ("Area_BRA",)          # layers with the inner face of the exterior walls
    ignore: tuple[str, ...] = ()                    # layers that are not read
    room_number: str = "ROOMNUMBER"                 # attribute tags of the room label block
    room_name: str = "ROOMNAME"
    room_wing: str = "WING"
    room_number_text: tuple[str, ...] = ("F29-T1-Nr",)      # layers on which a text in a room outline is its number
    room_name_text: tuple[str, ...] = ("F29-T2-Navn",)      # layers on which a text in a room outline is its name
    shafts: tuple[str, ...] = ("heis", "sjakt")             # room names of lifts and shafts: no floor in them
    cores: tuple[str, ...] = ("heis", "trapp", "sjakt")     # words in the names of rooms that run through all storeys


LAYERS = ("ignore", "rooms", "gross", "inner", "room_number_text", "room_name_text", "lines")


def content_of(inp: Input, layer: str) -> str:
    """What the reader takes from `layer`: one of LAYERS, or "" when it does not read the layer."""
    name = layer.casefold()
    for content in LAYERS:
        for pattern in getattr(inp, content):
            if re.fullmatch(".*".join(re.escape(part) for part in pattern.casefold().split("*")), name):
                return "" if content == "ignore" else content
    return ""


@dataclass(frozen=True)
class Gates:
    """Limits that decide whether a finished build passes. See `verify`."""
    on_line: float = 0.98                       # least share of the cut model outline that lies on a drawn line
    unexplained: float = 0.005                  # largest share of a gross outline that is neither room, wall nor floor
    riser: tuple[float, float] = (150.0, 190.0)     # usual riser, mm; outside it a floor-to-floor height is in doubt


@dataclass(frozen=True)
class Georeference:
    """Where the model frame lies on the map."""
    crs: str                    # name of the projected coordinate system, for example "EPSG:25832"
    eastings: float             # map coordinates of the model origin, m
    northings: float
    height: float = 0.0         # map height of model z = 0, m
    rotation: float = 0.0       # degrees, counter-clockwise from map east to the x axis of the model


@dataclass
class Project:
    name: str
    root: Path
    out: Path
    reference: str
    levels: list[LevelSpec]
    heights: Heights
    stairs: list[StairSpec] = field(default_factory=list)
    tol: Tolerances = field(default_factory=Tolerances)
    input: Input = field(default_factory=Input)
    gates: Gates = field(default_factory=Gates)
    registration: dict[str, tuple[float, float]] = field(default_factory=dict)   # pinned translations, "level/block"
    solid_points: list[tuple[str, float, float]] = field(default_factory=list)    # review decisions: force a cell solid
    open_points: list[tuple[str, float, float]] = field(default_factory=list)     # review decisions: force a cell open
    author: str = ""
    organization: str = ""
    georeference: Georeference | None = None

    def level(self, key: str) -> LevelSpec:
        return next(lv for lv in self.levels if lv.key == key)

    def above(self, key: str) -> LevelSpec | None:
        """The storey whose floor is the next one above this storey's floor."""
        e = self.level(key).elevation
        higher = sorted((lv for lv in self.levels if lv.elevation > e), key=lambda lv: lv.elevation)
        return higher[0] if higher else None

    def below(self, key: str) -> LevelSpec | None:
        """The storey whose floor is the next one under this storey's floor."""
        e = self.level(key).elevation
        lower = sorted((lv for lv in self.levels if lv.elevation < e), key=lambda lv: lv.elevation)
        return lower[-1] if lower else None

    def top_of(self, key: str) -> float:
        """Elevation of the top of the plate that closes this storey (next floor, or the roof)."""
        a = self.above(key)
        return a.elevation if a else self.heights.roof


# ---------------------------------------------------------------- reading and checking

_REQUIRED = object()
_TOP = ("schema", "name", "out", "reference", "author", "organization", "heights", "level", "stair", "tolerances",
        "input", "gates", "registration", "review", "georeference")
_STAIR = tuple(f.name for f in fields(StairSpec))


class _Reader:
    """Takes values out of the parsed file and collects every problem."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def table(self, value: object, where: str, known: tuple[str, ...], renamed: dict[str, str] | None = None) -> dict:
        if not isinstance(value, dict):
            self.problems.append(f"{where}: must be a table")
            return {}
        for key in value:
            if key not in known:
                new = (renamed or {}).get(key)
                self.problems.append(f'{where}: unknown key "{key}"' + (f'; it is named "{new}" now' if new else ""))
        return value

    def number(self, d: dict, key: str, where: str, default: object = _REQUIRED) -> float:
        if key not in d:
            if default is _REQUIRED:
                self.problems.append(f'{where}: "{key}" is missing')
                return 0.0
            return default  # type: ignore[return-value]
        v = d[key]
        if isinstance(v, bool) or not isinstance(v, int | float):
            self.problems.append(f'{where}: "{key}" must be a number')
            return 0.0
        return float(v)

    def numbers(self, d: dict, key: str, where: str, count: int, default: object = _REQUIRED) -> tuple[float, ...]:
        if key not in d:
            if default is _REQUIRED:
                self.problems.append(f'{where}: "{key}" is missing')
                return (0.0,) * count
            return default  # type: ignore[return-value]
        v = d[key]
        if (not isinstance(v, list) or len(v) != count
                or any(isinstance(x, bool) or not isinstance(x, int | float) for x in v)):
            self.problems.append(f'{where}: "{key}" must be a list of {count} numbers')
            return (0.0,) * count
        return tuple(float(x) for x in v)

    def text(self, d: dict, key: str, where: str, default: object = _REQUIRED) -> str:
        if key not in d:
            if default is _REQUIRED:
                self.problems.append(f'{where}: "{key}" is missing')
                return ""
            return default  # type: ignore[return-value]
        v = d[key]
        if not isinstance(v, str) or not v.strip():
            self.problems.append(f'{where}: "{key}" must be a text that is not empty')
            return ""
        return v

    def names(self, d: dict, key: str, where: str, default: tuple[str, ...]) -> tuple[str, ...]:
        """One name or a list of names."""
        if key not in d:
            return default
        v = d[key]
        v = [v] if isinstance(v, str) else v
        if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
            self.problems.append(f'{where}: "{key}" must be a name or a list of names')
            return default
        return tuple(v)


def _levels(r: _Reader, top: dict, root: Path) -> list[LevelSpec]:
    entries = top.get("level")
    if not isinstance(entries, list) or not entries:
        r.problems.append("project file: at least one [[level]] is needed")
        return []
    out: list[LevelSpec] = []
    for i, entry in enumerate(entries, start=1):
        d = r.table(entry, f"level {i}", ("key", "name", "drawing", "elevation"), {"dwg": "drawing"})
        key = r.text(d, "key", f"level {i}")
        where = f'level "{key}"' if key else f"level {i}"
        if key and not KEY.fullmatch(key):
            r.problems.append(f"{where}: a key holds only letters, digits, '_', '.' and '-'")
        if key and any(lv.key == key for lv in out):
            r.problems.append(f"{where}: this key is used twice")
        drawings = r.names(d, "drawing", where, ())
        if not drawings:
            r.problems.append(f'{where}: "drawing" must name a drawing, or a list of drawings that share their coordinates')
        if len(set(drawings)) < len(drawings):
            r.problems.append(f"{where}: a drawing is named twice")
        for drawing in drawings:
            if Path(drawing).suffix.lower() not in (".dwg", ".dxf"):
                r.problems.append(f'{where}: drawing "{drawing}" must be a .dwg or a .dxf file')
            elif not (root / drawing).is_file():
                r.problems.append(f"{where}: drawing {root / drawing} does not exist")
        out.append(LevelSpec(key, r.text(d, "name", where, key), tuple(root / drawing for drawing in drawings),
                             r.number(d, "elevation", where)))
    seen: dict[float, str] = {}
    for lv in out:
        if lv.elevation in seen:
            r.problems.append(f'level "{lv.key}": has the elevation of level "{seen[lv.elevation]}"')
        seen[lv.elevation] = lv.key
    return out


def _heights(r: _Reader, top: dict, levels: list[LevelSpec]) -> Heights:
    names = tuple(f.name for f in fields(Heights))
    if "heights" not in top:
        r.problems.append("project file: [heights] is missing")
        return Heights(0.0)
    d = r.table(top["heights"], "heights", names)
    before = len(r.problems)
    defaults = Heights(0.0)
    h = Heights(r.number(d, "roof", "heights"),
                *(r.number(d, n, "heights", getattr(defaults, n)) for n in names[1:]))
    for n in names[1:]:
        if getattr(h, n) <= 0:
            r.problems.append(f'heights: "{n}" must be more than 0')
    if h.window_sill >= h.window_head:
        r.problems.append('heights: "window_sill" must be under "window_head"')
    if len(r.problems) > before or not levels:
        return h
    floors = sorted(levels, key=lambda lv: lv.elevation)
    tops = [lv.elevation for lv in floors[1:]] + [h.roof]
    for lv, top_of in zip(floors, tops, strict=True):
        clear = top_of - lv.elevation - h.plate
        if clear < max(h.door_head, h.window_head):
            what = "the roof" if lv is floors[-1] else "the next floor"
            r.problems.append(f'level "{lv.key}": {top_of - lv.elevation:.0f} mm up to {what} leaves {clear:.0f} mm under '
                              f"the plate, less than the door head or the window head")
    return h


def _stairs(r: _Reader, top: dict, keys: set[str]) -> list[StairSpec]:
    entries = top.get("stair", [])
    if not isinstance(entries, list):
        r.problems.append("project file: each stair is a [[stair]] table")
        return []
    out: list[StairSpec] = []
    for i, entry in enumerate(entries, start=1):
        d = r.table(entry, f"stair {i}", _STAIR)
        name = r.text(d, "name", f"stair {i}")
        where = f'stair "{name}"' if name else f"stair {i}"
        if name and any(s.name == name for s in out):
            r.problems.append(f"{where}: this name is used twice")
        kind = r.text(d, "kind", where)
        if kind and kind not in KINDS:
            r.problems.append(f'{where}: "kind" must be one of {", ".join(KINDS)}')
        levels = r.names(d, "levels", where, ())
        if not levels:
            r.problems.append(f'{where}: "levels" must name the storeys from which the stair climbs')
        draw_level = r.text(d, "draw_level", where, "")
        for key in (*levels, *([draw_level] if draw_level else [])):
            if keys and key not in keys:
                r.problems.append(f'{where}: there is no level "{key}"')
        entry_side, up = r.text(d, "entry", where, ""), r.text(d, "up", where, "")
        box: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0)
        at: tuple[float, ...] = (0.0, 0.0)
        sweep = r.number(d, "sweep_deg", where, 0.0)
        if kind in ("dogleg", "straight"):
            box = r.numbers(d, "box", where, 4)
            if "box" in d and any(box) and not (box[0] < box[2] and box[1] < box[3]):
                r.problems.append(f'{where}: "box" is x0, y0, x1, y1 with x0 < x1 and y0 < y1')
            if up not in SIDES:
                r.problems.append(f'{where}: "up" must be one of {", ".join(SIDES)}')
        if kind == "dogleg":
            if entry_side not in SIDES:
                r.problems.append(f'{where}: "entry" must be one of {", ".join(SIDES)}')
            elif up in SIDES and (entry_side in ("N", "S")) == (up in ("N", "S")):
                r.problems.append(f'{where}: "up" is the side of the first flight, so it cannot be {up} when "entry" is '
                                  f"{entry_side}")
        if kind == "spiral":
            at = r.numbers(d, "at", where, 2)
            if sweep == 0.0:
                r.problems.append(f'{where}: "sweep_deg" must not be 0')
        out.append(StairSpec(name, kind, levels, box, at, entry_side, up,   # type: ignore[arg-type]
                             r.number(d, "start_deg", where, 0.0), sweep, draw_level))
    return out


def _input(r: _Reader, top: dict) -> Input:
    d = r.table(top.get("input", {}), "input", tuple(f.name for f in fields(Input)))
    base = Input()
    unit = r.text(d, "unit", "input", base.unit)
    if unit not in UNITS:
        r.problems.append(f'input: "unit" must be one of {", ".join(UNITS)}')
    layers = {n: r.names(d, n, "input", getattr(base, n)) for n in LAYERS}
    words = {n: tuple(x.lower() for x in r.names(d, n, "input", getattr(base, n))) for n in ("shafts", "cores")}
    return Input(unit=unit, **layers, **words, room_number=r.text(d, "room_number", "input", base.room_number),
                 room_name=r.text(d, "room_name", "input", base.room_name),
                 room_wing=r.text(d, "room_wing", "input", base.room_wing))


def _gates(r: _Reader, top: dict) -> Gates:
    d = r.table(top.get("gates", {}), "gates", tuple(f.name for f in fields(Gates)))
    base = Gates()
    g = Gates(r.number(d, "on_line", "gates", base.on_line), r.number(d, "unexplained", "gates", base.unexplained),
              r.numbers(d, "riser", "gates", 2, base.riser))   # type: ignore[arg-type]
    if not 0.0 < g.on_line <= 1.0:
        r.problems.append('gates: "on_line" is a share, more than 0 and at most 1')
    if not 0.0 <= g.unexplained < 1.0:
        r.problems.append('gates: "unexplained" is a share, at least 0 and less than 1')
    if not 0.0 < g.riser[0] < g.riser[1]:
        r.problems.append('gates: "riser" is the smallest and the largest usual riser in mm')
    return g


def _points(r: _Reader, d: dict, key: str, keys: set[str]) -> list[tuple[str, float, float]]:
    out = []
    entries = d.get(key, [])
    for p in entries if isinstance(entries, list) else [None]:
        if (not isinstance(p, list) or len(p) != 3 or not isinstance(p[0], str)
                or any(isinstance(v, bool) or not isinstance(v, int | float) for v in p[1:])):
            r.problems.append(f'review: each point of "{key}" is [storey, x, y]')
        elif keys and p[0] not in keys:
            r.problems.append(f'review: there is no level "{p[0]}"')
        else:
            out.append((p[0], float(p[1]), float(p[2])))
    return out


def parse(data: dict, root: Path) -> Project:
    """The project that `data` defines. Paths in it are relative to `root`. Raises ProjectError with all problems."""
    r = _Reader()
    top = r.table(data, "project file", _TOP)
    if top.get("schema", SCHEMA) != SCHEMA:
        r.problems.append(f'project file: "schema" is {top["schema"]!r}; this version of plan2bim reads schema {SCHEMA}')
    levels = _levels(r, top, root)
    keys = {lv.key for lv in levels}
    reference = r.text(top, "reference", "project file")
    if reference and keys and reference not in keys:
        r.problems.append(f'project file: "reference" names level "{reference}", which does not exist')
    tol_names = tuple(f.name for f in fields(Tolerances))
    tol_table = r.table(top.get("tolerances", {}), "tolerances", tol_names)
    tol = Tolerances(*(r.number(tol_table, n, "tolerances", getattr(Tolerances(), n)) for n in tol_names))
    for n in tol_names:
        if getattr(tol, n) <= 0:
            r.problems.append(f'tolerances: "{n}" must be more than 0')
    registration = {}
    pins = top.get("registration", {})
    if not isinstance(pins, dict):
        r.problems.append("registration: must be a table")
        pins = {}
    for block in pins:
        level, _, index = block.rpartition("/")
        if not index.isdigit() or (keys and level not in keys):
            r.problems.append(f'registration: "{block}" must be a block as the report names it, for example "2/1"')
        registration[block] = r.numbers(pins, block, "registration", 2)
    review = r.table(top.get("review", {}), "review", ("solid", "open"))
    geo = None
    if "georeference" in top:
        g = r.table(top["georeference"], "georeference", tuple(f.name for f in fields(Georeference)))
        geo = Georeference(r.text(g, "crs", "georeference"), r.number(g, "eastings", "georeference"),
                           r.number(g, "northings", "georeference"), r.number(g, "height", "georeference", 0.0),
                           r.number(g, "rotation", "georeference", 0.0))
    project = Project(
        name=r.text(top, "name", "project file"), root=root, out=root / r.text(top, "out", "project file", "out"),
        reference=reference, levels=levels, heights=_heights(r, top, levels), stairs=_stairs(r, top, keys), tol=tol,
        input=_input(r, top), gates=_gates(r, top), registration=registration,   # type: ignore[arg-type]
        solid_points=_points(r, review, "solid", keys), open_points=_points(r, review, "open", keys),
        author=r.text(top, "author", "project file", ""), organization=r.text(top, "organization", "project file", ""),
        georeference=geo,
    )
    if r.problems:
        raise ProjectError(r.problems)
    return project


def load(path: str | Path) -> Project:
    """Read a project file, TOML or JSON."""
    path = Path(path).resolve()
    if not path.is_file():
        raise InputError(f"{path} does not exist")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix.lower() == ".toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        else:
            raise InputError(f"{path.name}: a project file is a .toml or a .json file")
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InputError(f"{path.name}: {e}") from e
    return parse(data, path.parent)
