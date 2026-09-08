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


def check(project, values=None):
    """validate the assembly: no interpenetrating solids, no unconstrained parts."""
    values = project.defaults() if values is None else values
    failed = False

    hits = build_assembly.find_overlaps(project, values)
    if hits:
        failed = True
        print("check failed: %d overlapping part pair(s):" % len(hits))
        for a, b, vol in hits:
            print("  %s <-> %s : %.1f mm^3" % (a, b, vol))
    else:
        print("check ok: no overlapping parts")

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
