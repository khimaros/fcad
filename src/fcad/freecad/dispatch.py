"""build/validate targets, driven by freecad/_entry inside freecadcmd.

the project to build is named by FCAD_PROJECT (a directory holding a
`project.py` that exposes PROJECT). targets map to a set of output formats
produced for the parts and/or the assembly. with no target (or 'all') it produces
everything under the project's dist/.
"""

import os
import sys

import FreeCAD as App

from fcad.freecad import build_parts, build_assembly

# "sketch" exports the defining 2d sketch (svg + dxf); only parts have one.
ALL = {"fcstd", "step", "stl", "svg", "dxf", "drawing", "sketch"}

# the bom and the cut list derived from it are assembly-stage only: a part file
# knows its own length, but only the assembly knows how many of it there are.
DOCS = {"bom", "cutlist"}

# target -> (formats for parts, formats for assembly). None = skip that stage.
TARGETS = {
    "all":      (ALL, ALL | DOCS),
    "parts":    (ALL, None),
    "assembly": (None, ALL | DOCS),
    "step":     ({"step"}, {"step"}),
    "stl":      ({"stl"}, {"stl"}),
    "svg":      ({"svg"}, {"svg"}),
    "dxf":      ({"dxf"}, {"dxf"}),
    "drawings": ({"drawing"}, {"drawing"}),
    "sketches": ({"sketch"}, None),
    "bom":      (None, {"bom"}),
    "cutlist":  (None, {"cutlist"}),
}


def dirs(project):
    dist = project.dist
    d = {"dist": dist,
         "parts": os.path.join(dist, "parts"),
         "drawings": os.path.join(dist, "drawings"),
         "sketches": os.path.join(dist, "sketches")}
    for path in d.values():
        os.makedirs(path, exist_ok=True)
    return d


def run(project, target, values=None):
    # don't litter dist/ with .FCBak backups on resave.
    App.ParamGet("User parameter:BaseApp/Preferences/Document").SetInt(
        "CountBackupFiles", 0)
    part_fmts, asm_fmts = TARGETS[target]
    d = dirs(project)
    values = project.defaults() if values is None else values
    if part_fmts:
        build_parts.build(project, values, d, part_fmts)
    if asm_fmts:
        build_assembly.build(project, values, d, asm_fmts)
    print("build ok: target=%s -> %s" % (target, d["dist"]))


def build_all(project, values):
    run(project, "all", values)


def optimize(project, values=None):
    """sweep parameters for a cheaper cut list and print the ranking.

    the sweep itself is pure stdlib (`fcad.optimize`), but it has to run in here
    because loading a project means importing its module, and a project module
    imports `Part` -- so the kernel has to be up even though none of this
    touches it."""
    from fcad import optimize as opt
    names = [n for n in os.environ.get("FCAD_OPT_PARAMS", "").split(",") if n]
    if not names:
        raise SystemExit("fcad optimize: name at least one parameter to vary")

    def num(key, default=None, cast=float):
        raw = os.environ.get(key, "")
        return cast(raw) if raw else default

    try:
        results, skipped, warning = opt.sweep(
            project, names,
            num("FCAD_OPT_SPAN", 60.0), num("FCAD_OPT_STEP", 10.0),
            values=values, kerf=num("FCAD_OPT_KERF"),
            trim=num("FCAD_OPT_TRIM"),
            objective=os.environ.get("FCAD_OPT_OBJECTIVE") or None)
    except (KeyError, TypeError) as exc:
        raise SystemExit("fcad optimize: %s" % exc)
    for line in opt.report(results, skipped, warning,
                           limit=num("FCAD_OPT_LIMIT", 10, int)):
        print(line)


