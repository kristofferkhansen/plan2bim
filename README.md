# plan2bim

`plan2bim` builds a 3D model of a building from one 2D floor plan drawing (DWG or DXF) for each storey.
It writes the model as an IFC4 file and as a viewer in one HTML file, and it checks the result.

Nothing in plan is measured again or drawn again. A wall is the area that the drawn lines close.
Each wall face of the model thus lies on a line of the drawing.
A plan drawing holds no heights. They come from the project file.

## Run

```sh
uv sync
uv run plan2bim build example/example.toml      # the example building; its drawings are in the repository
uv run plan2bim build PROJECT.toml              # your building
```

A DXF needs nothing else. A DWG needs `dwg2dxf` of LibreDWG on the `PATH`. The Docker image holds it:

```sh
docker build -t plan2bim .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work plan2bim build /work/PROJECT.toml
```

| Command | What it does |
|---|---|
| `plan2bim build PROJECT` | Builds the model and checks it. `--out DIR`, `--strict`, `--no-plots`, `-q`. |
| `plan2bim check PROJECT` | Checks the project file and builds nothing. It reports all problems at once. |
| `plan2bim inspect DRAWING` | Lists the layers, the kinds of entity and the unit of a drawing. Use it for a drawing with other layer names. |

The project file holds the drawings, the storey elevations, the stairs and every assumption that is
not in the drawings. It is TOML or JSON. `example/example.toml` shows all fields.

The outputs go to the `out` directory of the project. `NAME` is the name of the project file without its suffix.

| Output | Content |
|---|---|
| `NAME.html` | The model in a viewer: one file, opens from disk, no server and no network access |
| `NAME.ifc` | The model as IFC4 |
| `NAME.txt` | The result, the registration, notes from the drawings, results of all checks |
| `NAME.json` | The same result for a program: status, numbers of each storey, every note with its position |
| `qa/KEY.png` | The drawing of each storey, with each cell coloured by what it was taken to be |

The exit status of `build` says how it went:

| Status | Meaning |
|---|---|
| 0 | The build passed its checks. There can be points to look at. |
| 1 | A check failed. With `--strict`: also when there is a point to look at. |
| 2 | The project file, a drawing or the command is wrong. Nothing was built. |
| 3 | An error in `plan2bim` itself. |

## Drawings that it reads

One drawing for each storey. The defaults are those of area drawings for Norwegian facility management, in millimetres.

| Layer | Content |
|---|---|
| `0` | All building lines: wall faces, door leaves and swing arcs, window lines, stair risers |
| `Area_NTA` | One closed polyline for each room, and a block with the attributes `ROOMNUMBER` and `ROOMNAME` |
| `Area_BTA` | The gross outline of each building block |
| `Area_BRA` | The inner face of the exterior walls of each block |

The `[input]` table of the project file names other layers, other attribute tags, another unit (`mm`, `cm`, `m`)
and other room names for lifts and shafts. The rules that say what a drawn cell is are in
`plan2bim/classify.py`. They are not in the project file.

## Tests

```sh
uv run pytest
PLAN2BIM_CORPUS=/path/to/projects uv run pytest tests/test_corpus.py    # your own buildings, see the file
uv run python tests/samples.py dwg/samples                              # fetch public DWG files
PLAN2BIM_SAMPLES=dwg/samples uv run pytest tests/test_samples.py        # read them; needs dwg2dxf
```

The tests build the example building and compare the result with what the drawings show.
Real drawings are not in the repository. `tests/test_corpus.py` runs them from a directory of your own.
`tests/test_samples.py` reads public DWG files of Autodesk and LibreDWG, in each version of DWG from R14 to 2018.
No public drawing has the area layers, so these tests read drawings and build nothing.

## Code

| Module | Content |
|---|---|
| `plan2bim/cli.py`, `pipeline.py` | The command line, and the build stage by stage for use from a program |
| `plan2bim/project.py`, `errors.py` | The project file and its checks |
| `plan2bim/source.py`, `register.py`, `level.py` | Read the drawings, put all building blocks into one frame |
| `plan2bim/arrange.py`, `classify.py` | Every cell that the drawn lines enclose, and what each cell is |
| `plan2bim/stairs.py`, `assemble.py`, `model.py` | Stairs, heights, the model as vertical prisms |
| `plan2bim/mesh.py`, `web.py`, `viewer.html`, `ifc.py` | Closed solids, the viewer, the IFC file |
| `plan2bim/verify.py`, `qa.py` | Checks of the finished model, review plots |

`docs/plan2bim.md` gives the method, the assumptions, the checks and the tools.

## Licence

All rights reserved. See `LICENSE`.
