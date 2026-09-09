# roadmap

## later

- **FEM assembly contact.** the assembly FEM target currently fuses the structural
  solids into one rigidly-bonded body (a sane default). add bonded-tie (`*TIE`,
  `makeConstraintTie`) between touching face-pairs for per-part materials and joint
  fidelity, and optional nonlinear `makeConstraintContact` for slip/separation
  (both confirmed available).
- **publish to PyPI.** currently local/git install only.

## later

- **decimate the FEM animator's mesh.** `animate` runs `render.cluster` because
  a drilled assembly's stl is far too dense to redraw per frame; `fem_animate`
  draws the raw boundary mesh, which is fine at tens of thousands of nodes and
  will not be at hundreds of thousands. the wrinkle is that the field is
  per-node, so a FEM decimation has to carry it through the clustering rather
  than just keeping the silhouette.

## done

- **the checks that look for what is *not* there.** everything `check` asserted
  before this was positive space - solids that overlap, components that float,
  sketches that are loose. an assembly fails the other way round just as easily,
  and those failures are invisible to an overlap test by construction. `check`
  now also asserts: every `embeds` part is seated (R3.1.1); every part is
  grounded, fastened or resting on another; no part cuts away more than anything
  fills; every declared hole is actually bored; every part is one connected
  solid (R3.1.2). it *notes*, without failing, a part that loses more than 5% of
  its defining outline to joinery - what counts as the defining outline is the
  project's call.

  each was written from a real failure, and each of those rendered correctly,
  exported correctly and passed every assertion fcad had. the seating check
  found two on the day it was written, and then found a third in the *older*
  planter, which it was not designed for: 51 of that model's 180 screws are
  driven through undrilled post material, 465 mm^3 each, never into
  `corner_post_001`. that is its own backlog note - "per-corner mirror-image
  hole patterns blocked reusable part files" - turned from a sentence into a
  number and a list of instances. **that project's `check` now fails until it is
  fixed, which is the correct outcome and worth knowing before upgrading.**

  the judgement calls are deliberate and documented where they live: the
  support check only applies to a model that flags `grounded` somewhere, because
  "held up by something" is a claim about a physical stack and plenty of models
  are not one; a void is budgeted as a fraction of the part's own blank, because
  real clearances scale with the part and a mistake does not; the seating nudge
  is 1.5 mm, larger than a typical pilot clearance, or a correctly seated
  fastener reads as loose. `tests/test_checks.py` pins all four against fixtures
  rigged to fail and rigged to pass.

- **`CONSTRAINTS` and `fcad optimize`.** a project declares predicates over its
  own computed values saying when the model still means what it says; `check`
  asserts them (R3.1.3) and `optimize` refuses any candidate that breaks one
  (R3b). the two shipped together because the second is unsafe without the
  first: a sweep left to itself finds the corner where the model degrades and
  reports it as a saving, since from the outside there genuinely are fewer
  boards. the hand-rolled version that motivated this "saved" three boards by
  quietly clamping a planter's soil bed from 400 mm to 350, and said nothing.
  a project declaring no constraints is warned, loudly, in the output.

  on the planter it found an envelope 10 mm longer and 50 mm wider taking 16
  boards instead of 18 while carrying 6% more bed. it also made the objective
  choice concrete: ranked by boards it finds 15-board plans, ranked by purchased
  length it prefers a 17-board plan that buys 3.6 m less timber. board count is
  a proxy for cost and not a good one, so length is the default.

  it runs on the headless path despite touching nothing in the kernel, because
  loading a project means importing its module and a project module imports
  `Part`. `fcad.optimize` itself is pure stdlib and tested as such.

- **`fcad test` and `fcad.testing`.** `freecadcmd` exits 0 on an uncaught
  exception and discards buffered stdout when it exits non-zero, so a test
  judged on either channel reports a crash as a pass. *both* projects using fcad
  had independently invented the same workaround - write a verdict to
  `$RESULT_FILE`, grep it - and then hand-written the same `load` / `solids` /
  `blanks` / `extent` / `overlap` / `near` helpers on top. when two projects
  converge on the same hack it belongs in the tool (R3.3). a test that writes no
  verdict at all is a failure and says so, rather than passing quietly.

