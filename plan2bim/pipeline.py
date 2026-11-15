"""The build, stage by stage. The command line and a server both call these functions.

Each stage is a function of the project and of the stages before it. What each stage reads of the project:

| Stage | Reads |
|---|---|
| `read_drawings` | the drawings, `[input]`, `[tolerances]`, `reference`, `[registration]` |
| `classify_storeys` | `[[stair]]`, `[review]` |
| `build_model` | `[heights]`, the elevations, `[[stair]]` |
| `run` | all of the above, and `[gates]`, `author`, `organization`, `[georeference]` |
| `export` | the same as `run`; it writes the IFC file only |

The classification reads the stairs: a cell under a flight is a tread and not a wall, and a thin strip at
a stair is a string and not a sliding door. A classification that was made with other stairs, or with
none, is thus a preview only. It must be made again when a stair is added or changed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from . import (
    __version__,
    arrange,
    assemble,
    classify,
    ifc,
    level,
    mesh,
    qa,
    register,
    source,
    stairs,
    verify,
    web,
)
from .model import Model, Note
from .project import SCHEMA, Project

log = logging.getLogger("plan2bim")


@dataclass
class Read:
    """The drawings in one frame."""
    drawings: dict[str, source.Drawing]
    blocks: dict[str, list[register.Block]]
    levels: dict[str, level.Level]


@dataclass
class Built:
    flights: list[stairs.Stair]
    model: Model
    solids: list[mesh.Solid]


@dataclass
class Outcome:
    report: verify.Report
    files: dict[str, Path]      # what was written: viewer, ifc, report, data, and the plot of each storey


def read_drawings(project: Project) -> Read:
    drawings = {lv.key: source.read(lv.key, source.to_dxf(lv.drawing, project.out / "dxf" / f"{lv.key}.dxf"), project.input)
                for lv in project.levels}
    blocks = register.register(project, drawings)
    levels = {lv.key: level.build(project, drawings[lv.key], blocks[lv.key]) for lv in project.levels}
    log.info("read and registered %d drawings", len(levels))
    return Read(drawings, blocks, levels)


def classify_storeys(project: Project, read: Read) -> dict[str, classify.Classified]:
    treads = stairs.footprints(project, read.levels)
    out = {}
    for lv in project.levels:
        out[lv.key] = c = classify.classify(project, read.levels[lv.key], arrange.build(read.levels[lv.key], project.tol),
                                            treads.get(lv.key))
        log.info("%s: %d wall cells, %d doors, %d windows, %d rooms", lv.name, len(c.solids()), len(c.doors),
                 len(c.windows), len(read.levels[lv.key].rooms))
    return out


def build_model(project: Project, read: Read, cls: dict[str, classify.Classified]) -> Built:
    left_out: list[Note] = []
    flights = stairs.build(project, read.levels, {key: c.blocked() for key, c in cls.items()}, left_out)
    model = assemble.assemble(project, read.levels, cls, flights)
    model.notes += left_out
    solids = mesh.solids(model)
    log.info("model: %d elements, %d prisms, %d solids", len(model.elements), sum(len(e.pieces) for e in model.elements),
             len(solids))
    return Built(flights, model, solids)


def _data(project: Project, read: Read, built: Built, report: verify.Report, files: dict[str, Path]) -> dict:
    kinds: dict[str, int] = {}
    for e in built.model.elements:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    return {
        "schema": SCHEMA, "plan2bim": __version__, "project": project.name, **report.data(),
        "elements": dict(sorted(kinds.items())), "rooms": len(built.model.spaces),
        "registration": [{"block": b.id, "tx": round(b.t[0], 3), "ty": round(b.t[1], 3), "method": b.evidence.get("method"),
                          "against": b.evidence.get("against"), "coincident_points": b.evidence.get("exact_points")}
                         for blocks in read.blocks.values() for b in blocks],
        "files": {what: path.relative_to(project.out).as_posix() for what, path in files.items()},
    }


def _stages(project: Project) -> tuple[Read, dict[str, classify.Classified], Built]:
    read = read_drawings(project)
    cls = classify_storeys(project, read)
    return read, cls, build_model(project, read, cls)


def export(project: Project, path: Path) -> verify.Report:
    """Build the model and write its IFC file to `path`, and nothing else. The checks are those of `run`."""
    read, cls, built = _stages(project)
    ifc.write(path, project, built.model)
    log.info("%s", path)
    return verify.run(project, read.levels, cls, built.model, built.solids, built.flights, path)


def run(project: Project, name: str, *, plots: bool = True) -> Outcome:
    """Build the model and write it to `project.out`. `name` is the stem of every file written."""
    read, cls, built = _stages(project)
    out = project.out
    files = {"viewer": out / f"{name}.html", "ifc": out / f"{name}.ifc", "report": out / f"{name}.txt",
             "data": out / f"{name}.json"}

    ifc.write(files["ifc"], project, built.model)
    log.info("%s", files["ifc"])
    size = web.write(files["viewer"], project, read.levels, built.model, built.solids, files["ifc"])
    log.info("%s: %.1f MB", files["viewer"], size / 1e6)

    report = verify.run(project, read.levels, cls, built.model, built.solids, built.flights, files["ifc"])
    noted = ["", "Noted while reading the drawings", "", *([n.text for n in built.model.notes] or ["nothing"]), ""]
    files["report"].write_text("\n".join([f"# {project.name}", "", *report.result(), "", ""])
                               + register.report(read.blocks) + "\n".join(noted) + "\n" + report.text(), encoding="utf-8")
    log.info("%s", files["report"])
    if plots:
        for key, c in cls.items():
            files[f"plot {key}"] = out / "qa" / f"{key}.png"
            qa.overview(c, files[f"plot {key}"])
        log.info("plots of the storeys")
    files["data"].write_text(json.dumps(_data(project, read, built, report, files), indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    for s in report.storeys:
        log.info("storey %s: %.2f %% of the cut model outline is on a drawn line; %.1f m of %.0f m of drawn line is not "
                 "in the model", s.level, 100 * s.cut_on_line, sum(f.length for f in s.drawn_off) / 1000, s.drawn_length / 1000)
    return Outcome(report, files)
