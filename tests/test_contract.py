"""end-to-end test for the minimal (inference) project contract.

a whole project can be a single conventional file: module-level PARAMS + compute
(returning fcad.Part), with fcad inferring the varset schema, enum choices,
qty/length and the assembly anchor. this writes such a project to a temp dir,
loads it through the real loader, builds it through the real part/assembly
builders, and asserts the invariants the explicit Project once carried by hand:
inferred property types, enum dropdown, a fully-constrained defining sketch from
profile2d, auto-grounding when nothing is flagged, and computed bom rows. it also
checks fem material resolution (project default + a project-registered material).

needs FreeCAD: run with `freecadcmd tests/test_contract.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import csv
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App  # noqa: E402
import ObjectsFem      # noqa: E402

from fcad import config                                  # noqa: E402
from fcad.freecad import build_parts, build_assembly, fem, util  # noqa: E402

# a complete single-file project: two unflagged parts (so auto-ground must pick
# one), one carrying a profile2d (so a defining sketch is built and must be fully
# constrained), an enum param with declared choices, and a default fem material.
PROJECT_PY = '''
import fcad
import Part, FreeCAD as App
V = App.Vector

PARAMS = {"plate_len": 100.0, "plate_wid": 60.0, "thk": 10.0,
          "post": 20.0, "grade": "A"}
PARAM_META = {"plate_len": {"group": "Size"},
              "grade": {"group": "Grade", "choices": ["A", "B", "C"]}}
MATERIAL = "wood"

def _rect(l, w):
    return [(-l/2, -w/2), (l/2, -w/2), (l/2, w/2), (-l/2, w/2)]

def compute(p):
    l, w, t, s = p["plate_len"], p["plate_wid"], p["thk"], p["post"]
    plate = fcad.Part("plate", placements=[App.Placement()], length=l,
                      profile="plate", profile2d=(_rect(l, w), t),
                      solid=lambda: Part.makeBox(l, w, t, V(-l/2, -w/2, -t/2)))
    post = fcad.Part("post", placements=[App.Placement(V(200, 0, 0), App.Rotation())],
                     profile="post",
                     solid=lambda: Part.makeBox(s, s, 80, V(-s/2, -s/2, -40)))
    return [plate, post]
'''


def _load(proj_dir):
    """import the temp project through the real loader, as the cli would."""
    os.environ[config.ENV_PROJECT] = proj_dir
    os.environ[config.ENV_NAME] = "tcontract"
    os.environ.pop(config.ENV_DIST, None)
    from fcad.loader import load_project
    return load_project()


def main():
    checks = []

    def ck(name, ok):
        checks.append((name, bool(ok)))

    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "project.py"), "w") as f:
            f.write(PROJECT_PY)
        project = _load(tmp)

        # --- inference: the Project assembled itself from globals ---
        ck("single-file project loaded (no PROJECT object)", project is not None)
        ck("name from FCAD_NAME", project.name == "tcontract")
        ck("root is the project dir", project.root == tmp)
        schema = {row[0]: row for row in project.schema}
        ck("float -> PropertyLength", schema["plate_len"][1] == "App::PropertyLength")
        ck("str+choices -> PropertyEnumeration",
           schema["grade"][1] == "App::PropertyEnumeration")
        ck("PARAM_META group applied", schema["plate_len"][2] == "Size")
        ck("default group otherwise", schema["thk"][2] == "Parameters")
        ck("enum choices inferred", project.enum_choices.get("grade") == ["A", "B", "C"])

        # --- the inferred varset actually instantiates with the dropdown set ---
        vdoc = App.newDocument("vtest")
        vs = util.add_varset(vdoc, project, project.defaults())
        ck("varset enum carries its choices", list(vs.getEnumerationsOfProperty("grade")) == ["A", "B", "C"])
        App.closeDocument(vdoc.Name)

        # --- build through the real builders (light formats, fast) ---
        vals = project.defaults()
        d = dispatch_dirs(project)
        build_parts.build(project, vals, d, {"fcstd", "sketch"})
        build_assembly.build(project, vals, d, {"fcstd", "bom"})

        # profile2d became a fully-constrained defining sketch
        loose = build_parts.find_loose_sketches(d["parts"])
        ck("defining sketch fully constrained (%s)" % (loose or "none"), not loose)

        # auto-ground: nothing was flagged, yet the assembly is fully constrained
        asm = os.path.join(d["dist"], project.name + ".FCStd")
        free = build_assembly.find_unconstrained(asm)
        ck("auto-grounded: no unconstrained components (%s)" % (free or "none"), not free)
        ck("no interfering parts", not build_assembly.find_overlaps(project, vals))

        # bom: qty from placements, length from the explicit value / bbox
        with open(os.path.join(d["dist"], project.name + "-bom.csv")) as f:
            bom = {r["part"]: r for r in csv.DictReader(f)}
        ck("bom qty from placements", bom["plate"]["qty"] == "1")
        ck("bom length explicit", bom["plate"]["length_mm"] == "100.0")
        ck("bom length from bbox", bom["post"]["length_mm"] == "20.0")

        # --- a standalone .fcad file is a whole project, discovered + loaded ---
        fdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(fdir, "widget.fcad"), "w") as f:
                f.write('PARAMS = {"r": 5.0}\n'
                        'PARAM_META = {"r": {"group": "Geom"}}\n'
                        'def compute(p):\n    return []\n')
            # a dir holding one .fcad needs no flag; fcad discovers it (clear the
            # env the project.py sub-test set, as a fresh cli invocation would).
            for k in ("FCAD_ENTRY", "FCAD_PROJECT", "FCAD_NAME", "FCAD_DIST"):
                os.environ.pop(k, None)
            cfg = config.resolve(fdir)
            ck(".fcad discovered in its dir", cfg.entry.endswith("widget.fcad"))
            ck(".fcad name is the file stem", cfg.name == "widget")
            ck(".fcad root is its dir", cfg.project == fdir)
            for k, v in cfg.env().items():
                os.environ[k] = v
            from fcad.loader import load_project as _load_again
            wp = _load_again()
            ck(".fcad project loads + infers schema",
               wp.name == "widget" and wp.schema[0][2] == "Geom")
        finally:
            shutil.rmtree(fdir, ignore_errors=True)
            for k in ("FCAD_ENTRY", "FCAD_PROJECT", "FCAD_NAME"):
                os.environ.pop(k, None)

        # --- fem material: project default + project-registered material ---
        mdoc = App.newDocument("mtest")
        analysis = ObjectsFem.makeAnalysis(mdoc, "Analysis")

        class _P:
            material = "wood"
            materials = {"oak": {"E": 12345.0, "nu": 0.3, "rho": 600.0}}

        fem._material(mdoc, analysis, {}, _P)            # no case material -> default "wood"
        fem._material(mdoc, analysis, {"material": "oak"}, _P)  # registry lookup
        mats = [o for o in mdoc.Objects if hasattr(o, "Material") and "YoungsModulus" in o.Material]
        youngs = [o.Material["YoungsModulus"] for o in mats]
        ck("project default material applied (wood)", any("11000" in y for y in youngs))
        ck("project-registered material applied (oak)", any("12345" in y for y in youngs))
        App.closeDocument(mdoc.Name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok in checks if not ok]
    lines = ["%s %s" % ("ok  " if ok else "FAIL", name) for name, ok in checks]
    lines.append("RESULT %s" % ("PASS" if not failed else "FAIL"))
    text = "\n".join(lines) + "\n"
    rf = os.environ.get("RESULT_FILE")
    if rf:
        with open(rf, "w") as f:
            f.write(text)
    print(text)
    return 0 if not failed else 1


def dispatch_dirs(project):
    from fcad.freecad import dispatch
    return dispatch.dirs(project)


if any(a.endswith("test_contract.py") for a in sys.argv):
    raise SystemExit(main())
