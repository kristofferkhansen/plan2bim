# plan2bim: floor plan drawings to a 3D building model

`plan2bim` builds a 3D model of a building from one 2D floor plan drawing for each storey.
This document gives the method, the assumptions, the checks and the tools.

## In short

- The plan geometry of the model is the drawn geometry. The first check measures this: in a plan cut
  1.2 m above each floor, how much of the outline of the walls and columns lies on a drawn line (within 2 mm).
- The heights are assumptions. A plan drawing holds no height, no section and no elevation.
  Every height is in the project file and can be changed there.
- No two solids of the model may overlap. The IFC file is validated against the schema and is read back
  with a second geometry kernel.

## Run

```sh
uv sync
uv run plan2bim build PROJECT.toml
```

A storey drawing is a DWG or a DXF. A DXF is read as it is. For a DWG, `dwg2dxf` of LibreDWG (0.14 was used)
must be on the `PATH`. It converts each DWG to `out/dxf/KEY.dxf` once. The Docker image of the repository holds it.
When `dwg2dxf` cannot convert a drawing, convert it to DXF with another tool and name the DXF in the project file.

`plan2bim check PROJECT.toml` checks the project file only. `plan2bim inspect DRAWING` lists the layers,
the kinds of entity and the unit of a drawing.

`NAME` is the name of the project file without its suffix.

| Output | Content |
|---|---|
| `out/NAME.html` | The model in a viewer. One file, no server, no network access. It shows storeys and categories apart, room numbers and names, the source drawing as red lines, a horizontal or a vertical section, the assumptions and the notes. |
| `out/NAME.ifc` | The model as IFC4, for other BIM tools. |
| `out/NAME.txt` | The result, the registration, everything the reader noted in the drawings, and the results of all checks. Read this file after each run. |
| `out/NAME.json` | The same result for a program. See "Result and gates". |
| `out/qa/KEY.png` | For each storey: the drawing, with each cell coloured by what it was taken to be. |

## What the drawings must hold

The defaults are those of area drawings for Norwegian facility management (AutoCAD 2018, millimetres).

| Layer | Content |
|---|---|
| `0` | All building lines: wall faces, door leaves and swing arcs, window lines, stair risers, lift and shaft crosses, fixtures. No wall is an object. A wall is two faces and the lines that close its ends. |
| `Area_NTA` | One closed polyline for each room, and a block with room number and room name. |
| `Area_BTA` | The gross outline of each building block. |
| `Area_BRA` | The inner face of the exterior walls of each block. |

The `[input]` table of the project file changes this convention:

| Key | Meaning | Default |
|---|---|---|
| `unit` | Unit of the drawing coordinates: `mm`, `cm` or `m`. The model is always in mm. | `mm` |
| `lines`, `rooms`, `gross`, `inner` | The layer, or a list of layers, for each content of the table above. | `0`, `Area_NTA`, `Area_BTA`, `Area_BRA` |
| `room_number`, `room_name`, `room_wing` | Attribute tags of the room label block. | `ROOMNUMBER`, `ROOMNAME`, `WING` |
| `shafts` | Room names of lifts and shafts. Such a room has no floor where the storey below has one too. | `heis`, `sjakt` |
| `cores` | Words in the names of rooms that run through all storeys. They are evidence for the registration. | `heis`, `trapp`, `sjakt` |

The reader takes LWPOLYLINE, POLYLINE (2D), LINE, ARC and CIRCLE as building lines, and INSERT with attributes
as room labels. Every other entity on a layer that the reader uses is counted and reported, for example a SPLINE
or a block of building lines. Entities on other layers are not read and not reported.
A drawing with no building lines or no gross outline on the named layers stops the run. The message lists the
layers that the drawing has. A drawing whose header gives another unit than the project file is reported.

Such drawings do not hold: heights, sections, elevations, the terrain, the roof shape, wall types,
door and window types, or the direction in which a stair climbs.

## Method

The rule of the method: **nothing in plan is measured again or drawn again.**
A wall is not a centre line with a thickness. A wall is the area that the drawn lines close.

| Step | Module | What it does |
|---|---|---|
| 1 | `source.py` | Reads each DXF into lines, arcs, room polylines and outlines, in mm. No interpretation. |
| 2 | `register.py` | Puts all building blocks of all storeys into one frame. See "Registration". |
| 3 | `level.py`, `arrange.py` | Builds the planar arrangement of each storey: every cell that the drawn lines and the room outlines enclose. One repair: a line that stops less than 4 mm short of the line it was drawn towards is joined to it. Dashed lines do not close a cell. |
| 4 | `classify.py` | Says what each cell is. The rules are listed at the top of the file. See "Classification". |
| 5 | `stairs.py` | Reads the treads of each stair from the riser lines. The direction of climb comes from the project file. A stair that the drawing does not show where the project file says is left out, and the build fails with the rest of the model in place. |
| 6 | `assemble.py` | Gives each cell its heights from the project file. Each piece of each element is a vertical prism: a plan polygon, a bottom and a top. |
| 7 | `mesh.py` | Joins the prisms of one material, category and storey into one closed solid (manifold3d). The join removes all faces between adjoining prisms. This is why the display has no doubled faces and no flicker. |
| 8 | `ifc.py` | Writes the IFC file: wall, opening that cuts the wall, door or window that fills the opening. Rooms are `IfcSpace`. Doors and windows carry their overall size. Rooms, walls, plates, columns, doors and windows carry base quantities. |
| 9 | `web.py`, `viewer.html` | Writes the viewer: the solids, the viewer code and three.js r149 in one HTML file. |
| 10 | `verify.py` | Checks the finished model. See "Checks". |

