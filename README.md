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
fcad api-docs [DIR]       freecad api reference for the installed build
fcad install-macro        install the rebuild macro into FreeCAD's macro dir
fcad install-git          make `git diff` on a .fcad file open the 3d diff
fcad install-skill        install/refresh the freecad-python agent skill
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
on its own; `fcad fem all` works through every case the project declares.

the bundle carries `von_mises_p95`/`von_mises_p99` beside `von_mises`. reach for
those first: the nodal maximum lands on whatever singularity the model contains
-- a clamped face, the sharp internal corner of a notch or a drilled hole --
where linear elasticity has no finite answer, so it reports the mesh rather than
the part. how badly is easy to underrate. re-solving one unchanged planter board
-- same geometry, same load, same `mesh_size`, only a different mesh seed --
moved its reported maximum from 6.2 MPa to 2191.1, a factor of **350**, while
p95 went 3.93 to 4.80 and the deflection did not move at all:

```
long_wall_slat    max   6.2 MPa  ->  2191.1 MPa    x350
                  p95   3.93     ->     4.80       +22%
                  disp  1.940 mm ->     1.940 mm   identical
```

and that is a plain prismatic board with no holes in it, so the singularity is
the clamped face alone. the shipped cantilever shows the same thing without any
reseeding -- refine `mesh_size` from 6 to 2 and its peak climbs 74.9 -> 12848 MPa
while p95 stays near 70 and the tip deflection stays at 1.05 mm. treat `max` as a
sample of the mesh, not a property of the part. read p95 as a floor on the field
stress rather than a peak, though: on a clamped model the moment peaks at the
constrained end, and those nodes are precisely what a percentile discards.

the pictures follow the same rule. `fem-render` and `fem-animate` scale the
colormap to `von_mises_p99`, not to the peak -- scaled to a 12848 MPa
singularity the beam above renders as one flat purple slab -- and the plot states
both the clamp and the true maximum, so nothing is hidden. `FCAD_FEM_VMAX` sets
the top of the scale yourself.

an animation is a **camera** crossed with a **subject**, the same vocabulary in
`animate` and `fem-animate`:

```
fcad fem-animate --subject static --camera orbit   # the useful one
fcad fem-animate --subject modes                   # just the eigenmodes
fcad animate                                       # the assembly, building itself
fcad animate --subject static                      # or just orbit the finished model
fcad animate beam --camera turntable               # a single part, level spin
fcad animate --seconds 20 --fps 24                 # longer and smoother
```

`--seconds` sets the clip length and `--fps` the frame rate, on both animators;
the frame count is their product, not a third thing to specify.

the cameras are one motion at three amplitudes: `orbit` turns and sweeps its
elevation so the top and underside come into view, `turntable` turns level, and
`fixed` holds `FCAD_ELEV`/`FCAD_AZIM`.

`--subject static --camera orbit` holds the deformed shape at peak deflection and
flies around it, which is usually what you want: a structure deflects where it is
*supported*, and the supports are underneath, so a fixed view from above points
away from the answer. holding also means every face is seen at the same
deflection and can be compared -- one flex cycle per turn makes that impossible,
so a flexing orbit runs four cycles per turn (`FCAD_FEM_CYCLES`). the camera is
`FCAD_ELEV` (elevation, or the centre of the sweep), `FCAD_AZIM` (the still's
azimuth, or the orbit's start) and `FCAD_TILT` (sweep amplitude; 0 makes an orbit
hold its elevation) in every renderer.

`fcad animate` writes `dist/assemble_<target>.{mp4,gif}`: the parts flying in one
at a time to build the model. that is the default for the assembly, because a
model building itself says more in ten seconds than a spin does; a single part
has nothing to assemble, so `fcad animate beam` spins instead. `--subject static`
orbits the finished assembly when that is what you want. it uses the part STLs
plus the placements a build records, not the assembly STL -- that one is a single
welded lump with no part boundaries left in it.

the arrival order matters more than it sounds, and fcad reads it off flags your
project already declares. `--order grounded` (the default) walks outward from the
part flagged `grounded` over what actually touches what, so nothing ever arrives
floating, and holds parts flagged `embeds` to the end -- a screw should not be
driven before the board it holds. both flags are the ones you already write:
`grounded` is what anchors the assembly, and `embeds` is what excludes fasteners
from `check`'s interference test, so a project that already models its screws
needs no extra annotation. on the fastenplates example a plain z-sort drives the
screw home between the two plates; `grounded` does not.
`--order bottom-up|top-down|declared` are the escape hatches.

