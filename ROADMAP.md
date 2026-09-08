# roadmap

## later

- **FEM assembly contact.** the assembly FEM target currently fuses the structural
  solids into one rigidly-bonded body (a sane default). add bonded-tie (`*TIE`,
  `makeConstraintTie`) between touching face-pairs for per-part materials and joint
  fidelity, and optional nonlinear `makeConstraintContact` for slip/separation
  (both confirmed available).
- **publish to PyPI.** currently local/git install only.

## done

- **cut-list optimization.** the bom said how many pieces of what length but not
  what to buy. `fcad build cutlist` now packs each profile's bom rows into the
  stock lengths purchasable for it and writes `dist/<name>-cutlist.csv` (per cut
  pattern: stock length, board count, pieces per board, offcut) plus a summary
  line per profile. `src/fcad/cutlist.py` is pure stdlib and FreeCAD-free, so the
  cli and the in-freecad builder share it: an exact dp over the demand vector
  while the state x pattern product stays inside a fixed budget, first-fit-
  decreasing above it, with `Plan.exact` recording which ran so a greedy plan
  never poses as an optimum. a saw kerf is charged between adjacent cuts and an
  optional trim allowance against each board; a piece no stock can hold is
  reported, not dropped. stock lengths are market knowledge, so they come from an
  optional `STOCK` project global ({profile: [lengths]} or one list for all) that
  also decides *which* profiles are cut from stock at all — fasteners declare
  none and are skipped — with `--stock`/`--kerf`/`--trim`/`--objective` overriding
  per invocation for what-if runs (`--stock 2x6=8ft,12ft`, mm when unsuffixed).
  validated on ../planter: 33 pieces of 2x6 in four distinct lengths pack into 9
  boards at 1.3% waste. covered by `tests/test_cutlist.py` (a fixture whose
  optimum is unique and hand-checkable, built through the real assembly builder,
  plus kerf/trim accounting, the env overrides, oversize pieces, and the greedy
  fallback's demand conservation).
- **reduce per-project boilerplate: single-file projects.** a project can now be
  one file whose every line is geometry, either a directory's `project.py` or a
  standalone **`.fcad`** file (python, named directly `fcad -p planter.fcad ...` or
  auto-discovered as the sole `.fcad` in a dir; loaded via a `SourceFileLoader`
  since `.fcad` isn't a source suffix, its dir the root and its stem the name).
  fcad assembles the `Project` from conventional module globals (`PARAMS` +
  `compute`, plus optional `PARAM_META`/`FEM`/`MATERIAL`/`MATERIALS`/`from_spec`/
  `profile`/`NAME`) when no explicit `PROJECT = fcad.Project(...)` is present (that
  form, and the directory/`project.py` form, stay fully supported; R1.1 is
  additive). chosen approach was **python + inference**, no second config format.
  delivered: `fcad.Part`, the ready-made spec (a `solid` thunk + optional
  `profile2d`, with `qty`/`length` derived); `Project` became a thin adapter that
  normalizes `compute` (bare list or dict), falls back `from_spec`/`profile` to the
  spec's own `solid()`/`profile2d`, and infers the varset schema (property type
  from each default's python type, floats being lengths) + enum choices from
  `PARAMS`+`PARAM_META`; `loader` introspects the module; the assembly auto-grounds
  its first part when nothing is flagged; fem materials gained a project-wide
  `MATERIAL` default + a project `MATERIALS` registry merged over fcad's built-ins
  (so a material is stated once, never per case). `fastener_holes` stayed a
  distinct *optional* spec field (read via `getattr`) rather than merging into
  `holes`, to keep the dimensioned drawing showing only the drainage pattern.
  ../planter collapsed from 4 modules (`project.py` + `params`/`parts`/`assembly`)
  into a single `planter.fcad` (~430 lines, all design): dropped its `PROJECT`
  wiring, `SCHEMA`, `ENUM_CHOICES`, `WOOD` dict (now `MATERIAL = "wood"`) and
  `PartSpec.qty`. re-verified end to end through the cli: `make all`/`check` clean,
  bom byte-identical, `fem floor_slat` still 4.91 MPa / 0.176 mm /
  632/1722/1778 Hz. covered by `tests/test_contract.py`: a synthetic single-file
  project loaded + built end to end (inferred types/groups, enum dropdown,
  fully-constrained defining sketch, auto-ground, computed bom, material default +
  registry), through both the directory and the `.fcad`-file load paths.
- **FEM image + animation generation.** `fcad fem [TARGET]` meshes a built target
  (gmsh) and solves it with CalculiX headlessly, with no display and no VTK (it
  drives the granular `write_inp_file`/`ccx_run`/`load_results` tools, never
  `fea.run()`), writing the analysis doc plus a numpy result bundle
  (`dist/<target>.fem.{FCStd,npz}`). `fcad fem-render` draws the deformed surface
  colored by von Mises stress; `fcad fem-animate` writes a deformation sweep plus
  one animation per eigenmode (`--modal`/`--modes K`).
  analysis inputs flow through an optional `Project.fem`
  descriptor (material as a FreeCAD library card or `{E,nu,rho}` dict; fixed faces,
  loads, self-weight, mesh size, modes) with **geometry-predicate** face selection
  (`fcad.fem_select`, resolved to live face refs in-process so no fragile "FaceN"
  index crosses a boundary); absent a descriptor a default analysis (fix the base,
  self-weight, steel card) still yields a result, and an unsolvable mesh fails
  loudly. the `assembly` target fuses the structural solids into one bonded body;
  the boundary surface is extracted from the tet mesh for the renderer. validated
  end to end on ../planter's floor_slat (wood, soil pressure: 4.9 MPa, 0.18 mm sag,
  modes 634/1719/1772 Hz). covered by `tests/test_fem.py`.
- **reverse-engineerable drawings.** the part/assembly drawings (DXF + PDF) are
  now a projection-aligned third-angle sheet (top above front, right beside it)
  plus an isometric pictorial in the free cell, shared by the headless DXF and
  gui PDF paths (`util._page`). one shared scale snapped to a standard ratio
  (1:1/1:2/1:5/1:10/2:1...); width dim above each view, height dim to its right,
  the view label below; multi-row hole patterns dimensioned; and a shipped A4
  title-block template (`resources/templates/`, since the bundled default is
  blank) filled with part/project/scale/units/size. covered by
  `tests/test_drawing.py`. fixed along the way: the iso was upside down (chose a
  front-top-right direction with +Z up); the gui pdf dropped still-projecting
  views (now waits on each view's threaded HLR via `getVisibleEdges`); and the
  template constant still pointed at the blank stock template.
- **`fcad help [COMMAND]`.** `help` is now a first-class command: bare `fcad help`
  prints the top-level usage (same as no args / `-h`), and `fcad help COMMAND`
  prints that command's usage (an unknown command errors). covered by
  `tests/test_help.py`.
- absorbed the FreeCAD 1.1.1 api notes and the headless/gui implementation
  gotchas into `CONTRIBUTING.md` (moved out of the planter repo, where they were
  documenting fcad's instrumentation rather than the planter design).
- **bake a clean assembly so viewing never mutates it.** the Assembly
  workbench's Fixed joints reference the assembly container, forming a non-DAG
  `Assembly`<->joint cycle a topological recompute can never settle, so every
  recompute after the joints exist re-ran it, spamming "still touched after
  recompute" (and "graph must be a DAG") once per joint, during build *and* on
  `fcad view`'s load recompute. fixes: (1) `build_jointed_doc` recomputes only
  before the joints exist, positions the pre-placed parts with `solve()` alone
  (no post-joint recompute), and purges residual touched flags before saving, so
  the build is silent and the artifact opens with 0 touched; (2) the gui view
  paths open read-only (no recompute) and clear the gui `Modified` flag after
  forcing visibility, so closing no longer prompts to save. only a single benign
  "graph must be a DAG" line remains when the gui opens the file (inherent to the
  joint reference model). covered by `tests/test_assembly.py` (0 touched on open
  + no spam/DAG emitted during build).
- **uv-installable package + unified cli.** fcad is now a self-contained,
  `uv tool install`-able package with one `fcad` console script; all scripts
  and macros live inside it (no sibling-checkout shell-outs). see
  [DESIGN.md](DESIGN.md).
  - governance docs (ROADMAP, REQUIREMENTS, CONTRIBUTING, DESIGN)
  - `pyproject.toml` + `src/fcad` package; `config.py` + `freecad/_entry.py`
  - the modules moved into the package with package-relative imports
  - `cli.py` (the unified argparse cli) and `diff.py` (worktree orchestration,
    porting the old shell scripts); the rebuild macro ships as package data and
    installs via `fcad install-macro`
  - 3d diff split into `diff` / `diff-build` / `diff-open` (folded in from WIP)
  - e2e tested against ../planter (build, check, render, diff regression);
    planter's Makefile + project.py migrated to the installed cli/api, and its
    docs (README/DESIGN/CONTRIBUTING/ROADMAP) updated off `fcbuild`->`fcad`
- unified `fcad` bash cli; rename preview->view, view->view-parts
- gui rebuild macro and 3d-diff script
- initial import: reusable FreeCAD build + instrumentation
