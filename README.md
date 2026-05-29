# fcad

reusable FreeCAD build + instrumentation for parametric, code-defined models.

fcad drives FreeCAD (python on the OpenCASCADE BREP kernel) to build a model into
real solids, link them into a true Assembly-workbench assembly, export neutral
formats (STEP/STL/SVG/DXF), produce a bom and dimensioned TechDraw drawings in
DXF and PDF (a projection-aligned top/front/right + isometric sheet with a filled
title block), render offscreen PNGs, animate a turntable, and 3d-diff against git
HEAD. it knows **nothing** about any particular model: a project supplies its
geometry and parameters through a single `Project` descriptor.

## install

fcad is a uv-installable python package with a single `fcad` console script:

```
uv tool install -e .            # core (build/validate/inspect/diff)
uv tool install -e ".[render]"  # + the matplotlib renderer (render/animate/fem-*)
```

external requirements: FreeCAD 1.1.x on `PATH` (with the bundled Assembly,
TechDraw and FEM workbenches); `ffmpeg` for `animate`/`fem-animate`; `gmsh` and
`ccx` (CalculiX) on `PATH` for `fem`.

## using it from a project

your project lives in its own repo and depends on fcad. all it must supply is the
parameter defaults and a `compute` that turns parameter values into parts; fcad
infers the rest. the whole thing can be one file: either a `project.py` in a
directory, or a standalone **`.fcad`** file (python by another name), named
directly as `fcad -p planter.fcad build` or found by a plain `fcad build` in a
directory holding a single `.fcad`. for example, `block.fcad`:

```python
import fcad, Part, FreeCAD as App

PARAMS = {"length": 100.0, "width": 60.0, "height": 20.0}

def compute(p):
    box = Part.makeBox(p["length"], p["width"], p["height"])
    return [fcad.Part("block", solid=lambda: box, placements=[App.Placement()])]
```

