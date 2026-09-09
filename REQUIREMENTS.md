# requirements

fcad is reusable instrumentation that drives FreeCAD to build, export, inspect
and validate a parametric, code-defined model. it knows nothing about any
particular model: a project supplies its geometry and parameters through a single
`Project` descriptor. these requirements are frozen capabilities: a release must
never regress on them.

## R1 - project contract

- R1.1 a project is a directory holding a `project.py`, or a single `.fcad` file
  (python by another name: a whole project in one file). it may expose an
  explicit `PROJECT = fcad.Project(...)`, or, equivalently, the conventional
  module globals `PARAMS` + `compute` (plus optional `PARAM_META`, `FEM`,
  `MATERIAL`, `MATERIALS`, `STOCK`, `from_spec`, `profile`, `NAME`), from which
  fcad assembles the `Project`, inferring the varset schema (property type from
  each default's python type), enum choices, and per-part `qty`/`length`. either way
  fcad reads everything through one `Project` and carries zero model knowledge.
  the explicit form is never removed.
- R1.1a a part `compute` returns is duck-typed: fcad reads `name`, `placements`,
  `profile` (a bom label), `holes`, `grounded`, `embeds`, and realizes it via a
  `solid()` method (+ optional `profile2d`) or the project's `from_spec`/`profile`.
  `fcad.Part` is the ready-made part; a project may use its own type exposing the
  same surface. an assembly with no part flagged `grounded` anchors its first part.
- R1.2 the project location is resolved from (in order) the `--project` flag, the
  `FCAD_PROJECT` env var, then the current directory; it may name a directory or a
  `.fcad` file, and a directory holding a sole `.fcad` (and no `project.py`) is
  treated as that file. the project *root* (where `dist/` is created) is the
  directory, or the file's parent for a `.fcad`. the output stem (`--name`/
  `FCAD_NAME`) defaults to the directory basename, or the `.fcad` file's stem; the
  output dir (`--dist`/`FCAD_DIST`) defaults to `<root>/dist`.

## R2 - build & export (headless)

- R2.1 `build` produces, under `dist/`, for the parts and/or the assembly: the
  `.FCStd` documents, neutral geometry (STEP, STL), outline SVG, dimensioned DXF,
  dimensioned TechDraw drawings (DXF), defining-sketch exports (SVG + DXF, parts
  only), and a CSV bom and cut list (assembly only). every build that runs the
  assembly stage also records each part's instance placements in
  `dist/<name>-placements.json`, whatever formats were selected: the 3d diff (R4.4)
  pairs instances across two revisions and cannot obtain the older revision's
  placements by calling the project's `compute`, which no longer exists in that form.
  it records each part's `grounded`/`embeds` flags beside them in
  `dist/<name>-parts.json`, for the same reason and for the assembly animation
  (R4.3), which sequences on both and also runs outside FreeCAD. the two are
  separate files because the placements file is `{part: [placement]}` and a
  top-level key that is not a part name would be a trap for every reader of it.
- R2.2 `build` accepts target tokens that select a subset:
  `parts assembly step stl svg dxf drawings sketches bom cutlist`; no token means
  `all`. multiple tokens union. stage-specific tokens keep their meaning
  (`sketches` parts-only, `bom`/`cutlist` assembly-only).
- R2.3 the assembly `.FCStd` is a true Assembly-workbench assembly: each instance
  is an `App::Link` into its part file, grounded or fixed-jointed and solved.
- R2.4 every build runs headlessly with no display (`freecadcmd`).
- R2.5a a saved `.FCStd` opens showing its model, framed, on a plain double-click,
  with no viewer command and no display involved in producing it. the build bakes
  the view state (a `GuiDocument.xml` in the zip): the part solids, the assembly's
  component links and the assembly container are visible, the defining sketches,
  varsets and origin geometry are hidden, and the camera is an isometric fit of
  everything visible.
