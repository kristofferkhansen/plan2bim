"""The command line.

  plan2bim build PROJECT      build the model of a project and check it
  plan2bim check PROJECT      check a project file, build nothing
  plan2bim inspect DRAWING    list the layers, the kinds of entity and the unit of a drawing

`build` writes, under the output directory of the project:
  NAME.html     the model in a viewer, one file, opens from disk
  NAME.ifc      the model as IFC4
  NAME.txt      the result, the registration, what the reader noted, and the checks
  NAME.json     the same result for a program
  qa/KEY.png    per storey, the drawing with what every cell was taken to be

Exit status: 0 the build passed its checks; 1 a check failed (with --strict also when there is something
to look at); 2 the project file, a drawing or the command is wrong; 3 an error in plan2bim itself.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from collections import Counter
from pathlib import Path

from ezdxf import bbox

from . import __version__, source
from .errors import InputError
from .project import Input, load

log = logging.getLogger("plan2bim")


class _Elapsed(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"{record.relativeCreated / 1000.0:6.1f} s  {record.getMessage()}"


def _build(args: argparse.Namespace) -> int:
    # the geometry libraries load here, not for `check` and `inspect`
    from . import pipeline

    project = load(args.project)
    if args.out:
        project.out = Path(args.out).resolve()
    outcome = pipeline.run(project, Path(args.project).stem, plots=not args.no_plots)
    print("\n".join(outcome.report.result()))
    print(f"Report: {outcome.files['report']}")
    status = outcome.report.status
    return 1 if status == "fail" or (args.strict and status == "warn") else 0


def _check(args: argparse.Namespace) -> int:
    project = load(args.project)
    print(f"{args.project}: no problem found. Storeys: {len(project.levels)}. Stairs: {len(project.stairs)}.")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    reads = {layer.casefold(): what for what, layers in (
        ("building lines", Input().lines), ("rooms", Input().rooms), ("gross outline", Input().gross),
        ("inner outline", Input().inner)) for layer in layers}
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(args.drawing):
            path = Path(name)
            if not path.is_file():
                raise InputError(f"{path} does not exist")
            doc = source.document(source.to_dxf(path, Path(tmp) / f"{i}.dxf"))
            unit = source.INSUNITS.get(doc.header.get("$INSUNITS", 0))
            box = bbox.extents(doc.modelspace(), fast=True)
            print(f"{path}")
            print(f"  unit: {unit or 'not one of mm, cm, m, or not given'}")
            print(f"  extent: {box.size.x:.0f} x {box.size.y:.0f} drawing units" if box.has_data else "  extent: nothing drawn")
            counts = Counter((e.dxf.layer, e.dxftype()) for e in doc.modelspace())
            print(f"  {'layer':<28} {'entity':<12} {'count':>7}   read by default as")
            for (layer, kind), n in sorted(counts.items(), key=lambda item: (item[0][0].casefold(), -item[1])):
                print(f"  {layer:<28} {kind:<12} {n:>7}   {reads.get(layer.casefold(), '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plan2bim", description="Floor plan drawings to a 3D building model.",
                                     epilog=(__doc__ or "").split("\n\n", 2)[2],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"plan2bim {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the model of a project and check it")
    build.add_argument("project", help="project file, .toml or .json")
    build.add_argument("--out", help="directory for the outputs (default: `out` of the project file)")
    build.add_argument("--strict", action="store_true", help="exit with 1 also when there is something to look at")
    build.add_argument("--no-plots", action="store_true", help="do not draw the plot of each storey")
    build.add_argument("-q", "--quiet", action="store_true", help="do not show progress")
    check = sub.add_parser("check", help="check a project file, build nothing")
    check.add_argument("project", help="project file, .toml or .json")
    inspect = sub.add_parser("inspect", help="list the layers, the kinds of entity and the unit of a drawing")
    inspect.add_argument("drawing", nargs="+", help="DWG or DXF file")
    args = parser.parse_args(argv)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Elapsed())
    log.addHandler(handler)
    log.setLevel(logging.WARNING if getattr(args, "quiet", False) else logging.INFO)
    try:
        return {"build": _build, "check": _check, "inspect": _inspect}[args.command](args)
    except InputError as e:
        for line in str(e).splitlines():
            print(f"error: {line}", file=sys.stderr)
        return 2
    except Exception:
        log.exception("an error in plan2bim itself")
        return 3
    finally:
        log.removeHandler(handler)
