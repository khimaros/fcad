---
name: fcad
description: the fcad instrumentation package and its `fcad` cli - the project contract (PARAMS + compute, fcad.PartSpec, PARAM_META/FEM/MATERIAL/STOCK), build targets, dist/ layout, check/fem/cutlist/diff/animate. use whenever a `.fcad` file,  exposing PARAMS+compute, the `fcad` command  - including "add a part", "add a parameter", "why is check failing", "run the fem", "what lumber do i buy", "animate the assembly". this covers fcad's own contract only, not the FreeCAD API itself.
---

# fcad

fcad is reusable instrumentation that drives FreeCAD to build, export, validate
and inspect a parametric, code-defined model. it carries **zero model knowledge**:
a project supplies geometry and parameters through a single `Project` descriptor,
and fcad infers the rest.

**scope.** this skill is fcad's contract, cli and conventions. it deliberately
does not cover the FreeCAD api. three things do, and all are already on disk:

- the sibling **`freecad-python`** skill, whose `api/` lists every TypeId,
  factory and property (with enum values) of the FreeCAD installed here. look a
  feature up there rather than guessing its property names.
- **`CONTRIBUTING.md`, beside this file**: the FreeCAD gotchas fcad has already
  absorbed, under "api notes". read that before rediscovering one.
- **`examples/`, beside this file**: four projects that pass `fcad precommit`
  as they stand. `fastenplates` is the minimal assembly to crib from, and
  `robotarm` exercises the full surface (`geom_select`, curved/slotted sketches,
  `MOTION`, multi-material/multi-pose `FEM`, 2D `Sheet` nesting, `sheetmetal`,
  `SubAssembly`, `from_step`, `bom_group`, `ASSEMBLE`).

**`README.md` ships beside this file** and is fcad's user documentation: the same
contract at length, plus the parts this skill only points at - the 3d diff and
its git driver, FEM tuning and its memory limits, the cut list, and a
`## migrating` section listing what has changed and what is about to. this file
is the dense version and the traps; read the README when you need the long form,
rather than assuming this one omitted something on purpose.

## the shape of the thing

one package, one cli, three interpreters. the cli resolves configuration once,
exports it into the environment, and picks the interpreter per command so the
user never does:

- **headless** (`freecadcmd`) - build, check, precommit, fem. runs
  `fcad/freecad/_entry.py <command>`, which puts the package on sys.path inside
  FreeCAD and dispatches.
- **gui** (`freecad`) - view, view-parts, pdf, diff-open, and render and
  animate's frames (which run it offscreen under `xvfb-run`, so no window
  appears).