### Registration

Each drawing has its own origin. The unit of registration is the building block (one gross outline),
not the drawing, because one drawing can hold blocks that are displaced relative to each other.
One translation for such a drawing misplaces a block.

Evidence for each translation, strongest first: arcs of curved walls with the same radius; rooms of the
same size that run through the storeys (lifts, stairs, shafts); line end points that coincide exactly.
Drawings that were traced over each other share many end points. A translation is accepted only with
at least 8 coincident end points. `out/NAME.txt` gives the evidence for each block.
A block for which no translation is found stops the run. Its translation can be pinned in the project file.

### Classification

| Cell | Rule |
|---|---|
| Room | Inside a room polyline. |
| Wall | Closed completely by drawn lines, not a room, and too narrow to be a space (inscribed radius below 300 mm). Kept exactly as drawn, also when it is not a rectangle. |
| Door | A swing arc with its leaf. The rough opening runs from wall end to wall end and through the wall thickness, both as drawn. |
| Window | A group of very thin closed cells with a long strip is the joinery. The cells between the two jamb lines are the niche. |
| Opening | Two wall ends of about the same thickness that face each other, 500 to 2100 mm apart, with no door. |
| Sliding doors, screens | Thin closed strips (25 to 70 mm) in a room. A gap between two wall ends that such panels close is one opening. |
| Shaft | A cell with two equal diagonals drawn across it, of which at least 90 % of the outline is drawn. No wall stands in it and the floor is open. |
| Column | A drawn circle of column size. |
| Balcony | A closed area outside the building at a door, with a thin closed strip round it. The strip is the balustrade. |
| Implied wall | A wall of which only one face is drawn. The other face is the room outline. Each one is reported. |

The classifier reports every case in which it had to decide. These reports are in `out/NAME.txt`
under "Noted while reading the drawings".

## Assumptions

These values are not in the drawings. They are in the project file. `example/example.toml` shows them.

| Assumption | Default | Source |
|---|---|---|
| Floor to floor | None. The `elevation` of each storey is in the project file. | The user. See the note below. |
| Floor and roof plate | 300 mm | Assumption. |
| Roof | Flat plate at the level of the next floor, over each part with no storey above | Assumption. A plan shows no roof. |
| Door head | 2100 mm | Assumption. |
| Window sill and head | 900 mm and 2100 mm | Assumption. |
| Stair landing and stair thickness | 200 mm | Assumption. |
| Balustrades | 1100 mm | Assumption. |
| Direction of climb of each stair | `entry` and `up` in the project file | The user. A plan shows it only where a cut line or an arrow is drawn. |

**Note about the floor to floor height.** The stairs give the only evidence of height in a plan: the number
of risers is drawn. The floor to floor height divided by the number of risers gives the riser.
Risers of 150 to 190 mm are usual. A larger or a smaller riser shows that the floor to floor height is
probably wrong. The build reports such a stair as a point to look at. A section drawing or one measured height
settles this.

## Checks

All checks use only the delivered geometry: the prisms of the model and the solids of the IFC file.
Nothing in the checks reads the intermediate results of the pipeline. The results are in `out/NAME.txt`
and in `out/NAME.json`.

### 1. Plan cut against the drawing, both ways

The model is cut 1.2 m above each floor. A point is on a line if it is within 2 mm. Points are 25 mm apart.

- Model to drawing: the share of the model outline in the cut that lies on a drawn line. The rest is
  walls with a face that is not drawn, and posts between windows. Each stretch is listed.
- Drawing to model: the share of the drawn lines that lies on the outline of a model part.
  The rest is counted by reason:
  - Door symbols and shaft crosses. They are drawn and not built.
  - A line inside a wall. It parts nothing.
  - At a stair: walking lines, strings, guards, and a stair as another storey draws it.
  - Elsewhere. Each stretch is listed with its position.

### 2. Balance of areas

The gross outline and the room outlines are the drafter's own. Each storey must balance:
the gross outline is the sum of rooms, walls and columns, openings with doors and windows,
floor with no room outline, open area outside all rooms, and nothing. "Nothing" must be near zero.
Wall that stands inside a room outline is reported apart.

### 3. Solids that overlap

Each pair of solids is intersected. An overlap of more than 0.001 m3 is a finding.

### 4. Up and down

- Every room has a floor under it, apart from the named holes. Every room has a plate, a wall or a named hole
  over it. A patch below 1 % of the room area is not reported.
