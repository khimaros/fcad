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
does not cover the FreeCAD api. the FreeCAD 1.1.1 gotchas fcad has already
absorbed live in `fcad/CONTRIBUTING.md`; read that before rediscovering one.

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
- **gui** (`freecad`) - view, view-parts, pdf, diff-open.
- **in-process** (the cli's own python) - render, animate, fem-render,
  fem-animate, and diff's worktree orchestration.

## a project is PARAMS + compute

a whole project can be one file: a directory's `project.py`, or a standalone
**`.fcad`** file (python by another name, loaded by path since `.fcad` is not a
registered source suffix).

```python
from FreeCAD import Placement

import fcad
from fcad.freecad import partdesign

PARAMS = {"length": 100.0, "width": 60.0, "height": 20.0, "bore": 12.0}

def _block(doc, body, p):
    l, w = p["length"] / 2.0, p["width"] / 2.0
    partdesign.pad_and_bore(doc, body, [(-l, -w), (l, -w), (l, w), (-l, w)],
                            p["height"], [(0.0, 0.0, p["bore"])], name="block")

def compute(p):
    return [fcad.PartSpec("block", placements=[Placement()], length=p["length"],
                          build=lambda doc, body: _block(doc, body, p),
                          dimension_sketches=["block_bore1"],
                          openings=["block_bore1"])]
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
| `STOCK` | buyable stock lengths per bom profile (below) |
| `FROM_SPEC` / `PROFILE` | realize a spec that is not an `fcad.PartSpec` |
| `CONSTRAINTS` | predicates saying when the model still means what it says |
| `ASSEMBLE` | joint the assembly yourself (below) instead of Fixed-to-datum |
| `NAME`, `SCHEMA`, `ENUM_CHOICES` | override the inferred name/varset/dropdowns |

each is read as the UPPER_CASE constant or the lowercase `Project` kwarg name,
so `PARAMS` + `def compute` is idiomatic. an explicit
`PROJECT = fcad.Project(...)` is always supported and never removed.

### fcad.PartSpec

```
fcad.PartSpec(name, placements, build=None, solid=None, profile="", length=None,
              grounded=False, embeds=False, dimension_sketches=(),
              dimension_circles=(), openings=(), profile2d=None)
```

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
  feature. sketch names for a part with a tree, `(cx, cy, dia)` circles for one
  supplying `solid`.
- `profile2d` - `(points, thickness)`: the stock a part is cut from, **only** for
  a part supplying `solid`, where fcad cannot derive a blank from a tree. a
  declared part needs none of this.
- `grounded` - anchors the assembly. nothing flagged means the first part is
  auto-grounded, so a minimal project still passes `check`.
- `embeds` - excluded from the interference check and the fem fuse. anything that
  intentionally sinks into other solids (screws) needs this or `check` fails.

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

`fcad.freecad.partdesign` offers two helpers, to call or to crib from:
`add_sketch(doc, body, name, points=, circles=, z=, placement=)` emits a
**fully-constrained** sketch (which `check` requires), and
`pad_and_bore(doc, body, points, thickness, holes, name=)` is the shape most
parts are. neither is a layer - they make the calls you would make, and the
turned screw in `examples/fastenplates` ignores both. a project wanting arcs,
splines or constraints between elements calls Sketcher directly.

why it is not just tidier. a part used to be described *twice* - `holes` said
where the bores were while the project cut them separately, and `profile2d` was
an outline drawn beside a solid it might not match - and `check` carried two
assertions whose only job was to police that gap. both are gone: a bore that *is*
a feature cannot be declared-and-not-bored, and a sketch that *is* what was
padded cannot drift from it. that is two checks deleted rather than passing. a
body is also the only representation a person can open and edit: a
`Part::Feature` is a shape in a bag.

and `PartDesign::Hole` carries what a `Part.Shape` throws away - `DepthType`
(`Dimension`/`ThroughAll`), `HoleCutType` (counterbore/countersink/counterdrill),
`ThreadType` (ISO metric, UNC, NPT, BSP...), `Threaded`, `DrillPoint`. a bore cut
as a boolean is just a cylindrical face afterwards; a Hole knows it is a hole.

pass `solid=` for geometry no feature tree describes - imported, scripted, or
swept from something fcad cannot name. those parts keep the plain
`Part::Feature`. `examples/fastenplates` has one of each: a declared plate and a
turned screw.

**the cost, so you can weigh it:** a declared part is built by a real recompute,
about 17 ms against 3 ms for hand-rolled booleans, and its *blank* costs one more
recompute per feature (below). `check` pays that once per part - a pre-commit
gate, so fine. the bom, the cut list and `optimize` pay nothing **provided the
part states `length=`**; without it the fallback builds the shape to read a
bounding box, once per part per candidate, and a sweep that was 0.2s becomes
minutes.

**the blank, and why it is now right.** a part's blank - itself before its
joinery - is found by suppressing every feature whose removal gives material
back, and recomputing. FreeCAD will not tell you which those are (`AddSubType` is
not exposed, and Pad, Pocket, Hole and Fillet all derive from
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
alone.

when a feature must name an edge or face of an earlier one, **pick it by geometry
rather than by `EdgeN`** - enumerate `pad.Shape.Edges`, filter on a predicate,
and use the index you found. that is the same rule `fem_select` follows for FEM
faces, and it is what keeps a model from breaking when the topology renumbers.

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
fcad check                the nine assertions below
fcad precommit            build all, then check
fcad test [PATH ...]      run tests/test_*.py, judged by their verdict file
fcad optimize P [P ...]   sweep parameters for a cheaper cut list
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

**what `check` asserts** (R3.1) - all of these must hold before a commit:
1. every predicate in `CONSTRAINTS` holds
2. no structural solids interpenetrate (`embeds` parts excluded)
3. every `embeds` part is seated: it displaces nothing, and something holds it
4. every part is grounded, fastened, or resting on another
5. no part cuts away more material than anything fills
6. every circle a part dimensions is actually bored in it
7. every part is one connected solid
8. every assembly component is grounded or jointed
9. every defining sketch is fully constrained

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
- (3) reports `interferes` (exact) apart from `unseated` (a nudge test, so a
  heuristic). `embeds` means "this sinks into something", not "stop looking".
- (4) only applies to a model that flags `grounded` somewhere - "held up by
  something" is a claim about a physical stack, and plenty of models are not
  one. a part with an `embeds` part through it counts as fastened.
- (5) budgets the void as a fraction of the part's own blank, because real
  clearances scale with the part and a mistake does not. it measures against the
  blank with the part's `openings` still cut, so what it sees is joinery alone -
  a nut's bore is the point of the nut, and nothing in the geometry says so.

## what lands in dist/

```
parts/<name>.{FCStd,step,stl,svg,dxf}
drawings/<name>.dxf   drawings/assembly.dxf     dimensioned techdraw sheets
sketches/<name>.{svg,dxf}    defining sketches, parts only. a declared part's own;
                             else its `profile`/`profile2d` outline plus the
                             circles it names in `dimension_circles`
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
def assemble(doc, asm, links):
    """links: {"base": [Link_001], "arm": [Link_001, Link_002], ...}"""
    import JointObject                       # /usr/share/freecad/Mod/Assembly
    from fcad.freecad import build_assembly as asmlib

    joints = doc.getObject("Joints")
    j = joints.newObject("App::FeaturePython", "Rev_arm")
    JointObject.Joint(j, asmlib.joint_type(JointObject, "Revolute"))
    j.Reference1 = asmlib.whole_of(asm, links["base"][0])
    j.Reference2 = asmlib.whole_of(asm, links["arm"][0])
```

fcad adds **no** Fixed joints when a hook is present - one owner at a time, so
there is no over-constraint puzzle. call
`build_assembly.fix_to_datum(JointObject, asm, joints, datum, links)` for the
instances you do not want to joint yourself; that is exactly fcad's default.

`check`'s "every component is grounded or jointed" still applies, and a custom
joint satisfies it - the hook is not an escape from the assertion.

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
fcad optimize outer_len outer_wid --span 60 --step 10 --objective boards
```

it varies the named numeric params over a grid, packs the real bom for each
through the cut list solver, and ranks them. without constraints it will find
the corner where the model degrades and report it as a saving, because from the
outside there genuinely are fewer boards - a real sweep once "saved" three
boards by quietly shrinking a planter's bed by 50 mm. a project that declares
none is warned, loudly, in the output.

ranking follows `--objective`, and the choice matters more here than in the cut
list: on a real model the fewest-boards answer bought 3.6 m *more* timber than a
plan with one board extra. purchased length is the default for that reason.

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

plus `shape`, `solids`, `blanks`, `extent`, `overlap`, `near` - the vocabulary
every project's tests turned out to need. they read a project however it declares
itself: a `compute` returning the bare list or the dict form, and specs realized
by a module-level `from_spec` or by the `fcad.PartSpec`'s own `solid()` thunk.

the import needs no preamble. FreeCAD's embedded interpreter ignores PYTHONPATH,
so a test handed straight to freecadcmd cannot resolve `fcad` at all - which is
why projects hand-rolled a `sys.path` insert pointing at the fcad checkout.
`fcad test` runs each test through the same bootstrap the build path uses
instead, so `from fcad import testing` just works. `examples/fastenplates/tests/`
is the whole shape of it.

## STOCK and the cut list

`fcad build cutlist` packs each bom profile's pieces into buyable stock lengths
and writes `dist/<name>-cutlist.csv` plus a summary line per profile.

```python
FT = 304.8
STOCK = {"2x6": [8*FT, 10*FT, 12*FT, 16*FT], "2x4": [8*FT, 10*FT, 12*FT]}
```

an entry may carry a price as `(length, price)`, which adds a cost to the
totals line. the summary ends with one, because per-profile lines answer "how do
I cut the 2x6" and nobody's shopping list is one profile:

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
  fails on it. `partdesign.add_sketch` does that for outlines and circles;
  hand-written Sketcher code has to do its own.
- **state `length=` on any model you sweep.** without it the bom builds the
  part's shape to read a bounding box, and `optimize` does that per candidate.
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