- **fem results say what made them, and warn when they are aimed wrong.** a case's
  `gravity` is read in the part's own stock frame, so fcad now checks it against
  the part's real placement and complains when it no longer points down
  (R6.2.1): a bearer whose frame had been rotated for an unrelated reason solved
  happily at 13.8 MPa against a true 3.3, loaded across its weak axis with its
  own weight pulling sideways. and every result bundle records a hash of the
  project source (R6.2.2), because a solve takes minutes, results outlive the
  geometry that made them, and a stale von Mises number reads exactly like a
  fresh one - which both projects had hand-rolled the same makefile target to
  guard against.

- **the cut list totals, and can be priced.** the summary ends with boards,
  purchased length, waste and optionally money across every profile (R2.5.1); a
  `STOCK` entry may be `(length, price)`. per-profile lines answer how to cut one
  profile, and nobody's shopping list is one profile. purchased length leads
  because waste percentage is actively misleading as a headline: the planter's
  chosen envelope buys *less* total timber at a *higher* waste percentage.


- **`embeds` seating check.** `find_overlaps` skips `embeds` parts *by design* -
  the right call, and a hole: the parts fcad stops checking are exactly the ones
  whose whole job is to sit inside a hole in something else. `find_unseated`
  closes it, and `check` now asserts it (R3.1, R3.1.1). two findings, reported
  apart because one is exact and one is a heuristic: **`interferes`**, a part
  ploughing through a structural solid, which is the failure worth having the
  check for; and **`unseated`**, a part nudged 1.5 mm along each axis that meets
  material from fewer than three of six directions - a through bore captures
  four, a blind hole five, a part lying on a face one, mid-air none.

  it earned its place twice over on the day it was written. it reproduces both
  planter-nihon bugs that motivated it (pegs turned 90 degrees to their own
  bores; a wedge mortise closed on four sides, leaving solid tenon above and
  below it so no wedge could ever be inserted - both of which rendered and
  exported perfectly and passed every other assertion). and run against the
  *older* planter, which it was not designed for, it immediately found a real
  defect that project had recorded qualitatively and never measured: 51 of its
  180 screws are driven through undrilled post material, 465 mm^3 each, and
  never into `corner_post_001`. that is its backlog item "per-corner mirror-image
  hole patterns blocked reusable part files" - the parts are drilled from
  `placements[0]` only, so the three mirrored corners get the first corner's
  holes. a qualitative note became a number and a list of instances.

  the nudge is deliberately larger than a typical pilot clearance (1.5 mm
  against the ~0.5 mm radial gap a 1 mm-oversize clearance hole leaves) or a
  correctly seated fastener reads as loose. `tests/test_seating.py` pins all
  three states - seated, crossways, floating - plus silence on a model with no
  embedded parts at all.

- **the assembly animation climbs instead of ringing outward.** the walk was
  breadth-first, which guarantees a part arrives attached to something already
  placed but says nothing about *which* attached part comes next - so it arrived
  in rings around the anchor. on the planter that read as three separate climbs
  from ground to cap (one per corner post's ring, the posts sharing few
  neighbours) with the floor deck landing *after* the cap, since the deck is
  three hops out over ledger and runner while the top course is one hop off a
  post. the frontier is a heap on height now rather than a fifo: anchors first,
  then repeatedly the lowest part touching what is placed. same graph, same
  guarantee, and the planter now goes posts, first course, ledgers, second
  course, runners, deck, remaining courses, cap, screws - which is the order you
  would build it in. `tests/test_assemble.py` pins it on a two-post tower whose
  rails reach only their own post, the small version of exactly that failure.
- **the animation stopped eating the machine.** three compounding faults, found
  when `FCAD_AT_ONCE=1 fcad animate --speed 0.7` on the planter took the box out.
  (1) **the gif writer buffered every frame.** matplotlib's `PillowWriter` holds
  the whole clip in memory until the end, so 3854 frames wanted **8 GB** for a
  model whose geometry is 0.7 MB. both formats now stream through ffmpeg, with
  the gif palette generated in its own pass rather than the usual single-pass
  `split/palettegen/paletteuse`, which has to hold one branch while the other is
  computed - the same problem wearing a different hat. (2) **fasteners each took
  an arrival slot.** the planter is 224 instances of which **180 are screws**, so
  four fifths of a 269-second film was screws appearing one at a time. they share
  one slot now: the structure is what a viewer reads, the screws are punctuation.
  that alone took the clip from 3854 frames to 786. (3) **nothing bounded the
  frame count**, which the derived duration made easy to overshoot; `plan()`
  announces the clip and refuses past `FCAD_MAX_FRAMES` naming the levers. also
  fixed the per-frame churn: poses are written into one preallocated buffer
  rather than rebuilding and concatenating the whole model every frame. the
  user's original command now runs in 254s at **0.29 GB**.
