"""Project file: which drawings, which elevations, and every assumption the drawings do not contain."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LevelSpec:
    key: str            # short id used in file names and reports
    name: str           # storey name written to the model
    dwg: Path
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
    registration: dict[str, tuple[float, float]] = field(default_factory=dict)   # pinned translations, "level/block"
    solid_points: list[tuple[str, float, float]] = field(default_factory=list)    # review decisions: force a cell solid
    open_points: list[tuple[str, float, float]] = field(default_factory=list)     # review decisions: force a cell open

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


def load(path: str | Path) -> Project:
    path = Path(path).resolve()
    d = tomllib.loads(path.read_text())
    root = path.parent
    levels = [LevelSpec(lv["key"], lv["name"], root / lv["dwg"], float(lv["elevation"])) for lv in d["level"]]
    stairs = [StairSpec(name=s["name"], kind=s["kind"], levels=tuple(s["levels"]),
                        box=tuple(float(v) for v in s.get("box", (0, 0, 0, 0))),
                        at=tuple(float(v) for v in s.get("at", (0, 0))),
                        entry=s.get("entry", ""), up=s.get("up", ""), start_deg=float(s.get("start_deg", 0.0)),
                        sweep_deg=float(s.get("sweep_deg", 0.0)), draw_level=s.get("draw_level", ""))
              for s in d.get("stair", [])]
    rev = d.get("review", {})
    return Project(
        name=d["name"], root=root, out=root / d.get("out", "out"), reference=d["reference"], levels=levels,
        heights=Heights(**d["heights"]), stairs=stairs, tol=Tolerances(**d.get("tolerances", {})),
        registration={k: tuple(v) for k, v in d.get("registration", {}).items()},
        solid_points=[(p[0], float(p[1]), float(p[2])) for p in rev.get("solid", [])],
        open_points=[(p[0], float(p[1]), float(p[2])) for p in rev.get("open", [])],
    )
