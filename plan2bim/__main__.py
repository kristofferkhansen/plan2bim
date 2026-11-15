"""Build the model of a project: python -m plan2bim PROJECT.toml

Writes, under the project's output directory:
  NAME.html     the model in a viewer, one file, opens from disk
  NAME.ifc      the model as IFC4
  NAME.txt      registration, what the classification noted, and the checks of the result
  qa/KEY.png    per storey, the drawing with what every cell was taken to be
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from . import (
    arrange,
    assemble,
    classify,
    ifc,
    level,
    mesh,
    project,
    qa,
    register,
    source,
    stairs,
    verify,
    web,
)


def main(path: str) -> int:
    started = time.time()
    p = project.load(path)
    name = Path(path).stem

    def say(what: str) -> None:
        print(f"{time.time() - started:6.1f} s  {what}", flush=True)

    drawings = {lv.key: source.read(lv.key, source.dwg_to_dxf(lv.dwg, p.out / "dxf" / f"{lv.key}.dxf")) for lv in p.levels}
    blocks = register.register(p, drawings)
    levels = {lv.key: level.build(p, drawings[lv.key], blocks[lv.key]) for lv in p.levels}
    say(f"read and registered {len(levels)} drawings")

    treads = stairs.footprints(p, levels)
    cls = {}
    for lv in p.levels:
        cls[lv.key] = c = classify.classify(p, levels[lv.key], arrange.build(levels[lv.key], p.tol), treads.get(lv.key))
        say(f"{lv.name}: {len(c.solids())} wall cells, {len(c.doors)} doors, {len(c.windows)} windows, "
            f"{len(levels[lv.key].rooms)} rooms")

    flights = stairs.build(p, levels, {key: c.blocked() for key, c in cls.items()})
    model = assemble.assemble(p, levels, cls, flights)
    solids = mesh.solids(model)
    say(f"model: {len(model.elements)} elements, {sum(len(e.pieces) for e in model.elements)} prisms, {len(solids)} solids")

    size = web.write(p.out / f"{name}.html", p, levels, model, solids)
    say(f"{p.out / (name + '.html')}: {size / 1e6:.1f} MB")
    ifc.write(p.out / f"{name}.ifc", p, model)
    say(f"{p.out / (name + '.ifc')}")

    report = verify.run(p, levels, cls, model, solids, flights, p.out / f"{name}.ifc")
    notes = ["", "Noted while reading the drawings", "", *(model.notes or ["nothing"]), ""]
    (p.out / f"{name}.txt").write_text(register.report(blocks) + "\n".join(notes) + "\n" + report.text())
    say(f"{p.out / (name + '.txt')}")
    for key, c in cls.items():
        qa.overview(c, p.out / "qa" / f"{key}.png")
    say("qa plots")
    for s in report.storeys:
        print(f"         storey {s.level}: {100 * s.cut_on_line:.2f} % of the cut model outline is on a drawn line; "
              f"{sum(f.length for f in s.drawn_off) / 1000:.1f} m of {s.drawn_length / 1000:.0f} m of drawn line is not in the model")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
