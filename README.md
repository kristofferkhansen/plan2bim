# plan2bim

`plan2bim` builds a 3D model of a building from one 2D floor plan drawing (DWG) for each storey.
It writes the model as an IFC4 file and as a viewer in one HTML file.

Nothing in plan is measured again or drawn again. A wall is the area that the drawn lines close.
Each wall face of the model thus lies on a line of the drawing.
A plan drawing holds no heights. They come from the project file.

## Run

```sh
uv sync                                   # ezdxf, shapely, numpy, scipy, manifold3d, ifcopenshell, matplotlib
curl -L -o plan2bim/vendor/three.min.js https://unpkg.com/three@0.149.0/build/three.min.js
uv run python -m plan2bim PROJECT.toml    # needs dwg2dxf (LibreDWG) on the PATH
```

- The project file holds the drawings, the storey elevations, the stairs and every assumption that is
  not in the drawings. `example.toml` shows all fields.
- The viewer needs three.js r149. The second command gets it. See `plan2bim/vendor/README.md`.

The outputs go to the `out` directory of the project. `NAME` is the name of the project file without `.toml`.

| Output | Content |
|---|---|
| `NAME.html` | The model in a viewer: one file, opens from disk, no server and no network access |
| `NAME.ifc` | The model as IFC4 |
| `NAME.txt` | Registration, notes from the drawings, results of all checks |
| `qa/KEY.png` | The drawing of each storey, with each cell coloured by what it was taken to be |

## Drawings that it reads

Area drawings for facility management: one DWG for each storey, in millimetres.

| Layer | Content |
|---|---|
| `0` | All building lines as polylines: wall faces, door leaves and swing arcs, window lines, stair risers |
| `Area_NTA` | One closed polyline for each room, and a block with the attributes `ROOMNUMBER` and `ROOMNAME` |
| `Area_BTA` | The gross outline of each building block |
| `Area_BRA` | The inner face of the exterior walls of each block |

The layer names are constants in `plan2bim/source.py`. A drawing with another convention needs changes
there and in the rules of `plan2bim/classify.py`.

## Code

| Module | Content |
|---|---|
| `plan2bim/source.py`, `register.py`, `level.py` | Read the drawings, put all building blocks into one frame |
| `plan2bim/arrange.py`, `classify.py` | Every cell that the drawn lines enclose, and what each cell is |
| `plan2bim/stairs.py`, `assemble.py`, `model.py` | Stairs, heights, the model as vertical prisms |
| `plan2bim/mesh.py`, `web.py`, `viewer.html`, `ifc.py` | Closed solids, the viewer, the IFC file |
| `plan2bim/verify.py`, `qa.py` | Checks of the finished model, review plots |

`docs/plan2bim.md` gives the method, the assumptions, the checks and the tools.