- **fcad stopped leaking scratch into tmpfs.** FreeCAD gives the mesher and the
  solver a fresh `mkdtemp` each and removes neither, so every `fcad fem` left two
  directories behind - and `/tmp` is tmpfs, so that is not litter on a disk but
  resident memory held until reboot, ~750 KB for a toy beam and hundreds of MB
  for a real mesh, once per solve, forever. fcad owns one directory per solve and
  removes it; `FCAD_KEEP_WORK` keeps it and says where, since the `.inp` and
  `.frd` are what you want when a solve misbehaves. the mesher's half is the easy
  one to miss: `GmshTools.prepare()` calls `get_tmp_file_paths()` with no
  argument, which ignores the mesh object's `WorkingDirectory` entirely, so the
  call has to be made by hand.
- **`install-skill` installs fcad's own skill too.** `SKILL.md` moved into
  `resources/skills/fcad/` so it ships in the wheel at all, and there are now two
  skills: `fcad`, the project contract and cli, which ships complete as prose,
  and `freecad-python`, which carries the build-specific api reference. each
  reports its own freshness, so a FreeCAD upgrade refreshes the reference while
  the prose one correctly stays a no-op.
- **`animate`: the model building itself.** each instance flies in from an
  exploded position to where it belongs, one at a time. this is now what a bare
  `fcad animate` does, because a model assembling itself says more in ten seconds
  than a turntable does; `--subject static` still orbits the finished thing, and
  a single part, having nothing to assemble, still spins. naming a part *and*
  asking to assemble is an error rather than a silent whole-model clip. it runs
  off the part stls plus the placements a build records, never the assembly stl -
  that one is a single welded lump with no part boundaries left in it, while the
  parts and their placements *are* the model with its seams still in.

  the ordering is the part worth getting right, and it needed no new affordance:
  fcad already had all three inputs. `grounded` names the anchor. `embeds`
  already means "fastener" - it is the flag that excludes screws from the
  interference check. and the contact graph is arithmetic over geometry the
  renderer already holds. so the default order is a breadth-first walk outward
  from the anchor with the fasteners held to the end, and every part arrives
  attached to something already there. a plain z-sort only looks equivalent: on
  the shipped fastenplates example it drives the screw home *between* the two
  plates it holds, which `tests/test_assemble.py` pins as the before/after.

  what is deliberately *not* the source is the joint graph. `build_jointed_doc`
  fixes every non-grounded part to a single datum, so it is a star with every
  part one hop from the anchor - it exists to make the assembly fully constrained
  and says nothing about what touches what. the two flags travel out in a new
  `<name>-parts.json` rather than as extra keys in the placements file, whose
  shape is `{part: [placement]}` and whose readers would be tripped by a
  top-level key that is not a part name.

  timing is a **multiplier over a derived length**, not a length. a clip already
  has a duration its content implies: `FCAD_AT_ONCE` caps how many parts are in
  the air at once (1 being strictly sequential) and that, with the part count,
  sets how long the film has to be - twenty parts arriving singly is 25s where
  three is 4.6s. a fixed total would have squeezed both into the same seconds and
  made one of them a blur, which is what the first cut did. so `--speed` scales
  whatever the model implies, `--fps` sets the rate, the frame count is their
  product rather than a third setting, and `--seconds` remains for filling an
  exact slot. concurrency is likewise a count rather than a flight duration,
  because a duration does not survive the part count: one that looks right for
  three parts has twenty converging twenty-deep.