`fcad build` then produces the part files, the assembly, the drawings and the
bom (a `.fcad` file's output stem is its name, `block`, and `dist/` lands beside
it). fcad infers the varset (gui parameter panel) schema from each default's
python type (floats are lengths in mm), computes each part's `qty`/`length`, and
anchors the assembly's first part when none is flagged. refine only what you
need with optional globals: `PARAM_META` (per-param group, enum `choices`, or an
explicit property `type`), `FEM`, `MATERIAL`/`MATERIALS`, and `from_spec`/
`profile` (if your specs aren't `fcad.Part`).

`fcad.Part(name, placements, solid=..., profile2d=..., profile=..., holes=...,
grounded=..., embeds=...)` is the ready-made part: `solid` is a thunk returning
its BREP solid, `profile2d` an optional `(points, thickness)` for the defining
sketch, `profile` the bom label, `grounded` anchors it in the assembly, `embeds`
excludes a part that sinks into others (e.g. screws) from the interference
check. a project may instead return its own duck-typed spec exposing the same
surface, and/or hand fcad an explicit `PROJECT = fcad.Project(...)`; the explicit
form is fully supported.

## the `fcad` cli

`fcad` is the single entrypoint; it routes each command to the interpreter it
needs (headless `freecadcmd`, the `freecad` gui, or in-process python) so you
never choose. configuration is resolved from flags, then the environment, then
sane defaults: `--project` (`FCAD_PROJECT`, a dir or a `.fcad` file, default the
current dir), `--name` (`FCAD_NAME`, default the dir basename or the `.fcad`
stem), `--dist` (`FCAD_DIST`, default `<root>/dist`), and `--freecad`/
`--freecad-gui` (`FREECAD`/`FREECAD_GUI`). run from the project directory and the
defaults usually need no overrides.

```
fcad build [TARGET ...]   build/export into dist/ (no target = all); TARGET:
                             parts assembly step stl svg dxf drawings sketches bom
fcad check                interference + every component constrained
                             + every sketch fully constrained
fcad precommit            build all, then check
fcad view [parts]         open the assembly in the gui (or `view parts` for the
                             part files; --part NAME for a single one)
fcad render [TARGET]      offscreen png of a built stl
fcad animate [TARGET]     turntable mp4 + gif of a built stl
fcad fem [TARGET]         solve FEM (von Mises + displacement); --modal/--modes K
fcad fem-render [TARGET]  png of a solved FEM result (deformed, colored by stress)
fcad fem-animate [TARGET] deformation sweep + per-mode mp4/gif of a FEM result
fcad diff [TARGET]        3d diff vs git HEAD (compute headless, then open)
fcad diff-build [TARGET]  compute + save the diff headless (the slow part)
fcad diff-open [TARGET]   open a precomputed diff in the gui (instant)
fcad pdf                  dimensioned techdraw pdfs
fcad clean                remove dist/
fcad info                 print the resolved configuration
fcad install-macro        install the rebuild macro into FreeCAD's macro dir
fcad help [COMMAND]       show usage (top-level, or for one command)
```

`TARGET` is `assembly` (default) or a part name (e.g. `corner_post`). the 3d diff
is split because computing it is slow: `diff-build` bakes the green/red/grey split
to `dist/<target>.diff.FCStd` (run it in the background for a big assembly),
`diff-open` opens that instantly, and `diff` chains the two. see a project's
Makefile (e.g. the `planter` repo) for canonical, incremental invocations.

### a typical session

from your project directory (where `project.py` lives):

```
fcad build           # everything into dist/: parts, assembly, exports, bom
fcad check           # no interference; every component + sketch constrained
fcad view            # eyeball the assembly in the gui
fcad diff            # what changed vs git HEAD, in 3d
```

while iterating, `fcad build <TARGET>` rebuilds a single artifact (e.g.
`fcad build assembly`) for a fast loop. to tweak parameters live in the gui,
`fcad install-macro` once, then run `fcad_rebuild` from FreeCAD's Macro menu: it
reads the open document's `Parameters` panel and regenerates it. that indirection
is needed because part and hole **counts** are parametric, so a plain recompute
cannot add or remove objects.

## FEM (stress + modal)

`fcad fem [TARGET]` meshes a built target (gmsh) and solves it with CalculiX
headlessly, writing the analysis doc plus a numpy result bundle
(`dist/<target>.fem.{FCStd,npz}`). `fcad fem-render` then draws the deformed
surface colored by von Mises stress, and `fcad fem-animate` writes a deformation
sweep plus one animation per eigenmode (`--modal`/`--modes K`). the `assembly`
target fuses the structural solids into one bonded body; a single part is solved
on its own.

fcad carries no model knowledge, so the analysis inputs come from an optional
`FEM` global (or `fem=` on an explicit `Project`): a mapping `{target: case}`
(or a callable). each *case* declares the fixed faces, loads, self-weight, mesh
size and mode count; faces are picked by **geometry predicate**
(`fcad.fem_select`: `min_along("z")`, `max_along("x")`, `normal_dir(...)`, ...),
never by fragile face indices. material is a FreeCAD library card name, an fcad
alias (`"steel"`, `"aluminum"`, `"wood"`, ...), a name from your own `MATERIALS`,
or an explicit `{"E","nu","rho"}` dict; set a project-wide `MATERIAL` default and
a case need not repeat it. for example, the planter (all wood) states a floor
board fixed at both ends under soil pressure:

```python
from fcad import fem_select as fs

MATERIAL = "wood"   # project default; fcad ships the softwood card
FEM = {"floor_slat": dict(
    fixed=[fs.min_along("x"), fs.max_along("x")],
    loads=[dict(kind="pressure", faces=[fs.max_along("z")], magnitude=0.02)],
    modes=3)}
```

without a `FEM` descriptor a target still solves under a default (fix the base,
self-weight, a steel card). needs `gmsh` + `ccx` on `PATH`.

## architecture

see [DESIGN.md](DESIGN.md) for how one package drives three interpreters, and
[CONTRIBUTING.md](CONTRIBUTING.md) for developing and testing.