- Every hole in a floor is listed with its reason and with the rooms it takes floor from.
  The reasons are: a stair from the storey below, a lift from the storey below, a cell marked with a cross.
- Each stair, as the model holds it, has the drawn number of risers in equal steps.
  Each step touches the step under it. The first step stands at its own floor. The top step meets the next floor.
  Nothing of a stair is cut away by the floor above it.

### 5. The IFC file, read back

The file is read back with the geometry kernel of ifcopenshell (OpenCascade), not with the code that wrote it.

- The number of elements, and the volume per storey and kind, against the model.
- The plan cut of the file against the plan cut of the model, on each storey.
- Each extruded outline in the file is a valid outline of an area.
- Each opening cuts a wall. Each door and window fills an opening and lies in it.
- The schema validation of ifcopenshell.

### Result and gates

Each thing that the reader noted or that a check found is a note with a severity:

| Severity | Meaning | Examples |
|---|---|---|
| `fail` | The model or the IFC file is wrong. `plan2bim build` exits with 1. | Solids that overlap. A room with no floor. A stair with unequal steps, or one that is not in the model. The IFC file differs from the model or breaks the schema. A gate that is not passed. |
| `warn` | A person must look at it. The build passes. With `--strict` it exits with 1. | Everything under "Noted while reading the drawings". A model face with no drawn line. A drawn line that is not in the model. An unusual riser. Area that is neither room, wall nor floor. |
| `info` | For the record. | The holes in the floors with their reasons. Each stair with its risers. Drawn lines at a stair. |

The gates are limits in the `[gates]` table of the project file:

| Gate | Fails when | Default |
|---|---|---|
| `on_line` | On a storey, a smaller share of the model outline in the plan cut lies on a drawn line. | 0.98 |
| `unexplained` | On a storey, a larger share of the gross outline is neither room, wall, opening nor floor. | 0.005 |
| `riser` | Not a failure: a riser outside this range, in mm, is a point to look at. | 150 to 190 |

`out/NAME.json` holds the `status` (`pass`, `warn` or `fail`), the number of notes of each severity, the numbers
of each storey, the registration of each block, the number of elements of each kind, and every note with its
check, its severity, its storey and its position in model coordinates.

### What the checks cannot see

- A height. The drawings hold none.
- A cell with the wrong meaning, where both meanings have the same outline in plan.
  Example: a closed cell that is a wall in the model and a duct in the building.
- Plan position of a part that the drawing does not show.

## Where drawings do not agree

The model follows the drawing of each storey. It does not repair a drawing. These points are reported
in `out/NAME.txt`:

- A block that is displaced relative to the other blocks of its drawing.
- A stair that two storeys draw at different positions. The hole in the floor holds both positions.
- Implied walls: a wall with a face that is not drawn.
- A wall that is drawn across a door. The model cuts the door through.
- Room polylines with no label, with two labels, and duplicates.

## Not in the model

- Terrain. The model shows each storey free, also a storey that is below ground.
- The roof shape. Roofs are flat plates.
- Outside areas at doors that are too large for a balcony or have no balustrade drawn. They are reported.
- Guards and handrails of the dog-leg stairs.
- Fixtures, furniture, lift cars, ceilings, wall layers, materials.

## Tools

| Tool | Used for | Needed |
|---|---|---|
| LibreDWG `dwg2dxf` 0.14 | DWG to DXF. | For DWG only. A DXF from another converter, for example the ODA File Converter, is read as it is. |
| ezdxf | Reads the DXF. | Yes. |
| shapely 2 (GEOS) | Planar arrangement, all plan geometry, validity. | Yes. This is the core. |
| numpy, scipy | Coordinates, nearest-point search. | Yes. |
| manifold3d | Joins prisms into closed solids. Finds overlaps. | Yes. It guarantees closed solids. |
| ifcopenshell | Writes IFC4. Reads it back with a second geometry kernel. Validates the schema. | Yes. |
| three.js r149 | The viewer. It is copied into the HTML file. | Yes, for the viewer. |
| matplotlib | Review plots. | For review only. |
| A headless browser | Screenshots of the viewer for visual checks. | For review only. |

### Why the model is not built in a BIM authoring tool

A wall in a BIM authoring tool is usually a centre line with a thickness. Such a tool measures an imported
wall again. A wall cell that is not a rectangle cannot be kept exactly, and accuracy is lost at wall junctions.
`plan2bim` thus keeps each wall cell as drawn and writes the IFC file itself.

## Use from a program

`plan2bim/pipeline.py` holds the build as functions: `read_drawings`, `classify_storeys`, `build_model` and `run`.
The command line calls `run`. A server can call the stages one by one, for example to show the registered
drawings before the stairs are known.

The classification reads the stairs of the project file: a cell under a flight is a tread and not a wall.
A classification that was made with other stairs, or with none, is a preview. Make it again when a stair changes.

The project file is the contract. `plan2bim.project.parse` takes the same definition as a dictionary, so a
program can send it as JSON. Paths of drawings are relative to the directory that the program gives.
