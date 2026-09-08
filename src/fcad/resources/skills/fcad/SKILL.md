---
name: fcad
description: the fcad instrumentation package and its `fcad` cli - the project contract (PARAMS + compute, fcad.Part, PARAM_META/FEM/MATERIAL/STOCK), build targets, dist/ layout, check/fem/cutlist/diff/animate. use whenever a `.fcad` file,  exposing PARAMS+compute, the `fcad` command  - including "add a part", "add a parameter", "why is check failing", "run the fem", "what lumber do i buy", "animate the assembly". this covers fcad's own contract only, not the FreeCAD API itself.
---

# fcad

fcad is reusable instrumentation that drives FreeCAD to build, export, validate
and inspect a parametric, code-defined model. it carries **zero model knowledge**:
a project supplies geometry and parameters through a single `Project` descriptor,
and fcad infers the rest.

**scope.** this skill is fcad's contract, cli and conventions. it deliberately
does not cover the FreeCAD api. the FreeCAD 1.1.1 gotchas fcad has already
absorbed live in `fcad/CONTRIBUTING.md`; read that before rediscovering one.

## the shape of the thing

one package, one cli, three interpreters. the cli resolves configuration once,
exports it into the environment, and picks the interpreter per command so the
user never does:

- **headless** (`freecadcmd`) - build, check, precommit, fem. runs
  `fcad/freecad/_entry.py <command>`, which puts the package on sys.path inside
  FreeCAD and dispatches.