- R2.5 the `cutlist` target packs each bom profile's pieces into the stock lengths
  purchasable for it, writing `dist/<name>-cutlist.csv`: per cut pattern, the
  stock length, how many boards take that pattern, the pieces cut from each, and
  the offcut. a saw kerf is charged between adjacent cuts and an optional trim
  allowance against each board. which lengths exist is market knowledge, never
  fcad's: they come from the project's `STOCK` (a `{profile: [lengths]}` map, or
  one list for every profile) and only a profile declared there is planned, so
  fasteners and bought parts are excluded. `--stock`/`--kerf`/`--trim`/
  `--objective` override per invocation without editing the project. a plan is
  exact where the search is small enough to prove and first-fit-decreasing above
  that, and says which it was; a piece longer than every stock length is reported,
  never silently dropped.

## R3 - validate (headless)

- R3.1 `check` fails (non-zero exit) if any structural solids interpenetrate
  (parts flagged `embeds`, e.g. screws, are excluded), if any assembly component
  is neither grounded nor jointed, or if any defining sketch is not fully
  constrained. a failure states its reason - the overlapping pairs and their
  volumes, the unconstrained components, the loose sketches - and that report
  reaches stdout whether it is a terminal, a pipe, a file or CI. an exit status
  with no reason is a failure of this requirement.
- R3.2 `precommit` builds everything, then runs `check`.

## R4 - inspect

- R4.1 `view` opens the assembly in the gui with every component visible and the
  view fitted. `view parts` opens the built part files view-ready and stays open;
  `--part NAME` opens a single part.
- R4.2 `render [TARGET]` writes an offscreen shaded PNG of a built STL under plain
  python3 (no display). `TARGET` is `assembly` (default) or a part name.
- R4.3 `animate [TARGET]` writes an MP4 + GIF of a built model under plain
  python3, over a camera axis and a subject axis.
  the **camera** is `orbit` (a full turn while the elevation sweeps, so every
  side plus top and bottom comes into view; the default), `turntable` (that turn
  held level) or `fixed`. it is the same camera every other renderer uses, so
  `FCAD_ELEV`/`FCAD_AZIM`/`FCAD_TILT` mean one thing across all of them: the
  elevation of a still or the centre of a sweep, the azimuth of a still or the
  start of a turn, and the sweep amplitude (0 holding the elevation, which makes
  an orbit a turntable).
  the **subject** is `assemble` (the model building itself, each instance flying
  in from an exploded position to where it belongs) or `static` (the model whole,
  the camera doing the work). unspecified, it is `assemble` for the assembly and
  `static` for a single part, which has nothing to assemble; `--subject static`
  on the assembly must remain available, since orbiting the finished model is a
  distinct thing to want. `assemble` names a part target is an error, not a
  silent whole-model animation.
  a clip's length is derived from what it shows, not set: an assembly's follows
  from its part count and how many parts move at once, so twenty arriving singly
  runs far longer than three. `--speed` multiplies that, `--fps` sets the frame
  rate, and the frame count is their product rather than a third setting;
  `--seconds` forces an exact length when one is needed.
  `assemble` is driven by the part STLs plus `<name>-placements.json` and
  `<name>-parts.json`, never the assembly STL, which is one welded lump with no
  part boundaries left in it. the arrival order (`--order`) defaults to
  `grounded`: the parts flagged `grounded` first, then repeatedly the lowest part
  that touches what is already placed, with `embeds` parts held to the end. no
  part may arrive floating, a fastener must not arrive before the structure it
  fastens, and the sequence must climb rather than ring outward from the anchor,
  so a model is watched being built a course at a time. a model with no such flags
  recorded, or none to assemble, falls back or fails loudly rather than animating
  something misleading.
- R4.4 the 3d geometry diff of the working tree vs git HEAD (green added / red
  removed / grey unchanged) builds the HEAD geometry in a throwaway git worktree,
  from that worktree's own copy of the design (never the working tree's, whether
  the project is a directory or a `.fcad` file). because the booleans are the slow
  part it is split: `diff-build [TARGET]` bakes the result to
  `dist/<target>.diff.FCStd` headless, `diff-open [TARGET]` opens that instantly in
  the gui, and `diff [TARGET]` chains the two. the diff is computed **per part**:
  each part type is diffed against its own previous version once, in the part-local
  frame, and the result placed at each instance, so the boolean count tracks the
  number of changed part *types*, never the instance count; an unchanged part and a
  pure placement or quantity change cost no boolean at all. a part is only ever
  diffed against itself, so no part's old material may be subtracted from another
  part's new material. `diff-build` fails loudly, with no artifact, when the diff
  cannot be computed.