- **the animators now agree: a camera crossed with a subject.** `animate` moved
  the camera and not the model; `fem-animate` moved the model and not the camera.
  each had exactly what the other lacked, which was accidental rather than
  designed. both now take `--camera orbit|fixed` through one shared
  `render.aim(ax, frac)`, and `fem-animate` adds `--subject all|flex|static|
  modes`. the useful new combination is `--subject static --camera orbit`: the
  deformed shape held at peak and viewed from everywhere, so every face is seen
  at the *same* deflection and can actually be compared - which one flex cycle
  per turn makes impossible, so a flexing orbit runs four cycles per turn
  (`FCAD_FEM_CYCLES`) instead. holding also lets the view cube be squared to the
  deformed geometry, so the flexed shape cannot walk out of frame. the collision
  this forced out into the open: `FCAD_ELEV` meant a fixed elevation to the still
  renderers and the centre of a sweep to `animate`, with different defaults (22
  and 20). one meaning now, in one place, with `FCAD_TILT=0` collapsing an orbit
  to a fixed camera - which is what makes `fixed` a special case rather than a
  second code path. the vocabulary lives in the import-free `fcad/render/
  __init__.py`, because the cli must name these in `--help` without dragging
  matplotlib into every `fcad` startup.

- **a FEM picture scaled to its singularity showed nothing.** `fem-render` and
  `fem-animate` normalized the colormap to the nodal maximum, which is the one
  value in the bundle that reports the mesh rather than the part. on the shipped
  cantilever, refining `mesh_size` from 6 to 2 moves the peak from 74.9 to
  **12848 MPa** while p95 stays near 70 and the deflection stays at 1.05 mm - so
  the plot put the entire beam in the bottom 0.6% of the scale and rendered as a
  uniform slab. it is the same defect `von_mises_p95` already fixed for the
  reported number, still present in the image, and worst on an assembly, which is
  where a picture is most wanted. the scale now clamps to `von_mises_p99` (it was
  already in the npz), `FCAD_FEM_VMAX` overrides, and the plot states the clamp
  *and* the true maximum rather than hiding it. `fem-animate` also honours
  `FCAD_ELEV`/`FCAD_AZIM` now, which it silently ignored: a loaded structure
  deflects where it is supported and the supports are underneath, so the one view
  that shows a load path was reachable for a still and not for a clip.
- **the same design now meshes the same way twice.** gmsh's parallel 3d
  algorithm is not reproducible: four identical runs of one 358k-node mesh
  returned four different node counts, and pinning `Mesh.RandomSeed` does not
  help because the variation is thread interleaving, not the seed. FreeCAD sets
  `General.NumThreads` to the cpu count, so every fcad solve inherited it. that
  is not cosmetic - it is what lets an *unchanged* model re-solve to a different
  answer, and the peak von Mises has swung **350x** between two seeds of one
  plain prismatic board (6.2 -> 2191.1 MPa) while its deflection held to four
  figures. it also made fcad's own suite quietly flaky: the cantilever theory
  check asserts 5% and the reseed spread at its mesh size reached 4.8%, with one
  run drawing 19%. fcad now pins the mesher to one thread for the duration of a
  mesh and puts the preference back afterwards, since a gui session meshing
  single-threaded because a build ran is not a trade to make on someone's
  behalf. it costs about 15% of the mesh step (6.0s -> 6.9s at 358k nodes),
  which is the cheap phase next to the solve; `FCAD_FEM_MESH_THREADS` takes it
  back for anyone who would rather have the wall clock than the reproducibility.
  that was half of it. the other half was **the solver, and it was not a
  reproducibility problem but a correctness one.** an excursion survived the mesh
  fix - about one run in eight, up to 15% - on a CalculiX input proven
  byte-identical run to run. ten runs of that one fixed `.inp` at 16 threads
  returned **four different tip deflections** spanning 6.5%, the low ones 6.4%
  under a closed form that the single-threaded run matched to 0.25%; ten
  single-threaded runs returned one answer. so a multithreaded CalculiX solve
  here is not merely unrepeatable, it is intermittently *wrong*, and wrong in the
  direction that flatters a part. it had been failing this repo's own beam-theory
  check intermittently all along.

  getting one thread meant running ccx by hand: FreeCAD's `start_ccx` forces
  `OMP_NUM_THREADS` to the cpu count, and its `AnalysisNumCPUs` preference only
  ever raises the count - setting it to 1 selects the cpu-count branch - so there
  is no supported way down. that costs 1.2-1.6x wall clock (26.7s -> 41.9s at
  100k nodes) and `FCAD_FEM_THREADS` takes it back, though it is hard to see why
  anyone would want to. with both halves pinned a result is reproducible end to
  end, and `tests/test_fem.py` now asserts equality on deflection and peak stress
  rather than a tolerance.
