# design

## the problem fcad solves

FreeCAD ships its own bundled python and runs as either a headless console
(`freecadcmd`) or a gui (`freecad`); it is not pip-installable. some of fcad's
work must run inside that bundled interpreter (anything importing `FreeCAD`,
`Part`, `TechDraw`, the Assembly workbench), some needs the gui specifically
(TechDraw pdf/svg export, the interactive 3d diff, opening documents view-ready),
and some is plain python with ordinary pip dependencies (the matplotlib renderer).

so fcad is **one package and one cli over three interpreters**, with the cli
picking the right one per command so the user never has to.

## layout

```
src/fcad/
  cli.py          the unified argparse cli; the `fcad` console script
  config.py       resolve project/name/dist + freecad binaries (stdlib only);
                  the project path is a dir (project.py) or a single .fcad file
  project.py      the Project descriptor + the ready-made `Part` spec; infers
                  the varset schema/enum choices from PARAMS when not given
  loader.py       load a project (a dir's project.py or a .fcad file) and return
                  its Project, declared explicitly (PROJECT) or by convention
  cutlist.py      1d bin packing of the bom into buyable stock (pure stdlib)
  limits.py       what a child may spend: memory budget + estimate, gmsh wall
                  clock, the inherited rlimit (pure stdlib, imported both sides)
  _run.py         spawn the in-freecad entry: own session, inherited ceiling
  diff.py         worktree orchestration for `diff` (runs under the cli's python)
  fem_select.py   geometry-predicate face selectors for FEM (pure stdlib)
  freecad/        everything that imports FreeCAD (runs under freecadcmd/freecad)
    _entry.py     in-freecad bootstrap: put the package on sys.path, route a command
    dispatch.py   build/validate targets -> part/assembly builders
    build_parts.py  build_assembly.py  util.py
    view.py  view_parts.py  export_pdf.py  diff_doc.py
    api_docs.py   introspect the installed FreeCAD -> a markdown api reference
    fem.py        headless mesh (gmsh) + solve (CalculiX) -> numpy result bundle
  render/         plain-python, numpy/matplotlib (the [render] extra)
    render.py  animate.py  fem_render.py  fem_animate.py
  resources/macros/rebuild.FCMacro
  resources/templates/fcad_A4_landscape.svg
  resources/skills/freecad-python/    installed by `install-skill`:
    SKILL.md                          the prose
    wiki/                             52 curated wiki pages (CC0) + NOTICE.md
```

## how the cli reaches FreeCAD

`config.py` is pure stdlib so it imports under every interpreter. the cli resolves
the project/name/dist/binaries once, exports them into the environment, then:

- **headless + gui commands**: exec the chosen freecad binary against
  `freecad/_entry.py` as a script, passing the command name:
  `freecadcmd <...>/_entry.py build stl`, `freecad <...>/_entry.py view`.
  `_entry.py`
  inserts the installed package's parent directory onto `sys.path` (so FreeCAD's
  bundled python can `import fcad`), then dispatches the command to the matching
  `fcad.freecad` handler. there is exactly one copy of the code; FreeCAD just
  runs it through a different interpreter. the child is spawned into its own
  session, so the tools *it* spawns share one process group and a single signal
  reaps all of them - killing freecadcmd alone leaves a gmsh behind still holding
  gigabytes. the cost of that session is the terminal's ctrl-c, which no longer
  reaches a child outside the foreground group, so `_run.supervise` relays it.
- **render / animate / fem-render / fem-animate**: imported and called in-process
  under the cli's own python (these are the commands with pip dependencies and no
  FreeCAD need).
- **diff**: `diff.py` builds the git-HEAD geometry in a throwaway `git worktree`
  (a nested `freecadcmd ... build step`), then execs the gui against `diff_doc.py`.
  the nested build must be repointed at the worktree's *own* copy of the design:
  the cli has already exported `FCAD_ENTRY` as an absolute path into the working
  tree, so overriding `FCAD_PROJECT` alone would have a `.fcad` project silently
  diff itself. `freecadcmd` exits 0 even when the script raised, so `diff.py`
  judges success by whether the artifact appeared, not by the return code.
- **git-diff**: the external diff driver `install-git` registers, so `git diff` on
  a `.fcad` file opens the 3d diff. it shares the compute step with `diff` but not
  the worktree: git already hands it both revisions as files, which is both simpler
  and more general (any revision pair, not just HEAD). a `.fcad` file is the whole
  project, so each side builds in isolation from a copy of that one file, with
  `dist/` landing beside it. it must exit 0 whatever happens - git treats any other
  status as fatal - so failures are printed, which is where a diff driver's output
  belongs anyway.