- R4.5 `pdf` exports dimensioned TechDraw pages to PDF (one per part + the
  assembly) via the gui.
- R4.6 `install-git` registers fcad as the local repo's external diff driver for
  the built `.FCStd` documents, so a plain `git diff` on one shows the 3d diff
  rather than "Binary files differ", and marks `.fcad` as Python so forges render
  it as the source it is. the `.fcad` source keeps its ordinary text diff. the two
  attributes must not swap files: the `diff.fcad.command` setting and the
  `*.FCStd diff=fcad` attribute are local and untracked (git will not run a command
  a tracked file names), while the `linguist-language`/`gitlab-language` hint is
  written to the tracked `.gitattributes` (a forge only reads committed files).
  `.gitattributes` is the only working-tree file it touches, and installing twice
  is a no-op.
- R4.6a `git-diff` is that driver: git calls it with
  `path old-file old-hex old-mode new-file new-hex new-mode`, and it diffs the two
  saved revisions without building anything, so it covers whatever revisions git
  was asked to compare rather than only HEAD. a part document carries its own
  solid and diffs as geometry; an assembly document carries only links and diffs
  as placements, resolving both revisions' shapes from the part files on disk. it
  treats `/dev/null` as an absent side (wholly added or removed) and always exits
  0, because git reports any other status as a fatal error.
- R4.7 a viewer opening a headless-built document must leave the gui's own state
  agreeing with what it draws: every object it shows, layer groups included, reads
  as visible in the tree, and the document is not left marked modified.

## R5 - cli & packaging

- R5.1 a single `fcad` console script is the only entrypoint; it routes each
  command to the interpreter it needs (headless `freecadcmd`, the `freecad` gui,
  or in-process python) without the user choosing.
- R5.2 the package is self-contained: every script and macro ships inside it. it
  installs with `uv tool install` and exposes the `fcad` command.
- R5.3 `clean` removes `dist/`. `info` prints the resolved configuration.
  `install-macro` installs the rebuild macro into FreeCAD's macro directory.
- R5.5 `api-docs [DIR]` writes a FreeCAD api reference for the *installed* build
  (default `<dist>/api`): the TypeIds `addObject` accepts, each workbench's
  factory functions with their call signatures, and property tables giving each
  property's name, type, default and enum values. every name is read off a real
  object by introspection, never transcribed, so the reference cannot disagree
  with the FreeCAD that produced it. it documents the toolchain rather than a
  model, so it loads no project and runs headless. it must cover the workbenches,
  not just `App`: FreeCAD only registers a module's types once that module is
  imported, so an index naming a couple of hundred types is the requirement and a
  few dozen means the reference is silently near-empty.
- R5.6 `install-skill [--dir DIR] [--force]` installs the agent skills fcad ships
  (default under `~/.claude/skills/`). two of them: `fcad`, which is fcad's own
  contract and ships complete as prose, and `freecad-python`, which documents the
  FreeCAD api underneath it and cannot ship complete. the latter installs its
  `SKILL.md` verbatim, a curated
  subset of the FreeCAD wiki (CC0) covering the scripting semantics introspection
  cannot supply, and an `api/` reference (R5.5) generated for the FreeCAD
  installed on that machine - the last of which is why that skill cannot simply
  be committed complete. re-running it is the update path for both: the
  api-bearing one records which build its reference describes and regenerates
  when that build or its shipped `SKILL.md` has changed, the prose one whenever
  its shipped `SKILL.md` has, and each reports a no-op otherwise; `--force`
  regenerates regardless. each overwrites only what it ships - `SKILL.md`,
  `api/`, and the wiki pages by name - and never deletes, so a fuller wiki mirror
  or local notes in the same directory survive an install.
- R5.4 the freecad binaries are overridable via `--freecad`/`--freecad-gui` or the
  `FREECAD`/`FREECAD_GUI` env vars.

## R6 - FEM (finite-element analysis)