- **in-process** (the cli's own python) - animate's planning and encoding,
  fem-render, fem-animate, and diff's worktree orchestration.

a build's own output is quiet: the cli drops the kernel's STEP banners and
progress bars, and whatever FreeCAD and its addons print while starting up, so
what is left is fcad's lines. `FCAD_VERBOSE=1` shows it all. a build prints one
line per part and one for the assembly saying what it cost and on which steps
(`part case: 11.2 s (drawing 6.0 s, dxf 2.1 s, svg 1.4 s)`), so a slow part
names itself.

## a project is PARAMS + compute

a whole project can be one file: a directory's `project.py`, or a standalone
**`.fcad`** file (python by another name, loaded by path since `.fcad` is not a
registered source suffix).

```python
import fcad
from fcad.freecad import partdesign

PARAMS = {"length": 100.0, "width": 60.0, "height": 20.0, "bore": 12.0}

def _block(doc, body, p):
    l, w = p["length"] / 2.0, p["width"] / 2.0
    partdesign.pad_and_bore(doc, body, [(-l, -w), (l, -w), (l, w), (-l, w)],
                            p["height"], [(0.0, 0.0, p["bore"])])

def compute(p):
    return [fcad.PartSpec("block", length=p["length"],
                          build=lambda doc, body: _block(doc, body, p),
                          dimension_sketches=["*_bore*"],
                          openings=["*_bore*"])]
```

**that part is a `PartDesign::Body`** - fully-constrained sketch, `Pad`, one
`PartDesign::Hole` per bore - and `build` is where you write real FreeCAD code.
`pad_and_bore` is a helper for the commonest shape, not a layer: the turned screw
in `examples/fastenplates` ignores it. see "the well-lit path" below, and reach
for `solid=` only when the part is not a body at all.

`Part` and `FreeCAD` are FreeCAD's own modules: they import by name because a
project runs inside FreeCAD, not because fcad puts them there - a project module
is loaded as ordinary python and nothing is injected into it. import the names
used rather than aliasing (`FreeCAD as App` is the wiki's habit, not a rule).
**FreeCAD's `Part` and fcad's `fcad.PartSpec` are unrelated** - the geometry kernel
against the part spec - so the `fcad.` prefix is what keeps them apart.

`compute` returns a bare list of parts, or a dict `{"specs": [...], ...}` when it
wants to hand derived values along. required: `PARAMS` + `compute`. everything
else is optional refinement:

| global | what it does |
| --- | --- |
| `PARAM_META` | per-param `group`, enum `choices`, explicit `type`, `min`, `max` |
| `FEM` | per-target analysis cases (below) |
| `MATERIAL` / `MATERIALS` | project-wide fem material default / named registry |
| `STOCK` | buyable 1D lengths or 2D `fcad.Sheet` sizes per bom profile; dict or callable `(values) -> dict` |
| `FROM_SPEC` / `PROFILE` | realize a spec that is not an `fcad.PartSpec` |
| `CONSTRAINTS` | predicates saying when the model still means what it says |
| `ASSEMBLE` | joint the assembly yourself (below) instead of Fixed-to-datum |
| `MOTION` | kinematic keyframe sweeps checked by `check` and animated by `--subject motion` |
| `NAME`, `SCHEMA`, `ENUM_CHOICES` | override the inferred name/varset/dropdowns |

each is read as the UPPER_CASE constant or the lowercase `Project` kwarg name,
so `PARAMS` + `def compute` is idiomatic. an explicit
`PROJECT = fcad.Project(...)` is always supported and never removed.

### fcad.PartSpec

```
fcad.PartSpec(name, placements=None, build=None, solid=None, profile="",
              length=None, grounded=False, embeds=False,
              dimension_sketches=(), dimension_circles=(), openings=(),
              profile2d=None, color=None, transparency=0, disjoint=False,
              rides=None, material=None, sheet_size=None, flat_pattern=None,
              cots=False, sku="", unit_cost=None, mass_g=None, bom_group="")
```

`placements` defaults to `[Placement()]` and coerces a single `Placement` to a
1-element list. `PartSpec` fails fast on passing both `build` and `solid`,
out-of-range `transparency`, or invalid `color`.

- `PartSpec.from_step(name, step_path, placements, ...)` loads a vendor STEP
  file lazily (`cots=True`), carrying `sku`/`unit_cost`/`mass_g` into the BOM
  while skipping PartDesign void and disjoint checks.
- `fcad.SubAssembly(name, specs).place(placements)` + `fcad.compose(...)`
  compose reusable multi-part modules and merge repeated child part names.
- `material` enables per-part multi-material conformal `CompSolid` assembly FEM.
- `sheet_size` / `flat_pattern` (via `fcad.sheetmetal`) feed 2D `fcad.Sheet`
  nesting (`dist/<name>-sheetlist.csv`) and `dist/sketches/<part>-flat.svg`.
- `bom_group` groups asymmetrically drilled handed twins in BOM output.
- `dimension_sketches` and `openings` accept exact sketch names or fnmatch globs
  (`*_bore*`), failing `check` if any entry matches nothing.
- `fcad.geom_select` (`edges`/`faces` with `at_x`/`at_y`/`at_z`, `along_axis`,
  `normal_dir`, `curved`, `straight`, `in_box`, `all_of`, `any_of`, `invert`)
  picks PartDesign edges/faces without hardcoded `EdgeN` indices and raises on
  unexpected counts. `partdesign.add_sketch` also accepts per-vertex corner
  radii `(x, y, r)` and angled stadium `slots`, and `pad_and_bore` defaults
  `name` to `body.Name`.

**the look, for `fcad render`:** `color` is an `(r, g, b)` of 0..1 floats and
`transparency` runs 0 (solid) to 100. give every part of a model with more than
one material a colour, and anything meant to be seen through (a crystal, a lens,
a window) a transparency around 80 - without them the render is one grey lump
and whatever sits under glass is invisible.

**what stays together, for `fcad render --explode`:** every part is a layer of
its own unless it says `rides="<part>"`, which keeps it on that part. use it
when one physical piece is several parts only so they can differ in colour or
material: the components on a board, an insert pressed into a base. a cover or
tray needs nothing: it is ranked by its closed end.

**geometry - exactly one of these:**

- `build` - `(doc, body) -> None`: your own FreeCAD code, called with a live
  document and an empty `PartDesign::Body`. anything PartDesign can do, including
  features fcad has never heard of. lazy, like `solid`. this is the path to take.
- `solid` - a **thunk** returning the BREP solid in the part's own unplaced
  frame, for geometry that is not a body at all: imported, or built with plain
  `Part` booleans. lazy on purpose: the bom, cutlist and schema passes never
  realize it, so keep the booleans inside the thunk.

**the bom:**

- `profile` - the bom label (e.g. a nominal lumber name); also the `STOCK` key.
- `length` - the bom length. defaults to the built shape's bbox **X-extent**,
  which means *building geometry*: state it on any model you sweep, or every
  `optimize` candidate pays for a solid just to read a bounding box.

**the drawing, which is not the geometry:**

- `dimension_sketches` - names of the part's own sketches whose circles get
  dimensioned. fcad reads them back out of the built tree, so a dimension cannot
  disagree with the bore it came from. a fastener hole is simply not listed.
- `dimension_circles` - `(cx, cy, dia)` for a part supplying `solid`, which has
  no sketches to read. these are drawn on its exported defining sketch as well,
  which is safe because (6) asserts each is actually bored - the sketch fcad used
  to draw carried an unvalidated list, and that is what made it a second
  description rather than a view of the part.

**intent the geometry does not carry:**

- `openings` - which cuts are *meant* to stay empty. `check` asks whether a part
  gave up material nothing fills; that is right for a lap cut twice as wide as
  its crossing member and wrong for a nut, and both are just a subtractive
  feature. it is every cut no other part fills, not only holes: a bore, a
  window, and equally a cut that shapes the part - a taper, a relief, a notch
  for clearance. a Fillet or Chamfer needs no declaring; `check` knows a
  dress-up is not joinery. for a part with a tree, the name of the **sketch** each cut was made
  from (what you passed `add_sketch`, not the Pocket or Hole built on it);
  `(cx, cy, dia)` circles for one supplying `solid`. `check` fails on an
  opening that names no cut, so a wrong name costs one run.
- `profile2d` - `(points, thickness)`: the stock a part is cut from, **only** for
  a part supplying `solid`, where fcad cannot derive a blank from a tree. a
  declared part needs none of this.
- `grounded` - anchors the assembly. nothing flagged means the first part is
  auto-grounded, so a minimal project still passes `check`.
- `embeds` - excluded from the interference check and the fem fuse. anything that
  intentionally sinks into other solids (screws) needs this or `check` fails.
- `disjoint` - the part is several separate pieces on purpose: the lit segments
  of a display, a set of feet. `check` otherwise reports it as a part severed
  by its own joinery. the pieces are still one part everywhere else - one bom
  row, one colour, one shape tested for overlap and support - so it suits
  pieces that are placed together and useless apart.

`grounded` and `embeds` do double duty: they also order the assembly animation,
which lands the grounded parts first and then takes, repeatedly, the lowest part
touching what is already placed, holding the `embeds` parts to the end - so the
model goes up a course at a time. flagging them correctly is what makes
`fcad animate` read as construction - unflagged, it falls back to a z-sort and
drives screws home midway up the stack. no separate annotation exists or is
needed for this.

a project may return its own duck-typed spec instead, exposing the same surface.

### the well-lit path: a part is its feature tree

a part with `build` and **no** `solid` is *declared*: FreeCAD builds it as a
`PartDesign::Body` and fcad reads the shape back, so there is one description of
the part and no second implementation to drift from. `dist/parts/<name>.FCStd`
is that body - every sketch fully constrained. prefer this.

`build` gets a live document and an empty `PartDesign::Body`, and what goes in is
**real FreeCAD code**:

```python
from fcad import types
from fcad.freecad import partdesign

def post(doc, body):
    sk = partdesign.add_sketch(doc, body, "outline", points=pts, z=-t/2)
    pad = body.newObject(types.PartDesign.Pad, "pad")
    pad.Profile = sk
    pad.Length = t
    doc.recompute()                    # each feature reads the previous shape
    groove = partdesign.add_sketch(doc, body, "groove", points=slot, z=t/2)
    pocket = body.newObject(types.PartDesign.Pocket, "groove_cut")
    pocket.Profile = groove
    pocket.Length = 6.0
    doc.recompute()

fcad.PartSpec("post", placements=[...], build=post)
```

**there is no `fcad.Feature` and no `fcad.Sketch`** - no vocabulary to learn and
nothing standing between a project and the workbench. anything fcad defined would
be a smaller, lossier copy of FreeCAD's object model, and would be exactly what
blocks the feature its author did not think of. `PartDesign::Fillet` on an edge
of an earlier feature works here and could not be expressed by any fcad-side
description of a part.

`fcad.freecad.partdesign` offers helpers, to call or to crib from (`add_text`,
for lettering, is further down):
`add_sketch(doc, body, name, points=(), loops=(), circles=(), slots=(), z=0.0,
placement=None)` emits a **fully-constrained** sketch (which `check` requires)
and returns it, and `pad_and_bore(doc, body, points, thickness, holes=(),
name=None)` (`name` defaults to `body.Name`) is the shape most parts are.
neither is a layer - they make the calls you would make. a project wanting
splines or constraints between arbitrary elements calls Sketcher directly.

what `add_sketch` takes, since it is the call a `build` makes most. everything
after `name` is optional and defaults to empty, so a sketch of `circles` alone -
the profile of a bore - is a complete call:

- `points` is the outline, a closed polygon of `(x, y)` or `(x, y, r)` tuples
  in the sketch's own plane, where `r > 0` rounds that corner with a true
  fully-constrained tangent circular arc. `loops` is a list of further closed
  polygons (sharp or rounded), `circles` a list of `(cx, cy, dia)`, and `slots`
  a list of `(x1, y1, x2, y2, width)` stadium slots. **they combine in one
  sketch**: a loop, circle, or slot inside the outline is a hole in the face a
  Pad extrudes.
- so **a window, a ring, a frame or a plate with clearance holes or slots is one
  sketch and one Pad**. reach for that before a Pocket: there is no direction to
  get wrong, and a part that is mostly hole (a gasket) has one feature, not two.
- `z` lifts a sketch parallel to XY. `placement` puts it anywhere else and wins
  when both are given: `Placement(Vector(0, 0, 0), Rotation(Vector(1, 0, 0), 90))`
  stands it on XZ (sketch x is X, sketch y is Z) for a revolution or a side
  profile, and `Rotation(Vector(1, 1, 1), 120)` on YZ (sketch x is Y, sketch y
  is Z) for a profile seen from the side or a bore along X. move the plane with
  the placement's `Vector`.
- a Pad or Pocket off such a sketch runs along the sketch normal, and which way
  that points is easy to get wrong. `pad.SideType = "Symmetric"` spends `Length`
  half each way from the sketch, so put the sketch on the feature's mid-plane
  and there is no direction to choose.

**dress-ups work on a body like any other feature**, and are what make a model
read as an object rather than as extruded outlines - break the outside edges of
a housing, round a strap. pick edges by geometry with `fcad.geom_select`, never
by hardcoded `EdgeN`:

```python
from fcad import geom_select as gs

def housing(doc, body):
    sk = partdesign.add_sketch(doc, body, "outline", points=pts)
    pad = body.newObject(types.PartDesign.Pad, "pad")
    pad.Profile = sk
    pad.Length = h
    doc.recompute()
    base = body.Tip
    top = gs.edges(base.Shape, gs.at_z(h))
    bevel = body.newObject(types.PartDesign.Chamfer, "top_bevel")
    bevel.Base = (base, top)
    bevel.Size = 0.8
    doc.recompute()
```

a `PartDesign::Fillet` is the same with `Radius` in place of `Size`. size them
for the look: what a dress-up takes is not counted by the void check, however
large. a cut made any other way that shapes the part (a Pocket trimming a
taper) is counted, so name its sketch in `openings`.

a **turned part** is the commonest shape past pad-and-bore, and fcad has no code
for it because it needs none - a `PartDesign::Revolution` is just another
feature. sketch the half-section as (radius, z) pairs closed back along the
axis, stand the sketch on XZ with `placement`, and turn it about the sketch's
own vertical axis:

```python
from FreeCAD import Placement, Rotation, Vector

def screw(doc, body):
    half = [(0, ht), (hr, ht), (hr, 0), (sr, 0), (sr, -grip), (0, -grip)]
    sk = partdesign.add_sketch(
        doc, body, "screw_profile", points=half,
        placement=Placement(Vector(0, 0, 0), Rotation(Vector(1, 0, 0), 90)))
    turn = body.newObject(types.PartDesign.Revolution, "screw_turn")
    turn.Profile = sk
    turn.ReferenceAxis = (sk, ["V_Axis"])
    turn.Angle = 360.0
    doc.recompute()
```

the same holds for every other feature: `types.PartDesign.<Name>` resolves
against the installed FreeCAD, so a Groove, AdditiveLoft, AdditivePipe, Fillet,
Chamfer or LinearPattern is available the day FreeCAD ships it, with nothing to
add here. their properties are tabled in the freecad-python skill's
`api/properties.md`.

why a single description matters. a part used to be described *twice* - `holes`
said where the bores were while the project cut them separately, and `profile2d`
was an outline drawn beside a solid it might not match - and `check` carried two
assertions whose only job was to police that gap. both are gone: a bore that *is*
a feature cannot be declared-and-not-bored, and a sketch that *is* what was
padded cannot drift from it. that is two checks deleted rather than passing. a
body is also the only representation a person can open and edit: a
`Part::Feature` is a shape in a bag.

and `PartDesign::Hole` carries what a `Part.Shape` throws away - `DepthType`
(`Dimension`/`ThroughAll`), `HoleCutType` (counterbore/countersink/counterdrill),
`ThreadType` (ISO metric, UNC, NPT, BSP...), `Threaded`, `DrillPoint`. a bore cut
as a boolean is just a cylindrical face afterwards; a Hole knows it is a hole.

pass `solid=` for geometry no feature tree describes - imported (or use
`PartSpec.from_step`), scripted, or swept from something fcad cannot name. those
parts keep the plain `Part::Feature`. `examples/fastenplates` shows both ways to
write a declared body (`pad_and_bore` for the plate, `PartDesign::Revolution`
for the screw); `examples/robotarm` adds `PartSpec.from_step` for a COTS motor.

**the cost, so you can weigh it:** a declared part is built by a real recompute,
about 17 ms against 3 ms for hand-rolled booleans, and its *blank* costs one more
recompute per feature (below). `check` pays that once per part - a pre-commit
gate, so fine. the bom, the cut list and `optimize` pay nothing **provided the
part states `length=`**; without it the fallback builds the shape to read a
bounding box, once per part per candidate, and a sweep that was 0.2s becomes
minutes.

**the blank, and why it is now right.** a part's blank - itself before its
joinery - is found by suppressing every feature that left the body with less
material than it found, and recomputing. FreeCAD will not tell you which those
are (`AddSubType` is not exposed, and Pad, Pocket, Hole and Fillet all derive from
`PartDesign::FeatureAddSub`), so fcad asks the geometry instead. that needs no
vocabulary and is right about a Pocket, a Groove and a dressup Fillet without
fcad knowing what any of them are. it replaced an outline-and-thickness blank
that could only ever express a hole, which is why a groove or a lap used to read
as material the part had lost and nothing had accounted for.

**what the geometry still cannot say is which cuts are *meant* to be empty.** a
nut's bore and a lap relieved twice as wide as its crossing member are both a
subtractive feature; only the project knows the first is the point of the part.
that is `openings`, and it is the one declaration that survived - it is intent,
not a second description of geometry.

three gotchas when writing a `build`, each of which fails quietly rather than
loudly: a feature's properties can only be set once it has a base ("No base set,
no sketch support either"), so create it in the body first; the previous feature
must be recomputed before the next is added ("Base feature's TopoShape is
invalid"), so `doc.recompute()` between them; and a cut whose profile sits on the
far face needs `Reversed` set correctly or it runs away from the material - it
succeeds, reports `Up-to-date`, and removes nothing at all. a bore sketched on
the -Z face wants `Reversed=True`, a pocket sketched on the +Z face wants it left
alone. `check` catches that last one - it fails on any feature that leaves its
part unchanged and names it - so a wrong guess costs one run, not a missing hole.

a cut that has no direction to get wrong: `pocket.SideType = "Symmetric"` cuts
both ways from its sketch, whichever face that sits on. with
`pocket.Type = "ThroughAll"` it is a through cut from either side; with a
`Length` it spends half each way, so a blind cut from a face wants `Length` set
to **twice** its depth. if a feature leaves a face split by a seam it does not
need (two extrusions meeting on the sketch plane can), set `feature.Refine =
True` and it merges them; look at a render before deciding it matters.

**lettering is a profile too**, so an engraved or embossed part stays declared.
`partdesign.add_text(doc, body, name, text, size, font=None, placement=None)`
puts a line of text in the body, lying in XY with its baseline along x unless
`placement` says otherwise, and a Pocket off it engraves while a Pad raises:

```python
legend = partdesign.add_text(doc, body, "legend", "WATER RESIST", 2.0,
                             placement=Placement(Vector(-9, 0, t / 2), Rotation()))
cut = body.newObject(types.PartDesign.Pocket, "legend_cut")
cut.Profile = legend
cut.Length = 0.1
doc.recompute()
```

`size` is the letter height in mm. `font` is a path to a `.ttf`; left out, it
is the drawing face FreeCAD ships, so the build does not depend on what fonts
the machine has. name the text in `openings` (`openings=["legend"]`) if the cut
is big enough to trouble the void check. lettering is the most expensive
geometry a part can carry: every glyph is curves, and each drawing view projects
all of them with hidden lines removed, so four lines of text add about a minute
to a part's build. keep it to the part that needs it.

when a feature must name an edge or face of an earlier one, **pick it by geometry
rather than by `EdgeN`** using `fcad.geom_select` (`gs.edges(base.Shape,
gs.at_z(h))`, `gs.along_axis("z")`, `gs.curved()`, `gs.all_of(...)`,
`gs.faces(base.Shape, gs.normal_dir("+z"), exact=1)`). it raises `ValueError` on
zero or unexpected matches rather than silently passing `[]` to `Fillet` or
`Chamfer`. capture `base = body.Tip` **before** calling `body.newObject(...)`,
since creating the new feature immediately moves `body.Tip` to the still-empty
object.

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
fcad build [TARGET ...]   no target = all; one freecadcmd pass per token. a
                          token is a target (parts, assembly, step, ...) or a
                          part's name, which builds that part alone
fcad check                the eleven assertions below
fcad precommit            build all, then check
fcad test [PATH ...]      run tests/test_*.py, judged by their verdict file
fcad optimize P [P ...]   sweep parameters (--objective length|boards|price|mass)
fcad view [parts]         gui; --part NAME for one part
fcad render [T] [PNG]     offscreen png in the parts' colours (below)
fcad animate [T]          mp4+gif in the parts' colours, drawn as render is
fcad fem [T]              headless gmsh + CalculiX; --modal / --modes K
fcad fem-render|fem-animate [T]
fcad diff|diff-build|diff-open [T]
fcad api-docs [DIR] | install-skill    reference for the installed FreeCAD
fcad pdf | clean | info | install-macro | install-git | help [COMMAND]
```

for every command but `build`, `TARGET` is `assembly` (default) or a part name;
`fcad fem all` works through every case the project declares. flags may come
before or after the positionals, `--explode` with or without its gap.

**rendering** is how you find out what you built, so do it before reporting a
model done, and look at the png. `fcad render` draws a built target with the
FreeCAD viewer in each part's `color`/`transparency`:

```
fcad render                                 # the whole assembly, iso
fcad render --exclude strap_a,strap_b       # fit the camera to what is left
fcad render --parts case,crystal --view top
fcad render --section y                     # cut in half, to see inside
fcad render --explode                       # lifted apart along z, from the side
fcad render case                            # one part
```

`--view iso|top|bottom|front|back|left|right|side`, `--size WxH`, and an output
path as the second positional (`fcad render case out.png`), or as the only one
when it ends `.png` (`fcad render --exclude strap_a out.png` is the assembly).
the default is `dist/<name>.png` for the
assembly and `dist/parts/<part>.png` for a part, with each `--view`, `--section`
and `--explode` given added to the name (`<name>.top.section-y.png`), so one
view does not overwrite another.
`--parts` and `--exclude` are **not** in the name: give a subset you want to
keep a path. a model that is small beside something long - a watch head on its
strap - needs `--exclude` or `--parts` to be seen at all. needs `xvfb-run` on
PATH.

**animating** is a camera crossed with a subject, shared by both animators.
`--camera orbit|turntable|fixed` (orbit sweeps its elevation so the underside
comes into view; turntable spins level). `fcad animate` supports three subjects:
`--subject assemble` (default for assemblies: parts flying in to build the
model), `--subject static` (default for parts: orbits whole), and `--subject
motion` (interpolates `<name>-motion.json` with position lerp + quaternion
SLERP at 25 fps by default, writing `<name>.motion.{mp4,gif}`). its frames come
from render's viewer, so a part's `color` and `transparency` show in the clip;
`--size WxH` sets the frame. `fcad fem-animate --subject all|flex|static|modes`,
where `static` holds peak deflection and lets the camera work - usually the clip
worth having (dense meshes are automatically vertex-clustered below
`FCAD_FEM_MAX_TRIS`). length is derived from the content, so `--speed`
multiplies it rather than `--seconds` setting it.

**build targets:** `parts assembly step stl svg dxf drawings sketches bom
cutlist`. multiple tokens union. stage-bound tokens keep their meaning:
`sketches` is parts-only, `bom` and `cutlist` are assembly-only (only the
assembly knows how many of a part there are).

**what `check` asserts** (R3.1) - all of these must hold before a commit:
1. every predicate in `CONSTRAINTS` holds
2. no structural solids interpenetrate (`embeds` parts excluded)
3. every `embeds` part is seated: it displaces nothing, and something holds it
4. every part is grounded, fastened, or resting on another
5. no part cuts away more material than anything fills (`cots=True` skipped)
6. every circle a part dimensions is actually bored in it
7. every part is one connected solid (`disjoint=True` and `cots=True` skipped)
8. every sampled pose in `MOTION` (when declared) holds constraints, clears overlaps, and seats fasteners
9. every assembly component is grounded or jointed
10. every defining sketch is fully constrained
11. every feature changes its part (a cut run the wrong way removes nothing)

(1) is the project's own statement of when the model still means what it says,
and is checked first because a model that has stopped meaning it makes the rest
moot.

**(3) to (7) look for the absence of geometry, which (2) is structurally unable
to see.** a fastener that fits no hole, a part with nothing under it, a cut
larger than the joint it relieves, a circle dimensioned and never made, a part
severed by its own joinery: every one of those renders correctly, exports
correctly, and passes an interference test. that is the whole reason they exist,
and they were each written after a real model shipped the failure.

(6) can only fail on a part that hands over its own `solid`, since that is the
only kind that still names its circles apart from cutting them. a declared part's
dimensions are read back out of its bores, so there is nothing to disagree with.

**one assertion used to live here and is gone rather than passing**: a note about
an outline the solid does not match. it policed the gap between a part's two
descriptions, and a declared part has one - the sketch *is* what was padded.

notes on the ones with judgement in them:
- (2) to (4) **scale with the part.** shared volume counts as interference above
  1 mm^3 *or* 0.1% of the smaller solid, whichever is less; the seating nudge is
  1.5 mm and the support drop 2 mm, *or* half the probing part's own extent,
  whichever is less. the absolutes are right for lumber and are only ceilings:
  a 5 mm^3 watch screw is held to 0.005 mm^3, and a 0.5 mm gasket is dropped
  0.25 mm rather than clean through the plate under it. `testing.TOUCH_VOL` is
  still the flat 1 mm^3, so a small model's own tests should state a tighter
  threshold.
- (3) reports `interferes` (exact) apart from `unseated` (a nudge test, so a
  heuristic). `embeds` means "this sinks into something", not "stop looking".
- (4) only applies to a model that flags `grounded` somewhere - "held up by
  something" is a claim about a physical stack, and plenty of models are not
  one. with nothing flagged it prints `check skipped: no part is flagged
  grounded` in place of its `ok`, and tests nothing: **flag the part the rest
  stands on** if the model is a real object, or a floating part passes
  unnoticed. a part with an `embeds` part through it counts as fastened, and so does
  one that would land on a fastener if dropped (a clearance hole shares no
  volume with its screw). a part that merely hangs loose around another, with
  more than its own half-height of slack, still reads as unsupported.
- (5) budgets the void as a fraction of the part's own blank, because real
  clearances scale with the part and a mistake does not. it measures against the
  blank with the part's `openings` still cut, so what it sees is joinery alone -
  a nut's bore is the point of the nut, and nothing in the geometry says so.
  the budget is 1% of that blank, and the passing line names the part running
  nearest it (`most: case, 0.42% of its blank against 1.0% allowed`). what a
  Fillet or Chamfer takes is shaping and is not counted, and holes drawn as
  inner loops of a padded sketch are not cuts and cost nothing.
- (7) is about a part severed by accident, so it also refuses one that is
  several pieces on purpose - the lit segments of a display, a row of feet.
  flag that part `disjoint` and build every piece into the one body (islands in
  one padded sketch are the cheap way). when the pieces are things you would
  count or buy singly, model the piece as the part instead and give it one
  placement per instance: the bom then counts them, and each is checked on its
  own. the passing line names the parts that were flagged and so not asked.

## what lands in dist/

```
parts/<name>.{FCStd,step,stl,svg,dxf}           .svg is a dimensioned 4-view A4 sheet (TOP/FRONT/RIGHT/ISO + holes)
drawings/<name>.dxf   drawings/assembly.dxf     dimensioned techdraw sheets (+ Hole callouts & assembly BOM)
sketches/<name>.{svg,dxf}    1:1 defining sketches, parts only. a declared part's own;
                             else its `profile`/`profile2d` outline plus `dimension_circles`
sketches/<name>-flat.svg     sheet-metal flat pattern with dashed bend lines (when `flat_pattern` set)
<name>.{FCStd,step,stl,svg,dxf}                 linked assembly + exports (.svg is a 4-view A4 sheet + BOM)
<name>-bom.csv          part,qty,profile,length_mm[,bom_group,sku,unit_cost,mass_g]
<name>-cutlist.csv      profile,pattern,stock_mm,boards,cut_mm,per_board,offcut_mm
<name>-sheetlist.csv    2D sheet nesting plan (when `STOCK` uses `fcad.Sheet`)
<name>-placements.json  per-instance placements, for the 3d diff
<name>-parts.json       per-part grounded/embeds/rides and look, for animate and render
<name>-motion.json      sampled kinematic placements (when `MOTION` is declared)
<name>[.<view>][.section-<axis>][.explode].png   the render
<name>.assemble.{mp4,gif}   <name>.spin.{mp4,gif}   <name>.motion.{mp4,gif}
<name>.fem.{FCStd,npz,png,mp4,gif}   <name>.fem.mode<K>.{mp4,gif}
<name>.diff.FCStd
parts/<part>.png   parts/<part>.spin.*   parts/<part>.fem.*   parts/<part>.diff.FCStd
```

**everything made from a target sits beside the target's own files and is named
after them**: the assembly's next to `<name>.FCStd`, a part's next to
`parts/<part>.FCStd`. so `fcad render` writes `<name>.png` - one more export of
the model - and `fcad render case` writes `parts/case.png`.

the json files exist because renderers and animators run outside FreeCAD and
cannot call `compute`: the diff needs both revisions' placements, the animator
needs the per-part flags/look, and `--subject motion` reads the sampled pose
frames. every assembly build writes placements and parts whatever formats were
asked for, and writes `-motion.json` whenever `MOTION` is declared.

the assembly `.FCStd` is a real Assembly-workbench assembly: each instance is an
`App::Link` into its part file, grounded or Fixed-jointed and solved.

### ASSEMBLE: jointing it yourself

fcad grounds the parts flagged `grounded` and mates every other instance to the
datum with a **Fixed** joint. that is the honest default for a model whose
positions python already computed - there is nothing left for the solver to
resolve, and a joint that named a face would drag in the topological naming
problem fcad avoids everywhere else. but Fixed is one of **thirteen** joint
types this FreeCAD ships (`Fixed, Revolute, Cylindrical, Slider, Ball, Distance,
Parallel, Perpendicular, Angle, RackPinion, Screw, Gears, Belt`), and a hinge is
not a fixed joint.

declare `assemble` and you own the jointing. fcad has already created the links
and grounded the anchors; you get the real `Assembly::AssemblyObject`:

```python
def ASSEMBLE(doc, asm, links):
    """links: {"base": [Link_001], "arm": [Link_001, Link_002], ...}"""
    from fcad.freecad import build_assembly as asmlib

    asmlib.joint(doc, asm, "Rev_arm", "Revolute",
                 links["base"][0], links["arm"][0])
    asmlib.fix_remaining(doc, asm, links, links["base"][0])
```

`asmlib.joint` returns the real FreeCAD joint object so any joint property can
be set directly, and `asmlib.fix_remaining` fixes every remaining unjointed
component to the datum. fcad adds **no** Fixed joints when a hook is present -
one owner at a time, so there is no over-constraint puzzle.

`check`'s "every component is grounded or jointed" still applies, and a custom
joint satisfies it - the hook is not an escape from the assertion.

## FEM

`FEM` is `{target: case}` (with an optional `"__default__"`) or a callable
`(target, shape, values) -> case`. a case:

| key | meaning |
| --- | --- |
| `fixed` | list of face selectors (or callable `(values) -> [...]`), clamping the face outright (default: `min_along("z")`) |
| `supports` | `[dict(faces=[...], fix="yz")]` - restrain only the named axes |
| `ties` | `[dict(master=sel_a, slave=sel_b, tolerance=1.0)]` - explicit CalculiX `*TIE` bonded faces |
| `loads` | list of load dicts (or callable `(values) -> [...]`); each load's `magnitude` may be a number or `lambda p: p[...]` |
| `self_weight` | default `not loads`; `gravity` defaults to `(0,0,-1)` |
| `mesh_size` | clamped to 1..25 mm; default bbox diagonal / 20 |
| `mesh_min` / `mesh_curvature` | element floor, and how hard gmsh chases curvature (12/turn) |
| `undrilled` | build parts from their blank, dropping the holes |
| `target` / `params` | solve an underlying target (`"assembly"` or part) with `PARAMS` overrides (e.g. a second kinematic pose) |
| `modes` | eigenmode count |
| `material` | library card name, an fcad alias (`steel`/`aluminum`/`wood`/...), a `MATERIALS` name, or `{E, nu, rho}`; falls back to `MATERIAL`. when structural parts in an assembly set distinct `PartSpec(..., material=...)`, fcad builds a conformal multi-material `CompSolid` with per-region materials |

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

## CONSTRAINTS, and finding a cheaper size

a parametric model has sizes at which it silently stops being the thing it
describes: a depth clamped against the stack beneath it, a count collapsing to
zero. `CONSTRAINTS` is how a project says so - a list of
`(values, computed) -> bool | str` predicates, where returning a string names
what went wrong and is what a person reads.

```python
CONSTRAINTS = [
    lambda p, d: (d["soil_depth"] >= p["soil_depth"]
                  or "the bed clamps to %.0f mm" % d["soil_depth"]),
]
```

`check` asserts them. `fcad optimize` **refuses any candidate that breaks one**,
which is the only thing that makes a sweep safe:

```
fcad optimize outer_len outer_wid --span 60 --step 10 --objective length|boards|price|mass
```

it varies the named numeric params over a grid, packs the real bom for each
through the cut list solver, and ranks them. without constraints it will find
the corner where the model degrades and report it as a saving, because from the
outside there genuinely are fewer boards - a real sweep once "saved" three
boards by quietly shrinking a planter's bed by 50 mm. a project that declares
none is warned, loudly, in the output, and so is one whose swept 1D stock parts
omit explicit `length=`.

ranking follows `--objective`: `length` (default: least purchased 1D length),
`boards` (fewest boards), `price` (1D stock + 2D `Sheet` stock + COTS
`unit_cost`), or `mass` (total grams from explicit `mass_g` or `volume *
density`).

## testing a project

`fcad test` runs `tests/test_*.py` under freecadcmd and judges each by the
verdict it writes, not by its exit status - freecadcmd exits 0 on an uncaught
exception and discards buffered stdout when it does not, so a test trusted on
either channel reports a crash as a pass. `fcad.testing` supplies the other half:

```python
from fcad import testing

def main():
    c = testing.Checks()
    mod = testing.load(testing.find())        # a .fcad is not importable
    parts = testing.specs(mod)
    c("the deck bears on the sills",
      testing.near(testing.extent(mod, parts["deck_board"]).ZMin, top))
    return c.report()                         # writes $RESULT_FILE

testing.main(main, __file__)
```

the rest of the vocabulary every project's tests turned out to need:

| call | gives |
| --- | --- |
| `find(path=None, stem=None)` | the `.fcad` one directory above the test |
| `load(path)` | the project module; read `mod.PARAMS`, call `mod.compute` |
| `specs(mod, values=None)` | `{name: spec}`, at `PARAMS` unless given values |
| `shape(mod, spec)` | the part's unplaced solid |
| `solids(mod, spec)` | one placed solid per placement, in placement order |
| `blanks(mod, spec)` | the same before joinery, when the project has a `blank` |
| `extent(mod, spec, index=None)` | `BoundBox` of one placed instance, or of all united |
| `overlap(a, b)` | shared volume of two placed solids, mm^3 |
| `near(a, b, tol=1e-6)` | `abs(a - b) <= tol`; state `tol` for measured geometry |
| `Checks()` | `c(name, ok)` records one assertion, `c.report()` writes the verdict |
| `fem_result(target, solve=False, values=None)` | load a fresh `FemResult` (`max_disp`, `vm_p95`, `vm_p99`, `mode_freqs`, `safety_factor`); refuses missing/stale `.npz` when `solve=False` |
| `fem_solve(target, values=None)` | returns a fresh `FemResult`, reusing cached `.npz` when fresh or solving in scratch for `values` overrides |

what comes back is an ordinary `Part` solid, so anything these do not cover is
plain FreeCAD: `solid.BoundBox`, `solid.CenterOfMass`, `solid.Volume`, and
`solid.isInside(Vector(x, y, z), tol, True)` to ask whether a point is material -
which is how a test shows a hole is where its fastener is.

they read a project however it declares
itself: a `compute` returning the bare list or the dict form, and specs realized
by a module-level `from_spec` or by the `fcad.PartSpec`'s own `solid()` thunk.

**conventions and patterns:**

- put a project's tests in a single `tests/test_<name>.py` (matching the `.fcad`
  stem, as all four shipped examples do: `test_hexnut.py`, `test_fastenplates.py`,
  `test_cantilever.py`, `test_robotarm.py`) unless slow FEM tests need to be
  run selectively via `fcad test tests/test_fem.py`.
- **testing FEM results:** call `testing.fem_solve(target)` to get a `FemResult`
  (`max_disp`, `vm_p95`, `vm_p99`, `vm_max`, `mode_freqs`, `first_mode`,
  `safety_factor(yield_mpa, percentile=95)`). when a fresh stamped `.npz`
  already sits in `dist/`, `fem_solve` loads it in milliseconds without
  re-running CalculiX; when the project source changed or `dist/` is empty, it
  re-solves and caches the new bundle. pass `values={...}` to test a parameter
  or load override in a throwaway scratch dir without overwriting `dist/` (e.g.
  confirming `2x` load produces `2.00x` deflection, or comparing extended vs
  retracted poses). call `testing.fem_result(target, solve=False)` in CI when
  you want a missing or stale `.npz` to fail loudly instead of solving.
  wherever a closed form exists (`F*L^3/(3*E*I)` on a beam), assert against the
  closed form, not just against a previous run (`examples/cantilever/tests/test_cantilever.py`).
- **testing articulation & kinematics:** sweep pose parameters via
  `mod.compute(dict(mod.PARAMS, theta=...))` to assert reach, joint angles,
  clearances (`testing.overlap(...) < 1e-3`), and actuator stroke
  (`a.distToShape(b)[0]`). call `build_assembly.find_motion_issues(project, p)`
  to assert the declared `MOTION` trajectory has zero collisions or constraint
  violations, assert `project.violations(bad_pose)` rejects out-of-envelope
  poses, and inspect `o.JointType` in the saved `.FCStd` assembly
  (`examples/robotarm/tests/test_robotarm.py`).

the import needs no preamble. FreeCAD's embedded interpreter ignores PYTHONPATH,
so a test handed straight to freecadcmd cannot resolve `fcad` at all - which is
why projects hand-rolled a `sys.path` insert pointing at the fcad checkout.
`fcad test` runs each test through the same bootstrap the build path uses
instead, so `from fcad import testing` just works.

## STOCK, 2D sheets, and sheet metal

`fcad build cutlist` packs each 1D bom profile's pieces into buyable stock
lengths (`dist/<name>-cutlist.csv`) and each 2D `fcad.Sheet` profile's blanks
into buyable sheets (`dist/<name>-sheetlist.csv`).

```python
FT = 304.8
STOCK = lambda p: {
    "2x6": [(8*FT, 9.0), (12*FT, 13.5)],
    "2mm al sheet": [fcad.Sheet(400.0, 300.0, price=18.0)],
}
```

prefer a callable `STOCK = lambda p: ...` whenever profile labels depend on
`PARAMS` (e.g. `"bar %gx%g" % (p["wid"], p["thk"])`), so sweeping those
parameters still matches the stock table instead of going stale on import-time
defaults. a static dict or bare list still works.

for bent sheet-metal parts, attach `flat_pattern=sheetmetal.unfold_u_channel(...)`
or `sheetmetal.unfold_box_tray(...)` to `PartSpec`: fcad computes K-factor bend
allowances, exports `dist/sketches/<part>-flat.svg` with solid cut contours and
dashed red bend lines, and feeds the unfolded `(width, height)` into 2D sheet
nesting automatically (flat plates without bends just set `sheet_size=(w, h)`).

an entry may carry a price as `(length, price)` or `fcad.Sheet(w, h, price=...)`,
which adds a cost to the totals line and enables `fcad optimize --objective price`:

```
  total: 16 board(s), 55474 mm purchased, 2386 mm waste (4.3%)
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
  (`Part.makeBox(l, w, t, Vector(-l/2, -w/2, -t/2))`). translate sets the
  shape's Placement, which the assembly clobbers when it assigns the instance
  Placement - parts end up offset by half their size and rotating about the wrong
  point.
- `solid` is a thunk, and so is `build`. work done outside them runs on every
  metadata-only pass.
- every sketch a `build` emits must come out **fully constrained** or `check`
  fails on it. `partdesign.add_sketch` does that for outlines (sharp or rounded
  `(x, y, r)` corners), loops, `slots`, and circles; hand-written Sketcher code
  has to do its own.
- **capture `base = body.Tip` before `body.newObject(Chamfer/Fillet, ...)`:**
  creating the dress-up moves `body.Tip` to the new empty feature immediately,
  so `gs.edges(body.Tip.Shape, ...)` after `newObject` sees zero edges.
- **do not freeze `PARAMS[...]` inside static `STOCK` or `FEM` dicts:** use
  `STOCK = lambda p: ...` and `"magnitude": lambda p: p["..."]` so edits,
  multi-pose FEM overrides, and `optimize` sweeps see live parameter values.
- **state `length=` on any model you sweep.** without it the bom builds the
  part's shape to read a bounding box, and `optimize` warns and does that per
  candidate.
- forget `embeds=True` on a fastener and `check` reports it as interference.
- `length` defaults to the bbox X-extent; mitered or rotated parts usually need
  it stated.
- **the peak von Mises is a property of the mesh, not of the part.** it lands on
  whatever singularity the model contains - a clamped face, the sharp corner of a
  notch - where linear elasticity has no finite answer, so it simply grows as the
  mesh is refined. the shipped cantilever goes from 74.9 to 12848 MPa between
  `mesh_size` 6 and 2 while its p95 stays near 70 and its deflection does not
  move. quote `von_mises_p95` / `FemResult.safety_factor(...)`, and treat `max`
  as a sample. (the caveat on p95: it is a floor on the field stress, since on a
  clamped model the moment peaks at the constrained end and a percentile discards
  exactly those nodes.)
- **the assembly fem target is rigidly bonded.** single-material assemblies fuse
  into one body, and multi-material assemblies build a conformal `CompSolid`
  (sharing mesh nodes across touching faces); either way every butt joint acts as
  a weld and reads stiffer than a screwed frame. treat assembly FEM as load flow
  and pose comparison, and size critical members against per-part cases.
- a fem case tuned before 2nd-order meshing will be far too fine: the same
  `mesh_size` now carries several times the nodes. if a solve is refused for
  memory, raise `mesh_size` rather than reaching for `FCAD_MEM`.