- **gui** (`freecad`) - view, view-parts, pdf, diff-open.
- **in-process** (the cli's own python) - render, animate, fem-render,
  fem-animate, and diff's worktree orchestration.

## a project is PARAMS + compute

a whole project can be one file: a directory's `project.py`, or a standalone
**`.fcad`** file (python by another name, loaded by path since `.fcad` is not a
registered source suffix).

```python
import fcad, Part, FreeCAD as App

PARAMS = {"length": 100.0, "width": 60.0, "height": 20.0}

def compute(p):
    l, w, h = p["length"], p["width"], p["height"]
    return [fcad.Part("block", placements=[App.Placement()],
                      solid=lambda: Part.makeBox(l, w, h, App.Vector(-l/2, -w/2, -h/2)))]
```

`compute` returns a bare list of parts, or a dict `{"specs": [...], ...}` when it
wants to hand derived values along. required: `PARAMS` + `compute`. everything
else is optional refinement:

| global | what it does |
| --- | --- |
| `PARAM_META` | per-param `group`, enum `choices`, explicit `type`, `min`, `max` |
| `FEM` | per-target analysis cases (below) |
| `MATERIAL` / `MATERIALS` | project-wide fem material default / named registry |
| `STOCK` | buyable stock lengths per bom profile (below) |
| `FROM_SPEC` / `PROFILE` | realize a spec that is not an `fcad.Part` |
| `NAME`, `SCHEMA`, `ENUM_CHOICES` | override the inferred name/varset/dropdowns |

each is read as the UPPER_CASE constant or the lowercase `Project` kwarg name,
so `PARAMS` + `def compute` is idiomatic. an explicit
`PROJECT = fcad.Project(...)` is always supported and never removed.

### fcad.Part

```
fcad.Part(name, placements, solid=None, profile2d=None, profile="",
          holes=(), fastener_holes=(), length=None, grounded=False, embeds=False)
```

- `solid` - a **thunk** returning the BREP solid in the part's own unplaced
  frame. lazy on purpose: the bom, cutlist and schema passes never realize it,
  so keep the booleans inside the thunk.
- `profile2d` - `(points, thickness)` for the defining sketch. `check` demands it
  come out **fully constrained**, so emit one closed outline rather than a shape
  minus cutters (the planter's notched floor board is a single octagon for this).
- `profile` - the bom label (e.g. a nominal lumber name); also the `STOCK` key.
- `holes` - `(cx, cy, dia)` circles that get dimensioned on the drawing.
  `fastener_holes` are sketch-only, so screw holes stay out of the dimensions.
- `length` - the bom length; defaults to the solid's bbox **X-extent**, so state
  it explicitly whenever stock length is not the X-extent.
- `grounded` - anchors the assembly. nothing flagged means the first part is
  auto-grounded, so a minimal project still passes `check`.
- `embeds` - excluded from the interference check and the fem fuse. anything that
  intentionally sinks into other solids (screws) needs this or `check` fails.

`grounded` and `embeds` do double duty: they also order the assembly animation,
which walks outward from the grounded part over what touches what and holds the
`embeds` parts to the end. so flagging them correctly is what makes
`fcad animate` read as construction - unflagged, it falls back to a z-sort and
drives screws home midway up the stack. no separate annotation exists or is
needed for this.

a project may return its own duck-typed spec instead, exposing the same surface.

### varset schema inference

the gui parameter panel is inferred from each default's python type: `bool` ->
PropertyBool, `int` -> PropertyInteger, `float` -> **PropertyLength** (fcad models
mm geometry), `str` -> PropertyString, `str` + declared `choices` ->
PropertyEnumeration (a dropdown). override per param via `PARAM_META["type"]`,
which accepts the aliases `length float integer int bool string enumeration enum
angle`.

## the cli

configuration resolves flag -> env var -> default:

| flag | env | default |
| --- | --- | --- |
| `-p/--project` | `FCAD_PROJECT` | cwd; a dir with `project.py`, or a `.fcad` file (a dir holding a sole `*.fcad` resolves to it, but `project.py` wins) |
| `-n/--name` | `FCAD_NAME` | dir basename, or the `.fcad` stem |
| `-d/--dist` | `FCAD_DIST` | `<root>/dist` |
| `--freecad` / `--freecad-gui` | `FREECAD` / `FREECAD_GUI` | `freecadcmd` / `freecad` |

run from the project directory and none of it needs overriding.

```
fcad build [TARGET ...]   no target = all; one freecadcmd pass per token
fcad check                interference + constrained components + constrained sketches
fcad precommit            build all, then check
fcad view [parts]         gui; --part NAME for one part
fcad render|animate [T]   offscreen png / mp4+gif from a built stl
fcad fem [T]              headless gmsh + CalculiX; --modal / --modes K
fcad fem-render|fem-animate [T]
fcad diff|diff-build|diff-open [T]
fcad api-docs [DIR] | install-skill    reference for the installed FreeCAD
fcad pdf | clean | info | install-macro | install-git | help [COMMAND]
```

`TARGET` is `assembly` (default) or a part name; `fcad fem all` works through
every case the project declares.

**animating** is a camera crossed with a subject, shared by both animators.
`--camera orbit|turntable|fixed` (orbit sweeps its elevation so the underside
comes into view; turntable spins level). `fcad animate` defaults to
`--subject assemble`, the model building itself part by part; `--subject static`
orbits it whole, and a part target spins on its own since it has nothing to
assemble. `fcad fem-animate --subject all|flex|static|modes`, where `static`
holds peak deflection and lets the camera work - usually the clip worth having.
length is derived from the content (an assembly of twenty parts arriving singly
is a longer film than three), so `--speed` multiplies it rather than `--seconds`
setting it.

**build targets:** `parts assembly step stl svg dxf drawings sketches bom
cutlist`. multiple tokens union. stage-bound tokens keep their meaning:
`sketches` is parts-only, `bom` and `cutlist` are assembly-only (only the
assembly knows how many of a part there are).

**what `check` asserts** (R3.1) - all three must hold before a commit:
1. no structural solids interpenetrate (`embeds` parts excluded)
2. every assembly component is grounded or jointed
3. every defining sketch is fully constrained

## what lands in dist/

```
parts/<name>.{FCStd,step,stl,svg,dxf}
drawings/<name>.dxf   drawings/assembly.dxf     dimensioned techdraw sheets
sketches/<name>.{svg,dxf}                       defining sketches, parts only
<name>.{FCStd,step,stl,svg,dxf}                 the linked assembly + exports
<name>-bom.csv          part,qty,profile,length_mm
<name>-cutlist.csv      profile,pattern,stock_mm,boards,cut_mm,per_board,offcut_mm
<name>-placements.json  per-instance placements, for the 3d diff
<name>-parts.json       per-part grounded/embeds, for the assembly animation
render_<target>.png   assemble_<target>.{mp4,gif}   spin_<target>.{mp4,gif}
<target>.fem.{FCStd,npz}   fem_<target>.png   fem_<target>.{mp4,gif}
<target>.diff.FCStd
```

the two json files exist because a renderer runs outside FreeCAD and cannot call
`compute`: the diff needs both revisions' placements, and the animator needs the
flags. every assembly build writes them whatever formats were asked for.

the assembly `.FCStd` is a real Assembly-workbench assembly: each instance is an
`App::Link` into its part file, grounded or Fixed-jointed and solved.

## FEM

`FEM` is `{target: case}` (with an optional `"__default__"`) or a callable
`(target, shape, values) -> case`. a case:

| key | meaning |
| --- | --- |
| `fixed` | list of face selectors, clamping the face outright (default: `min_along("z")`) |
| `supports` | `[dict(faces=[...], fix="yz")]` - restrain only the named axes |
| `loads` | list of load dicts |
| `self_weight` | default `not loads`; `gravity` defaults to `(0,0,-1)` |
| `mesh_size` | clamped to 1..25 mm; default bbox diagonal / 20 |
| `mesh_min` / `mesh_curvature` | element floor, and how hard gmsh chases curvature (12/turn) |
| `undrilled` | build parts from their 2d profile, dropping the holes |
| `modes` | eigenmode count |
| `material` | library card name, an fcad alias (`steel`/`aluminum`/`wood`/...), a `MATERIALS` name, or `{E, nu, rho}`; falls back to `MATERIAL` |

`fixed` is a clamp: every node pinned, so the face cannot rotate. clamping both
ends of a member reads exactly 5x stiff against one resting on its bearings, and
a third light on peak moment. `supports` with an axis left free lets the end
rotate - clamp one end and roller the other and a uniformly loaded beam carries
the right `wL^2/8`. all the closed forms here are span- and section-independent,
so a beam that misses them has a bug rather than a shape.

`undrilled` is usually what makes a whole-assembly solve possible at all: gmsh
sizes elements from curvature, so a 4 mm pilot hole demands ~1 mm elements
however coarse `mesh_size` is, and a fastened assembly spends every node on
fastener holes and never finishes meshing. turning `mesh_curvature` *down* is not
the equivalent workaround - below the default a small hole cannot be meshed at
all and gmsh returns nothing rather than a coarser hole.

a load: `kind` (`"force"` default, or `"pressure"`), `faces`, `magnitude`,
`direction` (force only, default `"-z"`), `reversed` (pressure only).
**units: pressure magnitude is MPa, force magnitude is N** - fcad passes quantity
strings so FreeCAD's internal mN / mN-per-mm2 never leak.

faces are chosen by geometry predicate, never by a `"FaceN"` index that reorders
whenever geometry changes. from `fcad.fem_select`:

```python
from fcad import fem_select as fs
fs.min_along("z")   fs.max_along("x")    # extreme face centers along an axis
fs.normal_dir("-z", tol_deg=15)          # outward normal within a cone
fs.in_box(lo, hi)   fs.largest(n=1)
fs.all_of(...)      fs.any_of(...)       fs.invert(sel)
```

shorthands `("z", "min")` / `("x", "max")` and a literal `"Face6"` escape hatch
are accepted too. absent a `FEM` descriptor a target still solves under a default
(fix the base, self-weight, steel).

the npz carries `von_mises_p95`/`p99` beside `von_mises` - **read those, not the
max** (see the traps below). the mesh is 2nd-order; a solve is refused before
CalculiX starts if the node count will not fit memory, with the node count and
the ceiling named, and `FCAD_MEM` raises it. results are reproducible: both tools
are pinned to one thread, which `FCAD_FEM_THREADS` / `FCAD_FEM_MESH_THREADS`
undo at the cost of that guarantee.

needs `gmsh` and `ccx` on PATH.

## STOCK and the cut list

`fcad build cutlist` packs each bom profile's pieces into buyable stock lengths
and writes `dist/<name>-cutlist.csv` plus a summary line per profile.

```python
FT = 304.8
STOCK = {"2x6": [8*FT, 10*FT, 12*FT, 16*FT], "2x4": [8*FT, 10*FT, 12*FT]}
```

keys are the same `profile` label the bom uses; a bare list applies to every
profile, and `"*"` is the catch-all entry in the dict form. **membership matters**:
only a profile declared here is planned at all, which is how fasteners and bought
parts stay out of it.

override per invocation without touching the project:

```
fcad build cutlist --stock 10ft,12ft --kerf 3.2 --trim 12 --objective boards
fcad build cutlist --stock 2x6=8ft,12ft        # scoped to one profile
```

`--stock` values are mm unless suffixed (`mm cm m in ft`), `;`-separated,
repeatable. a bare list re-lengths every declared profile; a `profile=` entry
also opts that profile in. `--kerf` (default 3.0) is charged between adjacent
cuts, `--trim` (default 0) off each board.

the plan is an exact dp while the search fits a fixed budget and
first-fit-decreasing above it; the summary marks the latter `(greedy)`, so a plan
never claims an optimum it did not prove. a piece longer than every stock length
is warned about, never dropped.

## editing parameters in the gui

part and hole **counts** are parametric, so a native expression recompute cannot
add or remove objects. hence the macro: `fcad install-macro` once, then edit the
`Parameters` VarSet in the open document and run `fcad_rebuild` from FreeCAD's
Macro menu.

## traps that are fcad's own

- **never `.translate()` to center a solid.** bake centering into the geometry
  (`Part.makeBox(l, w, t, App.Vector(-l/2, -w/2, -t/2))`). translate sets the
  shape's Placement, which the assembly clobbers when it assigns the instance
  Placement - parts end up offset by half their size and rotating about the wrong
  point.
- `solid` is a thunk. work done outside it runs on every metadata-only pass.
- a part with `profile2d` must yield a fully constrainable closed outline, or
  `check` fails on it.
- forget `embeds=True` on a fastener and `check` reports it as interference.
- `length` defaults to the bbox X-extent; mitered or rotated parts usually need
  it stated.
- **the peak von Mises is a property of the mesh, not of the part.** it lands on
  whatever singularity the model contains - a clamped face, the sharp corner of a
  notch - where linear elasticity has no finite answer, so it simply grows as the
  mesh is refined. the shipped cantilever goes from 74.9 to 12848 MPa between
  `mesh_size` 6 and 2 while its p95 stays near 70 and its deflection does not
  move. quote `von_mises_p95`, and treat `max` as a sample. (the caveat on p95:
  it is a floor on the field stress, since on a clamped model the moment peaks at
  the constrained end and a percentile discards exactly those nodes.)
- **the assembly fem target is one welded body.** fcad fuses the structural
  solids, so every butt joint becomes a weld and the box reads ~10x stiffer than
  the screwed frame it models. it is a picture of load flow, not a number to size
  against - size against the per-part cases.
- a fem case tuned before 2nd-order meshing will be far too fine: the same
  `mesh_size` now carries several times the nodes. if a solve is refused for
  memory, raise `mesh_size` rather than reaching for `FCAD_MEM`.