- **a solve can no longer take the machine with it.** `fcad fem` was the one
  command that failed by exhaustion: gmsh ran 14 minutes at 7.6 GB on a fastened
  assembly without finishing, and CalculiX was OOM-killed (exit -9) three times
  in one session at 282k-551k nodes, driving a 64 GB desktop under 1 GB free
  first - one of those cases having solved twice earlier the same session, which
  is the tell that the limit is the machine's free memory and not the model.
  three bounds now, in decreasing order of how much they can explain. **the
  preflight** estimates a direct solve from the node count and refuses one that
  will not fit before ccx starts, naming the count, the estimate, the ceiling and
  which knob to turn: 1.1 s and a sentence, against four minutes and an exit code
  of -9. the estimate is measured rather than guessed - peak ccx RSS over six
  mesh sizes on a 2nd-order steel cantilever, 26 MB at 1671 nodes to 2.25 GB at
  100855, which fits `500 * nodes^(4/3)` to +-2% above 20k nodes and independently
  predicts 8.6 GB for the 282k-node solve that died on a machine with ~10 GB
  free. **a wall clock** on the mesher, whose cost nothing knows before it runs
  (`FCAD_FEM_MESH_TIMEOUT`, default 900 s), which needed gmsh to be driven
  through `GmshTools`' granular seam rather than `create_mesh()` - that one waits
  `waitForFinished(-1)`, forever, and reports a failed mesh only by leaving it
  empty, so the failure used to resurface minutes later as a CalculiX complaint
  about a model that was never meshed. **an inherited rlimit** as the crash
  barrier, because the processes that get big are grandchildren FreeCAD spawns:
  it is the one mechanism that reaches them. the sharp edge there is that
  `RLIMIT_DATA` counts untouched mappings and OpenBLAS reserves its buffer pool
  up front (2.29 GiB of VmData against 0.03 GiB resident at 1671 nodes, constant
  with problem size and with thread count), so a ceiling set to the budget alone
  stops FreeCAD from starting rather than stopping a runaway - the first cut did
  exactly that, turning `FCAD_MEM=1M` into exit 127. children also get their own
  session now, so one signal reaps the mesher and solver with them; killing
  freecadcmd by pid used to leave a gmsh behind still holding 7.6 GB. frozen as
  R6.5.
- **partial supports, so a beam is not clamped at both ends.** a case may now
  declare `supports=[dict(faces=[...], fix="yz"), ...]` beside `fixed`,
  restraining only the named translation axes via `makeConstraintDisplacement`.
  a fully fixed face is a clamp -- every node on it is pinned, so it cannot
  rotate -- and clamping both ends of a member reads 5x stiff against a beam
  that merely rests on its bearings, understating its peak moment by a third.
  face selectors still cannot isolate an edge, so a true simple support is out
  of reach; clamp one end and roller the other and a uniformly loaded beam at
  least carries the right `wL^2/8`, recovering 2.08x of that 5x and leaving the
  model 2.41x stiff. those three are exact and carry no caveat about section or
  span -- a UDL deflects `wL^4/384EI` clamped both ends, `wL^4/185EI` clamped and
  rollered, `5wL^4/384EI` on bearings, and 384/185 x 925/384 = 5 -- so a beam
  that does not reproduce them has a bug rather than a shape. `tests/test_fem.py`
  measures 0.0156 mm against 0.0320, which is 2.05 against a predicted 2.076, and
  a `fix` silently ignored would read 1.00. (the property names came straight out of
  `api-docs`, which is the reason it exists: `xFree`/`yFree`/`zFree` appear
  nowhere in the wiki.)
- **`undrilled`, which is what makes a whole-assembly solve possible.** a case
  may build its parts from their 2d profiles instead of their drilled solids.
  gmsh sizes elements from curvature (`MeshSizeFromCurvature`, 12 per turn by
  default), so every 4 mm pilot hole demands ~1 mm elements however coarse the
  ceiling: the planter's fused box has 649 drilled faces and spent 14 minutes
  and 7.6 GB without finishing a mesh. undrilled it meshes in seconds (173k
  nodes) and solves in 45 s. `mesh_curvature`/`mesh_min` expose the underlying
  knobs, with the caveat that a hole below the resulting element size cannot be
  meshed at all and gmsh returns nothing rather than a coarser hole - which is
  why turning curvature *down* is not the fix it looks like.
