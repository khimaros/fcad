# contributing

## layout

fcad is a `src/`-layout python package (see [DESIGN.md](DESIGN.md)). the cli
and `config` are plain python; anything under `src/fcad/freecad/` may import
`FreeCAD` and only runs inside `freecadcmd`/`freecad`.

## developing

install editable so the `fcad` console script tracks your working tree:

```
uv tool install -e .
```

`config.py`, `cutlist.py` and `fem_select.py` must stay pure stdlib (every
interpreter imports them, `config` even at install time). keep
`import FreeCAD`/`Part`/`TechDraw` confined to `src/fcad/freecad/`, and
numpy/matplotlib confined to `src/fcad/render/`.

## testing

prefer end-to-end integration tests over unit tests: drive the cli against the
fixture project under `tests/` and assert the artifacts in `dist/`. the tests
require FreeCAD 1.1.x on `PATH` (the build/validate paths exercise the real
kernel).

two things the suite has to keep honest about itself, both learned the hard way:

- assert *values*, not self-consistency, wherever a closed form exists. "it
  deflected" passes just as happily on a solve that loaded the wrong axis.
- reproduce the real io. `freecadcmd` drops buffered stdout on a non-zero exit,
  and stdout is only block-buffered when it is **not** a tty, so a test that
  captures in-process (or runs on a pty) cannot see it. `tests/test_check.py`
  spawns a child with stdout on a pipe for exactly that reason.

before committing, run:

```
make precommit
```

## gui-only steps (need a display)

a project's `view`, `view-parts`, `pdf`, and `diff` targets launch the real
`freecad` gui. the headless build path never opens a window, so these
entrypoints work around gotchas the gui session introduces:

- **pdf:** `export_pdf.py` builds a TechDraw page per part + the assembly and
  exports each with `TechDrawGui.exportPageAsPdf`. three non-obvious gotchas
  (all confirmed by rendering the pdf back to png and looking):
  1. **the page must be painted first.** `exportPageAsPdf` renders the page's
     qgraphicsscene, which only exists after the page is opened in an mdi view
     and the views are told to paint. so: `page.ViewObject.doubleClicked()`,
     pump the qt event loop (`QtWidgets.QApplication.processEvents()` in a
     loop), `view.requestPaint()` / `page.requestPaint()`, pump again, then
     export. skip this and the pdf is blank even though `view.getVisibleEdges()`
     already returns the projected edges.
  2. **set the scale explicitly.** `view.ScaleType="Automatic"` only computes a
     scale lazily during gui paint; headless/scripted it stays `Scale=1.0` and a
     ~900 mm part overflows the sheet. compute the fit scale from the source
     bbox and set `ScaleType="Custom"` + `view.Scale` (see `util._fit_scale`).
  3. **tear down without a dialog or a crash.** close *every* open document
     (`App.closeDocument`) so no unsaved-changes dialog blocks `fcad pdf`, then
     `os._exit(0)`: a normal qt exit segfaults tearing down the techdraw gui
     scenes, but the pdfs are already written synchronously by then.
