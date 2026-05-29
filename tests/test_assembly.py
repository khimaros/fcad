"""regression test for the built assembly's saved state (fcad.freecad.build_assembly).

the Assembly workbench's Fixed joints reference the assembly container, which
forms an `Assembly`<->joint dependency cycle (a non-DAG). a headless topological
recompute therefore cannot settle the joints and they stay `touched`. if the
build saves in that state, every open re-runs the unsettleable graph and spams
`still touched after recompute` once per joint, and the gui viewer has to
recompute on load just to (fail to) settle it.

the build purges the touched flags before saving (the part positions are
already baked into each App::Link placement), so the saved artifact opens clean
and the viewer can be purely read-only. this asserts that invariant against a
freshly built (tiny) assembly produced by the real build_jointed_doc path.

needs FreeCAD: run with `freecadcmd tests/test_assembly.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import os
import shutil
import sys
import tempfile

# make the in-repo fcad package importable under FreeCAD's bundled python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App
import Part

from fcad.freecad import build_assembly

V = App.Vector


class _Spec:
    def __init__(self, name, placements, grounded=False):
        self.name = name
        self.placements = placements
        self.grounded = grounded


class _Project:
    # an empty schema means an empty VarSet; we only care about the joints here.
    name = "tassembly"
    schema = []
    enum_choices = {}


def _make_part(parts_dir, name):
    doc = App.newDocument(name)
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(10, 10, 10)
    doc.recompute()
    doc.saveAs(os.path.join(parts_dir, name + ".FCStd"))
    App.closeDocument(doc.Name)


def _capture_stderr(fn):
    """run fn with the C++ fd-2 stream captured, returning (result, stderr_text).

    the noisy `still touched` / `must be a DAG` lines come from the FreeCAD
    kernel (c++), so a python-level redirect won't see them; dup the real fd."""
    r, w = os.pipe()
    old = os.dup(2)
    os.dup2(w, 2)
    try:
        result = fn()
    finally:
        os.dup2(old, 2)
        os.close(old)
        os.close(w)
        text = os.read(r, 4_000_000).decode(errors="replace")
        os.close(r)
    return result, text


def _build(tmp):
    parts_dir = os.path.join(tmp, "parts")
    os.makedirs(parts_dir)
    for n in ("base", "arm"):
        _make_part(parts_dir, n)
    specs = [
        _Spec("base", [App.Placement(V(0, 0, 0), App.Rotation())], grounded=True),
        _Spec("arm", [App.Placement(V(20, 0, 0), App.Rotation()),
                      App.Placement(V(40, 0, 0), App.Rotation())]),
    ]
    path = os.path.join(tmp, "tassembly.FCStd")
    _, err = _capture_stderr(
        lambda: build_assembly.build_jointed_doc(
            _Project(), {}, {"specs": specs}, parts_dir, path))
    return path, err


def main():
    checks = []
    tmp = tempfile.mkdtemp()
    try:
        path, build_err = _build(tmp)
        doc = App.openDocument(path)
        try:
            joints = [o for o in doc.Objects if o.Name.startswith("Fix_")]
            touched = [o.Name for o in doc.Objects if "Touched" in o.State]
        finally:
            App.closeDocument(doc.Name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    spam = build_err.count("still touched")
    dag = build_err.count("must be a DAG")

    # the assembly must actually contain the fixed joints (else the test is vacuous)...
    checks.append(("built assembly has fixed joints", len(joints) > 0))
    # ...and open with nothing left touched, so the viewer never needs to recompute.
    checks.append(("nothing touched on open (%d touched)" % len(touched),
                   len(touched) == 0))
    # the build must not recompute the joints' non-DAG cycle (solve only), so it
    # emits neither "still touched after recompute" nor "graph must be a DAG".
    checks.append(("no 'still touched' spam during build (%d)" % spam, spam == 0))
    checks.append(("no 'must be a DAG' spam during build (%d)" % dag, dag == 0))

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


if any(a.endswith("test_assembly.py") for a in sys.argv):
    raise SystemExit(main())