- **`fem all`, and percentiles beside the peak.** `fcad fem all` works through
  every case a project declares rather than one target per invocation. the npz
  gained `von_mises_p95`/`p99`: the nodal maximum always lands on a singularity
  -- a clamped face, the sharp internal corner of a notch or a drilled hole --
  where linear elasticity has no finite answer, so it reports the mesh rather
  than the part. a notched planter floor board reads 205.8 MPa at its worst node
  and 5.18 at p95; a mesh reseed swung one runner's maximum from 1015 to 10.9
  between two runs while its p95 moved only from 7.7 to 4.0. the sharpest case
  so far is an *unchanged* planter board re-solved on a new seed: max 6.2 ->
  2191.1 MPa, a factor of 350, against p95 3.93 -> 4.80 and a deflection stable
  to four figures (1.940 mm both times) - and that board is plain and prismatic,
  so the singularity is the clamped face and nothing else. p95 is a floor on
  the field stress, not a peak: on a clamped model the moment maximum sits at
  the constrained end, which is exactly what the percentile throws away.
- **`api-docs`: a build-accurate FreeCAD api reference.** `fcad api-docs [DIR]`
  writes `index/typeids/factories/fem/properties.md` for the *installed* build by
  instantiating real objects and reading `PropertiesList` /
  `getTypeIdOfProperty` / `getEnumerationsOfProperty` off them, so no name in it
  is transcribed and it cannot drift from the FreeCAD that made it. on 1.1.1:
  337 TypeIds over 14 modules, 77 `ObjectsFem` makers, 26 property tables.
  it exists because the two alternatives both fail quietly - the wiki documents
  the *gui* and often never names the property behind a checkbox (`xFree` /
  `yFree` / `zFree` on a displacement constraint appear nowhere in 2600 pages of
  it), and recalled api knowledge goes stale as names move between versions
  (`Support` -> `AttachmentSupport`). it documents the toolchain, not a model, so
  it loads no project. the sharp edge is `supportedTypes()` reporting only
  *loaded* modules: without preloading the workbenches the index comes out as
  `App` plus `Image`, 38 types, with the same exit status and file count as a
  good run - so `tests/test_api_docs.py` asserts the coverage (>= 200 types, six
  named workbench TypeIds) and not just that files appeared. factories that need
  a parent object (elmer equations, mesh regions) are documented by call
  signature rather than reported as errors. frozen as R5.5.
- **`install-skill`: ship the reference as an agent skill.** `fcad install-skill`
  installs `resources/skills/freecad-python/SKILL.md` into
  `~/.claude/skills/freecad-python` and generates `api/` beside it for the local
  FreeCAD. the skill is shipped by fcad rather than committed complete anywhere
  because half of it - the api reference - only exists once it meets the machine
  it runs on. re-running is the update path: the reference is pinned to a build
  (`api/BUILD`, from a 50ms `freecadcmd --version`), so an unchanged build is a
  130ms no-op that says so, a changed build or an updated shipped `SKILL.md`
  regenerates, and `--force` always does. it owns `SKILL.md` and `api/` and
  leaves the rest of the directory alone. it also ships **52 curated wiki pages**
  (~650 KB, CC0), which is the other half of the answer: `api/` states that a
  property exists and what it accepts, the wiki explains what it *means*, and
  nothing generated can replace the FeaturePython lifecycle, attachment
  semantics, topological traversal or constraint construction. the subset is the
  point - the full export is 2630 pages and 22 MB, of which 599 are sub-600-byte
  stubs and 926 are gui pages with no python at all, so 2% of the files carry
  essentially all the scripting value. measured against eight real api questions
  from fcad's own development the full wiki missed five outright (`ElementOrder`,
  `SecondOrderLinear`, `DirectionVector`, `CharacteristicLengthMax`, and `xFree`
  outside a 0.19 release note), which is what settled the division of labour.
  frozen as R5.6; covered by `tests/test_api_docs.py`, including that an install
  composes with a fuller mirror rather than replacing it.

