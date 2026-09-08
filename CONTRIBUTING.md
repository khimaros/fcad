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
- `dconf-CRITICAL ... /run/user/1000/dconf` messages are sandbox env noise,
  unrelated to the model.
- **view state:** freecadcmd writes no GuiDocument and has no `ViewObject`, so a
  freecadcmd-built `.FCStd` opens in the gui with every object hidden (visibility
  is gui-only state that the headless build cannot persist): a part looks empty,
  holes and all. `view_parts.py` (`fcad view parts`) opens each part, shows the
  solid / hides its sketch, frames it, and re-saves so the file carries a
  GuiDocument; it does not exit, leaving the docs open for inspection. for the
  save to make the file open straight to the model, the part `.FCStd` must
  contain only the part: `build_one` saves it *before* the dxf/drawing/sketch
  exports (which add TechDraw pages).
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
  `App.Placement(App.Vector(...), App.Rotation(axis, deg))`.
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
  `Part.makeBox(l,w,t, App.Vector(-l/2,-w/2,-t/2))`. do NOT use
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
  floats, or you are off by 1000x. (2) a force **direction** needs no edge ref: set
  `force.DirectionVector` directly (it survives `update_objects`); `Direction` left
  empty. (3) **modal** = `solver.AnalysisType="frequency"` + `solver.EigenmodesCount`;
  results come back one `FemResultObject` per mode, each with `.EigenmodeFrequency`
  (Hz). (4) **nonpositive jacobian** (ccx exit 201): gmsh emits inverted tets on
  heavily-notched/thin solids regardless of mesh size, so detect a missing result
  (`result.Mesh is None`) and fail loudly rather than crash. (5) FreeCAD's material
  library has steels/aluminums/plastics with FEM properties but **no structural
  wood** card. read a card via
  `Materials.MaterialManager().getMaterial(uuid).Properties` and copy it onto
  `mat.Material`, or set `{E,nu,rho}` yourself for wood.
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