- **fem**: a headless `freecadcmd` command driving the FEM workbench's gmsh mesher
  and CalculiX solver. it deliberately avoids `fea.run()` and every `*Gui`/VTK
  module (neither exists headless), calling the granular `write_inp_file` /
  `ccx_run` / `load_results` tools instead, and reading the result fields
  (`vonMises`, `DisplacementVectors`) straight off the result object. it writes a
  numpy `.npz` (boundary surface + fields + mode shapes) so the plain-python
  renderers never import FreeCAD: the same hand-off boundary as the STL renderers,
  one step earlier. face selection resolves geometry predicates to live face refs
  in this same process, so a fragile "FaceN" index never crosses a process or
  geometry boundary (`fem_select` stays pure stdlib and importable everywhere).
  two of its settings are corrections to FreeCAD defaults that are silently wrong
  for fcad's purpose rather than merely coarse. the mesh is **2nd order but
  straight-edged**: linear tets are over-stiff in bending (~20% low), while the
  curved quadratic tets gmsh produces by default invert around small features and
  CalculiX refuses them outright, so pinning midside nodes to edge midpoints buys
  the quadratic displacement field without the inverted elements. and a force's
  **direction is re-asserted immediately before each solve**, because a
  ConstraintForce recomputes `DirectionVector` from its referenced face's normal
  whenever it executes - a load set up once and then recomputed points somewhere
  else entirely, and solves happily. the same shape shows up in the *output*: the
  reported peak von Mises sits on a singularity - a clamped face, the sharp
  internal corner of a drilled hole - where linear elasticity has no finite answer
  and the value is a function of the mesh, not the part, so the bundle carries
  `von_mises_p95`/`p99` next to it. all three defects share a shape worth
  remembering: a plausible, confidently-reported, wrong number. that is why the
  FEM tests assert against closed-form beam theory instead of a previous run.

  the fourth failure is not a wrong number but no number at all, arriving late:
  a solve is the one fcad step whose cost is set by the mesh rather than by the
  model, and both tools will happily consume the machine. so `limits.py` bounds
  them in two different registers, and the split is the point. the *estimate* -
  `ccx_bytes(nodes)` against `budget()`, checked after meshing and before
  CalculiX starts - is the diagnostic one: it is what can name the node count and
  say which knob to turn, and it costs seconds rather than the minutes a doomed
  solve spends before the OOM killer reaches it. the *rlimit* is the crash
  barrier: it cannot explain anything, but it is inherited, which is the only
  reason it reaches gmsh and ccx at all - they are grandchildren FreeCAD spawns,
  not children fcad does. that inheritance also fixes the ceiling's floor. it
  must clear what the toolchain reserves before doing any work (OpenBLAS maps a
  buffer pool up front and RLIMIT_DATA counts untouched mappings: 2.29 GiB of
  VmData against 0.03 GiB resident on a 1671-node solve), or the ceiling stops
  bounding runaways and starts stopping FreeCAD from starting. the mesher gets a
  wall clock instead of an estimate, because its cost is not knowable from
  anything fcad holds before it runs; bounding it means running gmsh through
  `GmshTools`' granular seam - `prepare`/`compute`/`waitForFinished` - rather
  than `create_mesh()`, which waits forever and reports failure only by leaving
  the mesh empty. the same granular-seam argument as the solver, for the same
  reason.

- **api-docs**: the one headless command that loads no project, because it
  documents the *toolchain* rather than a model. it runs under `freecadcmd`
  precisely so the reference describes the same binary every build uses. the
  design constraint is that it may not know anything: a name is either read off a
  live object or absent, never transcribed, which is what keeps it from drifting
  the way prose documentation does. two consequences shape the module. it must
  preload the workbench modules, since FreeCAD registers a module's TypeIds only
  once imported and `supportedTypes()` otherwise reports `App` plus `Image` while
  looking perfectly healthy - a silent near-empty result, so the test asserts
  coverage rather than exit status. and it documents by *signature* what it
  cannot instantiate: factories taking a parent object (an elmer equation wants
  its solver, a mesh region its mesh) cannot be called blind, and their signature
  is the answer a caller actually needs anyway.
- **install-skill** is why `api-docs` is a command rather than a script kept
  beside the skill. an agent skill about scripting FreeCAD is half prose, which
  can be committed, and half a reference to a specific FreeCAD, which cannot -
  it only exists once the skill meets a machine. so fcad ships the prose as
  package data and generates the other half on install, which also makes the
  update path the same command. staleness is keyed on `freecadcmd --version`
  (50ms) rather than on regenerating and diffing, so the common no-op is two
  orders of magnitude cheaper than the work it skips. it overwrites only what it
  ships and never deletes, so a fuller wiki mirror or a user's notes in the same
  directory survive an install - which is why the wiki goes in file by file
  rather than as a tree replace.
  the skill carries **both** a generated reference and 52 curated wiki pages
  because they answer different questions and neither substitutes: `api/` says a
  property exists, its type and its permitted values; the wiki says what it means
  and how it is normally used. measured on eight real api questions from fcad's
  own development, the *full* 2630-page wiki missed five outright - it documents
  the gui, so the property behind a checkbox is frequently unnamed anywhere in
  it. the reverse gap is just as sharp: no amount of introspection yields the
  FeaturePython lifecycle or how a Sketcher constraint is constructed. the subset
  is chosen rather than complete because 599 of those pages are sub-600-byte
  stubs and 926 are gui references with no python at all; ~2% of the files carry
  the scripting value, which is the difference between 650 KB of package data
  and 22 MB of it.