`FCAD_AT_ONCE` (default 3) caps how many parts are in the air together, and 1
makes it strictly sequential -- each part landing before the next leaves. it is a
concurrency rather than a flight duration because a fixed duration does not
survive the part count: at a duration that looks right for three parts, twenty
converge twenty-deep and read as an explosion running backwards. lengthening the
clip with `--seconds` changes the pace and nothing else.

the mesh is second-order, which matters more than it sounds: the 4-node tets
FreeCAD meshes with by default are over-stiff in bending and understate deflection
and stress by ~20% at the mesh sizes a build picks, so `mesh_size` is a knob for
resolving features rather than a workaround for a stiff solve.

fcad carries no model knowledge, so the analysis inputs come from an optional
`FEM` global (or `fem=` on an explicit `Project`): a mapping `{target: case}`
(or a callable). each *case* declares the fixed faces, partial supports, loads,
self-weight, gravity direction, mesh size and mode count; faces are picked by
**geometry predicate** (`fcad.fem_select`: `min_along("z")`, `max_along("x")`,
`normal_dir(...)`, ...), never by fragile face indices. material is a FreeCAD
library card name, an fcad alias (`"steel"`, `"aluminum"`, `"wood"`, ...), a name
from your own `MATERIALS`, or an explicit `{"E","nu","rho"}` dict; set a
project-wide `MATERIAL` default and a case need not repeat it. for example, the
planter (all wood) states a floor board under soil pressure:

```python
from fcad import fem_select as fs

MATERIAL = "wood"   # project default; fcad ships the softwood card
FEM = {"floor_slat": dict(
    supports=[dict(faces=[fs.min_along("x")], fix="xyz"),   # clamp
              dict(faces=[fs.max_along("x")], fix="yz")],   # roller
    loads=[dict(kind="pressure", faces=[fs.max_along("z")], magnitude=0.0095)],
    self_weight=True)}
```

`fixed` clamps a face outright. `supports` restrains only the axes named in
`fix`, which is what you want for a beam: a fully fixed face cannot rotate, so
clamping both ends of one reads exactly 5x stiffer than a member resting on its
bearings, and a third light on peak moment. face selectors cannot isolate an
edge, so a true simple support is still out of reach -- clamp one end, roller
the other, and a uniformly loaded beam carries the right `wL^2/8` and 2.41x the
right deflection, which is 2.08x better than clamping both. all three ratios are
closed form for a UDL (`wL^4/384EI` clamped, `wL^4/185EI` clamped-and-rollered,
`5wL^4/384EI` on bearings) and depend on neither section nor span, so they are
worth checking a model against. `gravity` is a direction in the
part's own stock frame, which is the frame a single part solves in: a member the
assembly stands on edge does not see `-Z` as down.

`undrilled=True` builds every part from its 2d profile instead of its real
solid, dropping the fastener and drainage holes. that is usually the difference
between a whole-assembly model solving and not: gmsh sizes elements from
curvature, so a 4 mm pilot hole pulls the local element size to about a
millimetre however coarse `mesh_size` is, and a fastened assembly can spend
every node resolving fastener holes and never finish meshing. `mesh_curvature`
and `mesh_min` tune that directly when you want the holes but less of them --
though note that a hole smaller than the resulting element size cannot be meshed
at all, and gmsh then returns nothing rather than a coarse hole.

be honest about what an assembly solve means: fcad fuses the parts into one
bonded body, so every joint becomes a weld. on the planter that reads about ten
times stiffer than the real screwed frame -- 0.29 mm of deflection against the
2.77 mm its worst single member shows under the same load. it is a picture of
load flow, not a number to size against.

a solve will not take the machine with it. CalculiX factors the stiffness matrix
directly, so its memory grows as about nodes^(4/3) -- halving `mesh_size` costs
ten times the RAM -- and an unbounded run reaches the OOM killer several minutes
in, having driven everything else into reclaim first. so fcad estimates the solve
from the node count and refuses one that will not fit *before* CalculiX starts:

```
$ fcad fem floor_slat
fcad fem: 'floor_slat' would need about 21G to solve and 9.58G is available
(551204 nodes at mesh_size 8). CalculiX factors the stiffness matrix directly,
so memory grows as nodes^(4/3) and halving mesh_size costs about ten times the
RAM: raise mesh_size; set mesh_curvature/mesh_min if drilled holes are driving
the element count, since gmsh refines on curvature independently of mesh_size;
or undrilled=True to drop the holes. or raise the ceiling with FCAD_MEM=21G if
the machine really has it.
```

that takes seconds rather than the four minutes the solve would have spent dying.
gmsh is bounded the other way, by wall clock (`FCAD_FEM_MESH_TIMEOUT`, default
900s), since a mesh chasing curvature through a few dozen fastener holes runs for
a quarter of an hour at gigabytes and produces nothing. `FCAD_MEM` sets the
ceiling for both -- it is also installed as an rlimit on every child, which the
mesher and solver inherit, so a runaway hits a wall of its own instead of the
kernel's. the estimate is a fit, so raise the ceiling if your machine really has
the memory; what fcad will not do is find out by being killed.

solving the same design twice gives the same numbers, which took pinning both
tools to one thread. gmsh's parallel mesher reseeds (four identical runs of one
358k-node mesh gave four node counts), and multithreaded CalculiX is worse than
unrepeatable -- ten runs of a single byte-identical input returned four tip
deflections spanning 6.5%, the low ones 6.4% under a closed form the
single-threaded run matched to 0.25%. that costs ~15% of the mesh and 1.2-1.6x of
the solve; `FCAD_FEM_MESH_THREADS` and `FCAD_FEM_THREADS` take it back if you
would rather have the wall clock than the answer.

without a `FEM` descriptor a target still solves under a default (fix the base,
self-weight, a steel card). needs `gmsh` + `ccx` on `PATH`.

## api reference for your FreeCAD

```
fcad api-docs             # -> dist/api/
fcad api-docs ~/notes/fc  # or anywhere you like
```

writes five markdown files describing the FreeCAD **you have installed**: every
TypeId `doc.addObject()` accepts, each workbench's `make*` factories with their
call signatures, and property tables giving each property's name, type, default
and enum values. it documents the toolchain rather than a model, so it needs no
project and touches nothing you have built.

the point is that nothing in it is remembered or transcribed. real objects are
created and their `PropertiesList` / `getTypeIdOfProperty` /
`getEnumerationsOfProperty` read back, so the answer matches your build by
construction. that is worth having because the usual sources quietly do not: the
wiki documents the *gui* and often never names the property behind a checkbox -
a displacement constraint's per-axis freedoms are `xFree` / `yFree` / `zFree`,
which appear nowhere in 2600 pages of it - and remembered api knowledge rots as
names move between releases (`Support` -> `AttachmentSupport`).

useful when writing a project's `compute`, and useful to point an llm/agent at
instead of letting it guess property names.

### as an agent skill

```
fcad install-skill        # -> ~/.claude/skills/freecad-python/
```

installs the skill fcad ships: scripting rules and traps, 52 curated pages of the
FreeCAD wiki (CC0), and the `api/` reference above generated for *your* FreeCAD.
that last part is why the skill cannot just be committed somewhere complete -
half of it only exists once it meets the machine it runs on.

the wiki pages and `api/` answer different questions, which is why both are
there: `api/` says a property exists, what type it is and what it accepts; the
wiki says what it *means* and how it is normally used - the FeaturePython
lifecycle, attachment, topological traversal, constraint construction. the subset
is deliberate. the full export is 2630 pages and 22 MB, of which 599 are stubs
and 926 are gui pages with no python in them; the 52 kept are ~2% of the files
and carry essentially all of the scripting value.

re-run it after a FreeCAD upgrade. it records which build the reference
describes and regenerates only when that (or the shipped `SKILL.md`) has
changed, so the common case costs a tenth of a second and says so; `--force`
regenerates regardless. it overwrites only what it ships and never deletes, so
if you clone the full wiki export in beside it your extra pages survive.

## architecture

see [DESIGN.md](DESIGN.md) for how one package drives three interpreters, and
[CONTRIBUTING.md](CONTRIBUTING.md) for developing and testing.