- **FEM: two silently wrong answers.** both produced plausible numbers that
  CalculiX reported success on, which is why no self-consistency assertion ever
  saw them. (1) a force load's declared `direction` never reached the solver: a
  `ConstraintForce` re-derives `DirectionVector` from its referenced face's
  outward normal every time it executes, and `analysis.addObject()` alone does
  that, so a beam asked for 500 N downward was solved as 500 N of axial tension -
  wrong by ~600x, silently. it is now re-asserted immediately before each solve
  and verified into the emitted `*CLOAD` block. (2) the mesh was 1st order
  (FreeCAD's gmsh default) and 4-node tets shear-lock in bending. the scale of
  that was the surprise: a stocky cantilever read 21% under its closed form, but
  planter's floor_slat - thin, L/h = 25 - read **0.176 mm against a fixed-fixed
  theory of 0.905, low by 5.1x**, and its modal frequencies correspondingly high.
  2nd-order tets bring the same slat to 0.896 mm, within **1.0%** of theory. they
  have to be *straight-edged* (`SecondOrderLinear`): gmsh otherwise curves midside
  nodes onto the geometry and inverts elements around small features, which ccx
  rejects outright ("nonpositive jacobian") - planter's drainage holes did exactly
  that. R6.1/R6.2 extended; covered by `tests/test_fem.py`, which now checks both
  load paths against closed-form beam theory rather than against themselves, the
  only kind of assertion that could have caught either bug.
  **projects must re-tune `mesh_size` upward**: it is ~8x the nodes at the same
  value, and a mesh_size chosen to fight the old stiff elements can now exhaust
  the solver (planter's floor_slat at its declared 8.0 dies mid-step at 1.65M dof;
  16.0 solves in ~2 min and is the accurate number quoted above). coarser and
  quadratic beats finer and linear on both accuracy and cost.
- **a failed command says why.** `freecadcmd` loses a failure two ways, and both
  bit: it discards python's buffered stdout when a command exits non-zero, so a
  redirected or piped `fcad check` failure printed *nothing at all* (on a terminal
  it printed fine, which is what hid it - CI and every logged run got the bare
  status), and it never prints the message a `SystemExit` carries, so fcad's own
  FEM diagnostics were swallowed whole. `_entry` now flushes on the way out and
  prints those messages itself. the FEM one also stopped being actively
  misleading: it said "try a smaller mesh_size" when the dominant failure with
  quadratic elements is a mesh too *large* to solve, so it now names both
  directions and reports the node count and mesh size it actually used. R3.1
  extended; covered by `tests/test_errors.py`, which drives child processes with
  stdout on a pipe, since a pty cannot reproduce it.
- **built files open view-ready and framed.** opening an fcad-built assembly showed
  nothing at all: visibility is gui state, held in the zip's `GuiDocument.xml`,
  freecadcmd has no `ViewObject` to write one, and without it every object restores
  switched off - on planter the only visible objects were the 190 joints, which
  draw nothing. `fcad view` papered over it by forcing visibility at open, so a
  plain double-click (or any other tool) still got an empty window. the build now
  writes that file itself (`util.export_gui_state`): freecad restores a *partial*
  GuiDocument happily, defaulting whatever is absent, so emitting one `Visibility`
  bool per object plus a camera is enough - no need to synthesize the 15-property
  ViewProvider blocks the gui writes, which would bake gui internals into the
  builder. it is appended to the saved zip with plain `zipfile`, so R2.4 (no
  display in a build) still holds. the camera makes it open *framed* too: its
  relationship to the model was measured off the gui's own `viewIsometric` +
  `ViewFit` and is exact - ortho height is the visible bbox diagonal, focal
  distance half of it, eye at centre + `diagonal/(2*sqrt(3))` along `(1,-1,1)` -
  and the baked result matches a real `ViewFit` to 0.000% on every field, on both a
  single part and planter's 190-instance assembly. frozen as R2.5a; covered by
  `tests/test_contract.py` (links/solids visible, sketches and origin hidden, and
  the camera arithmetic checked against an independently unioned bounding box).
- **`git diff` opens the 3d diff.** `fcad install-git` registers fcad as the local
  repo's external diff driver for the built `.FCStd` documents
  (`diff.fcad.command` in `.git/config`, `*.FCStd diff=fcad` in
  `.git/info/attributes`), so `git diff`, `git diff HEAD~3`, `git diff a-branch` on
  a committed artifact show the green/red/grey model instead of "Binary files
  differ". the binary is the file worth replacing: the `.fcad` source is python and
  diffs fine as text, and is deliberately left alone - `install-git` instead writes
  a *tracked* `.gitattributes` marking it Python
  (`linguist-language`/`gitlab-language`) so github and gitlab render a design as
  source and count it in the language stats. which attribute goes in which file is
  forced, not a preference: git will not run a command a tracked file names, so the
  driver must be local config, while a forge only ever reads committed files, so
  the language hint must be tracked. `.gitattributes` is the only working-tree file
  touched. `fcad git-diff` is the driver git calls with the 7-parameter
  external-diff contract; it shares the compute step with `fcad diff` but builds
  nothing at all, since git hands it two already-built documents - which is both
  simpler and more general, because it covers whatever revision pair git was asked
  about rather than only HEAD (2.6s for a planter part, 2.1s for its assembly).
  git diffs one file at a time and the two kinds of document record different
  things, which falls out as useful granularity: a part document holds its own
  solid so it diffs as geometry, while an assembly holds no geometry at all - only
  links - so it diffs as placements, which is exactly what that file records (a
  part's shape changing is a change to the part file, diffed separately). the
  links are stored *relative* to the document, so each revision is staged with the
  real file's sibling directories mirrored beside it as symlinks, or it would
  silently resolve to nothing; only directories are mirrored, so a staged copy can
  never be written through a link onto the real document. `/dev/null` (git's
  stand-in for an absent side) contributes nothing, which the per-part diff already
  reads as wholly added or removed with no special case. it always exits 0, because
  git reports any other status as a fatal error. covered by `tests/test_git.py`,
  which asserts the wiring, that installing twice changes nothing, that
  `git check-attr` binds `*.FCStd` to the driver while `.fcad` keeps its text diff,
  that a real `git diff` invokes it with 7 parameters, and the baked volumes for a
  resized part, a changed instance count, and a created/deleted document.
- **the diff's layers open with their visibility badge correct.** opening a diff
  showed all three layers drawn but their tree rows switched off, so toggling one
  did nothing until it had been clicked twice. a freecadcmd-built document opens
  with *every* object hidden, container objects included, and `view_diff` was
  showing only the `Part::Feature`s - leaving each layer's own group off while its
  contents were on. it now shows every object that has a `ViewObject`. verified by
  reading the tree items back under `xvfb-run freecad`: on a raw open all six
  objects report `Visibility=False` and the tree greys them; after, all six are
  `True`, the rows draw normally, and the document is still not marked modified.
  frozen as R4.7 and written up in CONTRIBUTING's gui-only notes.
- **per-part 3d diff.** `fcad diff` used to diff the assembly as one pile of
  solids: signature-matched instances were skipped, but everything left over went
  into two compounds cut against each other in three whole-assembly booleans. on
  ../planter's real HEAD diff (190 vs 184 solids, 80 vs 74 residual) the first of
  those three did not finish in 25 minutes. now each *part type* is diffed against
  its own previous version once, in the part-local frame, and the green/red/grey
  results are placed at every instance - a transform, not a boolean. so the kernel
  work follows the number of changed part types (11 types on planter, 3 changed)
  instead of the instance count, and instances that merely appeared, vanished or
  moved cost nothing: `fcad diff-build` on planter is ~11s end to end, most of it
  rebuilding the HEAD geometry, and material is conserved to 0.5 mm^3 in 228
  million. pairing by part also fixed a correctness bug the old cut had by
  construction: it subtracted every old solid from every new one regardless of
  provenance, so a screw that moved carved a spurious void out of a board that
  grew. this changes what the layers mean - they now answer "what changed about
  each part" rather than "what matter is here now", so material replaced by a
  *different* part reads as red and green in the same place. instances pair by
  placement (identical first, then nearest translation, then surplus/shortfall),
  which needs both revisions' placements; the diff cannot recompute the old ones,
  so every assembly build now records `dist/<name>-placements.json` (R2.1) and the
  diff reads only that plus `parts/*.step` - it no longer loads the assembly STEP
  at all. two pre-existing bugs surfaced and were fixed on the way: the nested
  HEAD build inherited `FCAD_ENTRY` pointing into the working tree, so `fcad diff`
  on a single-file `.fcad` project silently diffed the working tree against itself
  (and wrote the "HEAD" build over the working `dist/`); and `freecadcmd` exits 0
  even when the script raised, so a failed diff reported success, which
  `diff.build` now catches by checking for the artifact. covered by
  `tests/test_diff.py`: the fan-out, the cross-talk case, instance surplus, whole
  parts added and removed, a single-part target, and a time budget on the
  configuration that used to melt down (17s -> 0.1s).
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
  632/1722/1778 Hz (unchanged by that refactor, which was the point - but those
  figures were themselves wrong, see the 1st-order tet entry above). covered by
  `tests/test_contract.py`: a synthetic single-file
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