the project is loaded by `loader.py` from `FCAD_PROJECT` (the file to exec is
carried separately in `FCAD_ENTRY` when it is a `.fcad`). a project is either a
directory's `project.py` (imported by name, its dir on `sys.path`) or a standalone
`.fcad` file (python, exec'd from its path via a `SourceFileLoader` since `.fcad`
is not a registered source suffix). either may hand fcad an explicit
`PROJECT = fcad.Project(...)` or just the conventional globals (`PARAMS` +
`compute`, plus optional refinements); in the latter case `loader` assembles the
`Project` and `Project` infers the rest (varset schema from each default's python
type, enum choices, per-part `qty`/`length`). all four combinations are
first-class; the convention + `.fcad` pair is what lets a whole project be a
single file.

## why the modules are split the way they are

- `freecad/` modules may import `FreeCAD` at module load; nothing outside it may,
  so the cli and `config` stay importable under plain python (and at install time).
- `util.py` centralizes the headless FreeCAD gotchas confirmed on 1.1.1 (legacy DXF
  exporter preference, TechDraw `writeDXFPage` instead of the unavailable
  `importDXF.exportPage`, scale/layout math TechDraw only computes lazily in the
  gui).
- the drawing sheet (`_page`) is built to be reverse-engineerable: views carry a
  `(col, row)` grid cell and are laid out projection-aligned (third-angle: top
  above front, right beside front) with an isometric pictorial in the free cell,
  a single shared scale snapped to a standard ratio, the width dimension above
  each view and the height dimension to its right (leaving the space below for
  the view-label caption), and a shipped A4 title-block template
  (`resources/templates/`) since the bundled default is blank. the same `_page`
  feeds both the headless DXF (`make_drawing`) and the gui PDF (`export_pdf`).
- `cutlist` is pure stdlib and never imports FreeCAD, even though the `cutlist`
  build target runs inside `freecadcmd`: the packing consumes the bom's numbers,
  not geometry, so the same module is importable by the cli (for its flags and
  defaults) and by the builder. it carries two solvers because honesty about
  optimality matters more than always claiming it: a dp over the demand vector
  while the state x pattern product stays inside a fixed budget, first-fit-
  decreasing beyond it, with `Plan.exact` recording which one ran. the stock
  catalog is a project global rather than an fcad table because "what lengths can
  i buy" is a fact about a supplier, which is exactly the kind of model knowledge
  the instrumentation must not hold.
- `diff_doc` diffs **per part**, not per assembly. the kernel's boolean cost is
  superlinear in the combined faces of its two arguments, so cutting a compound of
  every changed solid against another compound of every changed solid does not
  finish on a real assembly (interpenetrating fasteners make the intersection graph
  dense). instead each part type is cut against its own previous version once, in
  the part-local frame, and the green/red/grey results are placed at each instance:
  the boolean count follows the number of changed part *types*, and instances that
  merely appeared, vanished or moved need no boolean. that requires both revisions'
  placements, which the diff cannot recompute (the old ones came from a revision of
  the project's code that is gone), so every assembly build records them in
  `dist/<name>-placements.json` and the diff reads both sides back. pairing by part
  also stops parts contaminating each other, which the whole-assembly cut could not:
  it subtracted every old solid from every new one regardless of provenance.
- the gui-only split (`export_pdf`, the view-readiers) exists because TechDraw's
  pdf/svg export and ViewObject colors live in the `*Gui` modules, which only
  exist in a gui session. `export_pdf` must wait for each view's threaded HLR
  projection to land (`getVisibleEdges`) before exporting, or the still-computing
  views drop out of the pdf at random. the diff is split along that same line but
  the other way round: `diff_doc` is purely headless (it bakes the three layers as
  plain object groups and saves them) and `view_diff` is purely gui (it colors
  those groups on open). neither carries a `GuiUp` branch for the other's job.
- builds save fully baked, viewers open read-only. the Assembly workbench's
  Fixed joints reference the assembly container, so the joint graph is a non-DAG
  cycle a topological recompute can never settle, so the joints would stay
  `touched` forever (one "still touched after recompute" log line each). part
  positions are already baked into the link placements, so `build_jointed_doc`
  purges the touched flags before saving, and `view`/`view_diff` open without
  recomputing. the only gui-session mutation left is view state (visibility,
  colors, fit), which `freecadcmd` cannot bake because it has no `ViewObject`.

## the rebuild macro

part and hole counts are parametric, so a native expression recompute cannot add
or remove objects, so regenerating from a script is the idiomatic answer. the
macro reads the `Parameters` VarSet of the active document and re-runs the build.
it `import fcad`s the installed package (the path is baked in by
`install-macro`), so it needs no sibling `../fcad` checkout.