- **view:** `view.py` opens the assembly read-only and forces component
  Visibility/fit. it does **not** recompute on load: the build saves the doc
  fully baked (it purges the joints' touched flags; see the Assembly note
  below), so a recompute would only re-run the joints' non-DAG cycle and log
  `still touched after recompute` once per joint. joints created headless still
  have no view-provider proxy, so when the gui restores the file it raises in two
  places: `Joint.redrawJointPlacements` calls `joint.ViewObject.Proxy.redraw...`
  where Proxy is still the default `int`; and if a `ViewProviderJoint` is
  present, restore replays `Placement1/2` and fires `updateData` before
  `attach()` has built the `switch_JCS*` coin nodes. `view.py` monkeypatches both
  (the data-class `Joint.redrawJointPlacements` and the `ViewProviderJoint`
  methods) to no-op until things are ready (the jcs markers are decoration we
  don't need), removing the tracebacks. the single `graph must be a DAG` line at
  open is a benign solver note (the `Assembly`<->joint reference cycle), not an
  error. forcing Visibility marks the gui document modified (a freecadcmd build
  writes no GuiDocument, so components open hidden), so after fitting, `view.py`
  clears it with `Gui.getDocument(name).Modified = False`: the view state is
  display-only and the build owns the file, so closing must not prompt to save.
- **diff-open:** a freecadcmd-built document opens with **every** object hidden,
  and that includes the container objects, not just the geometry. `view_diff`
  originally showed only the `Part::Feature`s, which drew the solids but left each
  layer's own `App::DocumentObjectGroup` switched off: the tree read hidden while
  the 3d view read visible, and clicking a layer appeared to do nothing until it
  had been toggled twice. show every object that has a `ViewObject`. confirmed by
  reading the tree items back under `xvfb-run freecad`: on a raw open all six
  objects report `Visibility=False` and the tree greys them (`fg=116,116,116`);
  after the fix all six are `True` and the rows draw normally.
- `dconf-CRITICAL ... /run/user/1000/dconf` messages are sandbox env noise,
  unrelated to the model.
- **view state:** visibility is gui state, held in the zip's `GuiDocument.xml`,
  and freecadcmd has no `ViewObject` to produce one. so a freecadcmd-built
  `.FCStd` used to open with every object switched off: a part looked empty, holes
  and all, and an assembly showed nothing at all (its only visible objects were the
  joints, which draw nothing without a view proxy). the build now writes that file
  itself (`util.export_gui_state`). freecad restores a *partial* GuiDocument
  happily, defaulting every property it does not find, so emitting just the
  `Visibility` bool per object is enough - no need to synthesize the 15-property
  ViewProvider blocks the gui writes, which would bake gui internals into the
  builder. it is appended to the saved zip with plain `zipfile`, so the build stays
  headless. call it directly after saving and before adding anything the saved file
  does not contain: `build_one` saves the part *before* the dxf/drawing/sketch
  exports (which add TechDraw pages), and bakes the view state at that same point.
  `view_parts.py` / `view.py` still force visibility at open, which costs nothing
  and keeps artifacts built by an older fcad working.
- **baked camera:** the same GuiDocument carries a `<Camera>` (one coin
  `OrthographicCamera` node serialized into a single attribute), so a file also
  opens *framed* rather than needing a manual View Fit. the relationship to the
  model was measured off the gui's own `viewIsometric` + `ViewFit`, on a single
  part and on a 190-instance assembly, and is exact: ortho `height` is the visible
  bounding box's **diagonal**, `focalDistance` is half of it, and `position` is the
  box centre plus `diagonal/(2*sqrt(3))` along `(1,-1,1)`. the `orientation`
  quaternion is a direction, so it is the same constant for every model. using the
  diagonal (not the projected extent) means it fits at any view angle. do not trust
  a camera captured from a `ViewFit` under a headless/offscreen window - the
  viewport has no useful size there and you get the default unit camera back.
  gotcha: `App.Document.save()` writes the GuiDocument but does
  **not** clear the gui's own `Modified` flag, so the window still reads "unsaved
  changes" after a save; clear it with `Gui.getDocument(name).Modified = False`
  (`view.py`/`view_diff.py` use the same call without saving, since they don't
  persist their display-only view state).

## FreeCAD 1.1.1 api notes (confirmed on this machine)

run scripts with `freecadcmd <script.py> [args]`. note `sys.argv` is
`['freecadcmd', '<script>', ...]` (script is argv[1], not argv[0]); `__name__`
is the file stem, **not** `"__main__"`, so detect direct runs by scanning argv
for the script name.

- **solids (true BREP):** `Part.makeBox(l,w,h)`, `Part.makeCylinder(r,h,pos,axis)`,
  `shape.cut/fuse/common(other)`. place with
  `Placement(Vector(...), Rotation(axis, deg))` (`from FreeCAD import Placement,
  Rotation, Vector`; the wiki's `FreeCAD as App` alias is a habit, not a rule).
- **STEP:** `Part.export([feature_objs], path)` works per part and for a
  compound; produces real solids (planar faces, not a mesh).
- **STL:** `Mesh.export([objs], path)`.
- **SVG (flat outline):** `importSVG.export([objs], path)` is headless ok for
  simple solids/sketches, but it raises `Part.OCCError: BRep_API: command not
  done` on drilled solids (Draft's `get_svg` can't `invert` some projected face
  wires). for such shapes project edges instead: `TechDraw.project(shape, dir)`
  returns `[visible, hidden, ...]` compounds; discretize the visible edges and
  write polylines yourself (see `util.export_svg_edges`).
- **DXF of a raw shape:** UNAVAILABLE offline (`importDXF.export` wants a
  downloadable addon; prints "DXF libraries not found" and writes nothing).
  route all dxf through TechDraw instead (below).
- **TechDraw (drawings + all dxf):** build a page with a template, add
  `DrawViewPart`s (set `.Source`, `.Direction`, and `.XDirection` for side
  views or projection fails), then `TechDraw.writeDXFPage(page, path)`.
  overall dimensions: `TechDraw.makeExtentDim(view, [], 0|1)` (0 horizontal,
  1 vertical) measures the whole view extent, with no need to name edges/vertices;
  add it to the page and offset its `.X/.Y` clear of the geometry. it is
  build-safe in either context. it creates a `DrawViewDimExtent` object, **not**
  a `DrawViewDimension` (so filter by the right TypeId when counting dims). the
  third dimension (thickness) just needs a second view along another axis; its
  extent dim then reads the thickness. a view dimension's `.X/.Y` are relative to
  its parent view, so the same offsets work wherever the view sits on the sheet.
  `importDXF.exportPage` raises `NameError(dxfExportBlocks)` headless, so don't
  use it. pdf/svg page export is gui-only (TechDrawGui).
- **templates + title block:** the bundled
  `/usr/share/freecad/Mod/TechDraw/Templates/Default_Template_A4_Landscape.svg`
  is empty (no border, no title block), so we ship our own under
  `resources/templates/`. a title block needs `freecad:editable="Name"` text
  elements (mirror `HowToExample.svg`'s element style; TechDraw's parser is
  picky); fill them with `tmpl.EditableTexts = {name: value}`. only the template
  border + editable text render in the **pdf**; `writeDXFPage` does not emit them.
- **gui pdf export races the threaded HLR:** in the gui TechDraw projects each
  `DrawViewPart` on a worker thread, so `exportPageAsPdf` fired too early drops
  the still-computing views from the pdf at random (`export_pdf` showed only the
  fastest 1-2 views). a view's `getVisibleEdges()` stays empty until its
  projection lands, so pump the event loop until **every** view reports geometry,
  then settle, before exporting. headless (`writeDXFPage`) is synchronous and
  unaffected.
- **point-to-point dimensions (e.g. hole pitch / diameter):** use
  `TechDraw.makeDistanceDim(view, "DistanceX", p1, p2)` with **scaled** 2d view
  points (model coord * `view.Scale`; it divides by the scale to report the true
  value). it returns `None` but creates+attaches a `DrawViewDimension` (grab it
  by diffing `doc.Objects`). two traps: (1) `makeDistanceDim3d` (3d points)
  reports correct values headless but **segfaults whenever the gui is up**
  (`addCosmeticVertex`), so the gui pdf path must use the 2d form; (2) even the
  2d form segfaults if called before the view's projected geometry exists; in
  the gui that means after the page is painted, so add these dims post-paint.
  see `util.add_hole_dims`.
- **VarSet (parameter panel, fcad's openscad customizer):**
  `doc.addObject("App::VarSet","Parameters")`, then
  `vs.addProperty("App::PropertyLength","outer_len","Size","")`; enums via
  `App::PropertyEnumeration` (set the list then the value). bind geometry with
  `obj.setExpression("Length","Parameters.outer_len")`. because part/hole
  **counts** are parametric, native recompute can't add/remove objects; use
  the rebuild macro to regenerate.
- **cross-document `App::Link`:** save the owner doc (`saveAs`) **before**
  adding links into other (saved) docs, or you get "Owner document not saved".
- **centered solids:** bake centering into geometry with
  `Part.makeBox(l,w,t, Vector(-l/2,-w/2,-t/2))`. do NOT use
  `shape.translate()` to center: translate sets the shape's Placement, which a
  later `shape.Placement = ...` assignment clobbers (parts end up offset by
  half their size and rotate about the wrong point).
- **Assembly workbench (real joints, headless):** modules live in
  `/usr/share/freecad/Mod/Assembly` (add to `sys.path`, then
  `import JointObject`). create with
  `asm = doc.addObject("Assembly::AssemblyObject","Assembly")` and
  `jg = asm.newObject("Assembly::JointGroup","Joints")`. components are
  `App::Link`s added via `asm.newObject("App::Link", name)` (pre-position with
  `.Placement`). ground a part:
  `gj = jg.newObject("App::FeaturePython", n); JointObject.GroundedJoint(gj, link)`.
  mate two parts:
  `j = jg.newObject("App::FeaturePython", n); JointObject.Joint(j, 0)` (0=Fixed;
  see `JointObject.JointTypes`), then
  `j.Reference1 = [asm, [a.Name+".", a.Name+"."]]` and likewise `Reference2`
  (whole-object refs use the origin JCS; `"Name.Face6"` etc. select elements).
  recompute the links **before** creating the joints, then position the assembly
  with `asm.solve()` alone; do **not** `recompute()` afterwards. the scheme
  grounds the parts flagged `grounded` and Fixed-mates every other part (screws
  included) to a datum, with links pre-positioned so the constrained solve is
  stable. **gotcha (touched joints / non-DAG):** each Fixed joint references the
  assembly container (`Reference1/2 = [asm, ...]`) while the container owns the
  joints, so the dependency graph has an `Assembly`<->joint cycle: a non-DAG.
  any `recompute()` once the joints exist re-runs that cycle: it can never settle
  the joints (they stay `touched`, logging `still touched after recompute` once
  per joint) and prints `graph must be a DAG`. so the build recomputes only
  before the joints exist, lets `solve()` (not a recompute) position the
  pre-placed parts, and `purgeTouched()`es every object before saving. the saved
  artifact then opens clean: no log flood, and the gui viewer needs no recompute.
  the cycle is inherent to the workbench's reference model (the solver, not the
  DAG recompute, positions parts); the gui still prints a **single** `graph must
  be a DAG` line when it opens the file and builds the dependency graph (benign).
- **FEM (gmsh mesh + CalculiX solve, headless):** build with `ObjectsFem`
  (`makeAnalysis`, `makeSolverCalculiXCcxTools`, `makeMaterialSolid`,
  `makeConstraintFixed/Force/Pressure/SelfWeight`, `makeMeshGmsh`), mesh via
  `femmesh.gmshtools.GmshTools(m).create_mesh()`, then run the solver with the
  **granular** `femtools.ccxtools.FemToolsCcx` calls
  `update_objects()`/`setup_working_dir()`/`write_inp_file()`/`ccx_run()`/`load_results()`.
  do **not** call `fea.run()`: it imports `FemGui`/VTK, and FreeCAD's bundled
  python has no `vtkmodules` (the gui/postprocessing modules do; the core solve and
  the `.frd` reader do not). the result is a `Fem::FemResultObject` (find via
  `o.isDerivedFrom("Fem::FemResultObject")`) exposing `vonMises`,
  `DisplacementVectors`, `NodeNumbers`, and `.Mesh.FemMesh` (`Nodes`, `Volumes`,
  `getElementNodes`) with no VTK. gotchas: (1) **internal units**: force is mN and
  pressure is mN/mm^2, so pass quantity strings (`"500 N"`, `"0.02 MPa"`), not bare
  floats, or you are off by 1000x. (2) a force **direction** needs no edge ref -
  set `force.DirectionVector` and leave `Direction` empty - but it does **not
  stay set**: a ConstraintForce re-derives it from its referenced face's outward
  normal every time it executes, and `analysis.addObject()` alone is enough to
  trigger that, never mind the later recompute. so assign it immediately before
  the solver reads it (`fem._reassert_directions`, called from `_run`), and
  confirm by grepping the emitted `*CLOAD` block for the right component column.
  set at construction it is silently replaced and the solve *succeeds*, loading
  the model along the face normal instead. (2a) the gmsh mesher defaults to
  `ElementOrder = "1st"`, and 4-node tets are grossly over-stiff in bending: a
  cantilever reads ~21% under its closed form and converges on it only from below.
  set `mesh.ElementOrder = "2nd"` - quadratic tets hit ~0.5% with a quarter the
  elements, and CalculiX solves C3D10 natively. anything checking FEM numbers
  should check them against a closed form, not against themselves; both of these
  bugs produced confidently wrong results that no self-consistency test could see.
  (3) **modal** = `solver.AnalysisType="frequency"` + `solver.EigenmodesCount`;
  results come back one `FemResultObject` per mode, each with `.EigenmodeFrequency`
  (Hz). (4) **nonpositive jacobian** (ccx exit 201): gmsh emits inverted tets on
  heavily-notched/thin solids regardless of mesh size, so detect a missing result
  (`result.Mesh is None`) and fail loudly rather than crash. (5) FreeCAD's material
  library has steels/aluminums/plastics with FEM properties but **no structural
  wood** card. read a card via
  `Materials.MaterialManager().getMaterial(uuid).Properties` and copy it onto
  `mat.Material`, or set `{E,nu,rho}` yourself for wood. (6) **neither tool is
  bounded by default.** `GmshTools.create_mesh()` is `run(True)`, whose wait is
  `QProcess.waitForFinished(-1)` - forever - and a mesher that failed reports it
  only by leaving `FemMesh` empty; gmsh has run 14 minutes at 7.6 GB on a
  fastened assembly without finishing. use the granular seam instead
  (`prepare()`, `compute()` returns the `QProcess`, `waitForFinished(ms)`,
  `kill()`), the same way the solver is driven. CalculiX is worse because it does
  finish, on a bigger machine: peak RSS measured on a 2nd-order steel cantilever
  goes 26 MB at 1671 nodes to 2.25 GB at 100855, which is `~500 * nodes^(4/3)`
  bytes and not linear - a direct sparse factorization's fill-in. estimate it
  before running ccx (`fem._preflight`); it has been OOM-killed at exit -9 three
  times in one session. when bounding either with an rlimit, note that
  `RLIMIT_DATA` counts untouched mappings and OpenBLAS reserves a buffer pool up
  front - 2.29 GiB of VmData against 0.03 GiB resident on the smallest solve,
  constant, and unaffected by thread count - so a ceiling under ~3 GiB stops
  FreeCAD starting at all rather than stopping anything from running away.
  (7) **neither tool is reproducible at its default thread count, and one of them
  is wrong.** gmsh's parallel 3d algorithm reseeds - four identical runs of a
  358k-node mesh gave four node counts, and `Mesh.RandomSeed` does not help
  because the variation is thread interleaving - and FreeCAD sets
  `General.NumThreads` to the cpu count. worse, ten CalculiX runs of a single
  *byte-identical* `.inp` at 16 threads returned four different tip deflections
  spanning 6.5%, the low ones 6.4% under a closed form the 1-thread run matched
  to 0.25%. so pin both: `NumOfThreads` in the Gmsh preference group (and restore
  it afterwards - it is the user's, and a gui left meshing single-threaded
  because a build ran is not fcad's call), and `OMP_NUM_THREADS` by running ccx
  yourself, because `start_ccx` forces it to the cpu count and `AnalysisNumCPUs`
  only ever *raises* it - setting that preference to 1 selects the cpu-count
  branch. costs ~15% of the mesh step and 1.2-1.6x of the solve. anything
  comparing FEM numbers between runs is otherwise comparing noise, and this bit
  `tests/test_fem.py`'s own beam-theory check intermittently for a long time
  before it was tracked down.
- **gui-only:** `obj.ViewObject` (colors), Draft layers, anything in
  `*Gui` modules. guard with `if App.GuiUp:`. `freecadcmd` is headless.
- disable `.FCBak` clutter: set `Preferences/Document/CountBackupFiles = 0`.

## style

lowercase comments and output (CAPS for acronyms/emphasis); comments explain
*why*. define magical constants as module-level globals. keep functions under ~50
lines and prefer pure functions. extend existing components rather than adding new
ones; keep external dependencies minimal.

## releases

releases are cut from `master`. never regress a capability in
[REQUIREMENTS.md](REQUIREMENTS.md). update REQUIREMENTS/DESIGN/README when behavior
or architecture changes, and tick the item in [ROADMAP.md](ROADMAP.md).
