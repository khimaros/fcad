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
  diff.py         worktree orchestration for `diff` (runs under the cli's python)
  fem_select.py   geometry-predicate face selectors for FEM (pure stdlib)
  freecad/        everything that imports FreeCAD (runs under freecadcmd/freecad)
    _entry.py     in-freecad bootstrap: put the package on sys.path, route a command
    dispatch.py   build/validate targets -> part/assembly builders
    build_parts.py  build_assembly.py  util.py
    view.py  view_parts.py  export_pdf.py  diff_doc.py
    fem.py        headless mesh (gmsh) + solve (CalculiX) -> numpy result bundle
  render/         plain-python, numpy/matplotlib (the [render] extra)
    render.py  animate.py  fem_render.py  fem_animate.py
  resources/macros/rebuild.FCMacro
  resources/templates/fcad_A4_landscape.svg
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
  runs it through a different interpreter.
- **render / animate / fem-render / fem-animate**: imported and called in-process
  under the cli's own python (these are the commands with pip dependencies and no
  FreeCAD need).
- **diff**: `diff.py` builds the git-HEAD geometry in a throwaway `git worktree`
  (a nested `freecadcmd ... build step`), then execs the gui against `diff_doc.py`.
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
- the gui-only split (`export_pdf`, `diff_doc`, the view-readiers) exists because
  TechDraw's pdf/svg export and Draft layer colors live in the `*Gui` modules,
  which only exist in a gui session. `export_pdf` must wait for each view's
  threaded HLR projection to land (`getVisibleEdges`) before exporting, or the
  still-computing views drop out of the pdf at random.
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