def check(project, values=None):
    """validate the assembly: no interpenetrating solids, no unconstrained parts."""
    values = project.defaults() if values is None else values
    failed = False

    # the project's own predicates first: they are cheap, and a model that has
    # stopped meaning what it says makes every geometric result below moot.
    broken = project.violations(values)
    if broken:
        failed = True
        print("check failed: %d constraint(s) violated:" % len(broken))
        for why in broken:
            print("  " + why)
    elif getattr(project, "constraints", None):
        print("check ok: every declared constraint holds")

    # every geometric assertion walks the same placed solids, so build them once.
    model = build_assembly.Model(project, values)

    hits = build_assembly.find_overlaps(project, values, model=model)
    if hits:
        failed = True
        print("check failed: %d overlapping part pair(s):" % len(hits))
        for a, b, vol in hits:
            print("  %s <-> %s : %.1f mm^3" % (a, b, vol))
    else:
        print("check ok: no overlapping parts")

    # the three below all look for the *absence* of geometry, which is what the
    # overlap test above is structurally unable to see: a fastener that fits no
    # hole, a part with nothing under it, a cut larger than the joint it
    # relieves. each of those renders correctly and exports correctly.

    # `embeds` is what excluded those parts from the overlap check, so it owes
    # the model a check of its own.
    unseated = build_assembly.find_unseated(project, values, model=model)
    if unseated:
        failed = True
        print("check failed: %d embedded part(s) not seated:" % len(unseated))
        for name, reason, detail in unseated:
            print("  %s: %s (%s)" % (name, reason, detail))
    else:
        print("check ok: every embedded part is seated in its hole")

    loose_parts = build_assembly.find_unsupported(project, values, model=model)
    if loose_parts:
        failed = True
        print("check failed: %d part(s) with nothing holding them up:"
              % len(loose_parts))
        for name in loose_parts:
            print("  " + name)
    else:
        print("check ok: every part is grounded, fastened or resting on another")

    voids = build_assembly.find_voids(project, values, model=model)
    if voids:
        failed = True
        print("check failed: %d part(s) cut away more than anything fills:"
              % len(voids))
        for name, vol, frac in voids:
            print("  %s: %.0f mm^3 unfilled (%.1f%% of the blank)"
                  % (name, vol, 100.0 * frac))
    else:
        print("check ok: no part carries an unfilled void")

    undrilled = build_assembly.find_undrilled(project, values, model=model)
    if undrilled:
        failed = True
        print("check failed: %d part(s) declare holes that are not bored:"
              % len(undrilled))
        for name, missing, total in undrilled:
            print("  %s: %d of %d holes are on the drawing but not in the solid"
                  % (name, missing, total))
    else:
        print("check ok: every declared hole is bored in the part")

    split = build_assembly.find_disjoint(project, values, model=model)
    if split:
        failed = True
        print("check failed: %d part(s) severed by their own joinery:"
              % len(split))
        for name, n in split:
            print("  %s: %d disconnected solids" % (name, n))
    else:
        print("check ok: every part is one connected solid")

    # advisory: what counts as a part's defining outline is the project's call,
    # so a sketch that omits joinery is worth saying out loud but is not a
    # failure.
    drift = build_assembly.find_sketch_drift(project, values, model=model)
    for name, frac in drift:
        print("check note: %s loses %.0f%% of its defining outline to joinery -"
              " its sketch does not describe it" % (name, 100.0 * frac))

    asm = os.path.join(project.dist, project.name + ".FCStd")
    if not os.path.exists(asm):
        print("check skipped: build the assembly first (fcad build)")
    else:
        free = build_assembly.find_unconstrained(asm)
        if free:
            failed = True
            print("check failed: %d unconstrained component(s):" % len(free))
            for name in free:
                print("  " + name)
        else:
            print("check ok: every component is grounded or jointed")

    parts_dir = os.path.join(project.dist, "parts")
    if not os.path.isdir(parts_dir) or not os.listdir(parts_dir):
        print("check skipped: build the parts first (fcad build)")
    else:
        loose = build_parts.find_loose_sketches(parts_dir)
        if loose:
            failed = True
            print("check failed: %d sketch(es) not fully constrained:" % len(loose))
            for name in loose:
                print("  " + name)
        else:
            print("check ok: every part sketch is fully constrained")

    if failed:
        sys.exit(1)


def precommit(project):
    """build everything, then validate it: the pre-commit sanity check."""
    run(project, "all")
    check(project)
    print("precommit ok: built %s" % project.dist)