- R6.1 `fem [TARGET]` runs a headless CalculiX linear-static analysis of a built
  target and saves both the analysis document (`dist/<target>.fem.FCStd`) and a
  FreeCAD/VTK-free numpy bundle (`dist/<target>.fem.npz`) holding the boundary
  surface plus per-node von Mises stress and displacement. it runs with no display
  and without VTK. `TARGET` is `assembly` (default) or a part name; the assembly is
  the structural solids fused into one bonded body (parts flagged `embeds` excluded).
  the mesh is second-order: 1st-order tets are over-stiff in bending and understate
  deflection and stress by ~20% at usable mesh sizes, which is a wrong answer rather
  than a coarse one. a solve is reproducible: the same design solved twice gives
  the same numbers, not merely similar ones. that requires pinning both external
  tools to one thread, because gmsh's parallel 3d algorithm reseeds and a
  multithreaded CalculiX intermittently returns a materially wrong answer (four
  results spanning 6.5% over ten runs of one identical input, the low ones 6.4%
  under the closed form single-threaded matched to 0.25%). `FCAD_FEM_MESH_THREADS`
  and `FCAD_FEM_THREADS` override, at the cost of that guarantee.
- R6.2 a project may declare per-target FEM inputs (material, fixed faces, loads,
  self-weight, mesh size, modes) via an optional `Project.fem` descriptor; faces are
  selected by geometry predicate, never by fragile face indices. a force load acts
  along the direction the project declared, which reaches the solver rather than
  being replaced en route by the loaded face's normal; a pressure load acts along
  that normal by definition (`reversed` flips it). material is a named
  FreeCAD library card, an fcad built-in alias (`steel`/`aluminum`/`wood`/...), a
  name registered in the project's own `materials`, or an explicit `{E,nu,rho}`
  dict; a case with no material takes the project-wide `material` default. absent a
  descriptor, fcad falls back to a default analysis (fix the min-Z faces,
  self-weight, a steel card) so any project yields a result. a target whose mesh
  cannot be solved fails with a clear, non-zero error rather than a partial
  artifact.
- R6.3 `fem --modal`/`--modes K` additionally computes K eigenmodes (frequencies +
  mode shapes) into the same npz.
- R6.4 `fem-render [TARGET]` writes an offscreen PNG of the deformed surface colored
  by von Mises with a colorbar; `fem-animate [TARGET]` writes a deformation-sweep
  MP4+GIF plus one MP4+GIF per eigenmode. both run under plain python3 (no FreeCAD,
  no VTK). neither scales its colormap to the nodal maximum, which sits on a
  singularity and reports the mesh rather than the part: the top of the scale is
  `von_mises_p99` (or `FCAD_FEM_VMAX`), and a plot that clamped says so and still
  states the true maximum, so nothing is hidden. `fem-animate` is a camera
  (`--camera orbit|turntable|fixed`, R4.3's) crossed with a subject
  (`--subject`): the flex sweep, the shape held at peak deflection, the
  eigenmodes, or all of them. a subject that yields no clips fails loudly rather
  than writing nothing quietly.
- R6.5 a solve is bounded before it can exhaust the machine. having meshed, fcad
  estimates what a direct CalculiX solve of that node count needs and refuses -
  non-zero, with no partial artifact, and *before* CalculiX starts - one that
  does not fit, naming the node count, the estimate, the ceiling and what to
  change. the mesher runs under a wall clock bound and is killed if it overruns,
  rather than waited on forever. and every child fcad spawns runs in its own
  session under a heap ceiling it passes to its own children, so the mesher and
  solver FreeCAD launches are bounded too and one signal reaps all of them.
  `FCAD_MEM` sets the ceiling and `FCAD_FEM_MESH_TIMEOUT` the wall clock. the
  estimate is a fit and so is advisory in both directions; what this forbids is
  a run reaching the OOM killer with nothing said.

## external requirements

FreeCAD 1.1.x (with the bundled Assembly, TechDraw and FEM workbenches) on `PATH`.
`render`/`animate`/`fem-render`/`fem-animate` need the `[render]` extra (numpy,
numpy-stl, matplotlib); the animators also need `ffmpeg`. `fem` additionally needs
`gmsh` (mesher) and `ccx`/CalculiX (solver) on `PATH`.
