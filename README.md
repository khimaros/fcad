# fcad

reusable FreeCAD build + instrumentation for parametric, code-defined models.

fcad drives FreeCAD (python on the OpenCASCADE BREP kernel) to build a model into
real solids, link them into a true Assembly-workbench assembly, export neutral
formats (STEP/STL/SVG/DXF), produce a bom, a cut list of what stock to buy, and
dimensioned TechDraw drawings in DXF and PDF (a projection-aligned
top/front/right + isometric sheet with a filled title block), render offscreen
PNGs, animate a turntable, and 3d-diff against git HEAD. it knows **nothing**
about any particular model: a project supplies its geometry and parameters
through a single `Project` descriptor.

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
explicit property `type`), `FEM`, `MATERIAL`/`MATERIALS`, `STOCK` (see the cut
list below), and `from_spec`/`profile` (if your specs aren't `fcad.Part`).

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
                             parts assembly step stl svg dxf drawings sketches
                             bom cutlist
                          cutlist knobs: --stock/--kerf/--trim/--objective
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
fcad install-git          make `git diff` on a .fcad file open the 3d diff
fcad help [COMMAND]       show usage (top-level, or for one command)
```

`TARGET` is `assembly` (default) or a part name (e.g. `corner_post`). the 3d diff
is split into a compute step and a view step: `diff-build` bakes the green/red/grey
split to `dist/<target>.diff.FCStd`, `diff-open` opens that instantly, and `diff`
chains the two. see a project's Makefile (e.g. the `planter` repo) for canonical,
incremental invocations.

### a typical session

from your project directory (where `project.py` lives):

```
fcad build           # everything into dist/: parts, assembly, exports, bom
fcad check           # no interference; every component + sketch constrained
fcad view            # eyeball the assembly in the gui
fcad diff            # what changed vs git HEAD, in 3d
```

the built `.FCStd` files open ready to look at, whether through `fcad view` or a
plain double-click in FreeCAD: the part solids, the assembly's components and the
assembly itself are visible, the defining sketches and origin geometry are not,
and the camera is already an isometric fit of the model. the build bakes that
view state itself, with no display involved.

while iterating, `fcad build <TARGET>` rebuilds a single artifact (e.g.
`fcad build assembly`) for a fast loop. to tweak parameters live in the gui,
`fcad install-macro` once, then run `fcad_rebuild` from FreeCAD's Macro menu: it
reads the open document's `Parameters` panel and regenerates it. that indirection
is needed because part and hole **counts** are parametric, so a plain recompute
cannot add or remove objects.

## 3d diff (what changed vs git HEAD)

`fcad diff` builds the committed design in a throwaway `git worktree` and shows
the two side by side as one model in three toggleable layers: **green** material
this revision adds, **red** material it removes, **grey** everything untouched.

the diff is per part. each part type is diffed against its own previous version
once, in its own frame, and the result is then placed at each of its instances -
a transform, not a boolean. so the cost tracks the number of part types you
*changed*, not the number of parts in the model, and a revision that only moves
things around or changes a quantity costs no booleans at all. on the planter
(190 instances, 11 part types, 3 of them changed) the whole of `fcad diff-build`
is about 11s, most of it rebuilding the HEAD geometry.

because a part is only ever compared against itself, the layers answer "what
changed about each part" rather than "what matter sits here now": if a screw
moves out of a space a board grows into, you see the screw in red and the board
in green in the same place, which is the honest per-part answer.

pairing instances needs both revisions' placements, and the diff cannot ask the
project for them twice (the two answers live in two different revisions of your
code), so every build records them in `dist/<name>-placements.json`. build before
you diff; `fcad diff` says so if that file is missing.

### from `git diff`

if you commit `dist/`, `git diff` on a built `.FCStd` says "Binary files differ"
and tells you nothing. `fcad install-git` registers fcad as this repo's diff
driver for those documents, so it shows you the model instead:

```
fcad install-git
git diff                                    # 3d, not "binary files differ"
git diff HEAD~3 -- dist/parts/corner_post.FCStd
```

git hands the driver both revisions as files, so this compares whatever you asked
git to compare — a branch, a tag, three commits back — and builds nothing, since
both sides are already-built documents. a file missing on one side (`/dev/null`
to git) reads as wholly added, or removed.

git diffs one file at a time, and the two kinds of document record different
things, so you get one 3d view per changed file:

- a **part** document holds its own solid, so it diffs as **geometry** — what
  changed about that part's shape;
- an **assembly** document holds no geometry at all, only links into the part
  files, so it diffs as **placements** — instances added, removed or moved. that
  is exactly what the file records; a part's shape changing is a change to the
  part file, which git diffs separately.

your `.fcad` source is deliberately *not* bound: it is python, it diffs perfectly
well as text, and that is what we ask forges to render it as. use `fcad diff` for
the whole-design view.

`install-git` also writes a tracked `.gitattributes` marking `.fcad` as Python, so
GitHub and GitLab render your design as source instead of plain text and count it
in the repo's language stats:

```
*.fcad linguist-language=Python gitlab-language=python
```

the two attributes land in different files, and which goes where is forced, not a
preference. git will not run a command a tracked file names (a clone would then
execute code it shipped), so the diff driver has to be local: `.git/config` plus
`.git/info/attributes`, with teammates running `fcad install-git` once each. a
forge only ever reads committed files, so the language hint has to be the tracked
`.gitattributes` — commit it. that file is the only thing in your working tree
`install-git` touches. undo the local half with
`git config --local --unset diff.fcad.command`.

## cut list (what to buy)

the bom says how many pieces of what length. `fcad build cutlist` says what to
buy: it packs each profile's pieces into the stock lengths a supplier actually
sells, charging a saw kerf between adjacent cuts and an optional trim allowance
off each board, and writes `dist/<name>-cutlist.csv` (per cut pattern: the stock
length, how many boards take it, the pieces cut from each, the offcut).

which lengths exist is a fact about a supplier, not about fcad, so it comes from
an optional `STOCK` global: a `{profile: [lengths mm]}` map keyed by the same
`profile` label the bom uses, or one list for every profile. only a profile
declared there is planned, so fasteners and bought parts stay out of it.

```python
FT = 304.8
STOCK = {"2x6": [8*FT, 10*FT, 12*FT, 16*FT], "2x4": [8*FT, 10*FT, 12*FT]}
```

```
$ fcad build cutlist
  4x4: buy 1 x 3657.6 mm = 1 board(s), 15.3% waste
  2x6: buy 5 x 4876.8 + 2 x 3657.6 + 2 x 2438.4 mm = 9 board(s), 1.3% waste
  2x4: buy 1 x 3657.6 mm = 1 board(s), 9.6% waste
```

override any of it per invocation, no edit to the project: `--stock` (either
`8ft,10ft,12ft` for every declared profile or `2x6=8ft,12ft` to scope it, mm when
unsuffixed, repeatable), `--kerf`, `--trim`, and `--objective length|boards`. so
comparing what your yard racks is one command: `fcad build cutlist --stock
10ft,12ft --kerf 3.2 --trim 12`.

the plan is exact where the search is small enough to prove it and
first-fit-decreasing above that; the summary marks a greedy plan `(greedy)` so it
never claims an optimum it did not find. a piece longer than every stock length
is reported as a warning, not dropped.

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
