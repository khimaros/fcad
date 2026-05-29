"""rebuild the model from the Parameters VarSet of the active document.

because part and hole counts are parametric, a native expression recompute cannot
add or remove objects on its own, so regenerating from a script is the idiomatic
freecad answer. the shipped rebuild.FCMacro is a thin shim that puts fcad on
sys.path and calls run(): it re-runs the build with the panel's values and reopens
the linked assembly. it is generic across projects; the project is located from the
active document's path (the built assembly sits at <project>/dist/<name>.FCStd).
"""

import os

import FreeCAD as App

from fcad.loader import load_project
from fcad.freecad import dispatch


def _root_from_active():
    doc = App.ActiveDocument
    if doc and doc.FileName:
        # <root>/dist/<name>.FCStd -> the project root is two levels up
        return os.path.dirname(os.path.dirname(doc.FileName))
    return os.getcwd()


def run():
    os.environ["FCAD_PROJECT"] = _root_from_active()
    proj = load_project()
    values = proj.defaults()
    vs = App.ActiveDocument.getObject("Parameters") if App.ActiveDocument else None
    if vs is not None:
        for name in values:
            if hasattr(vs, name):
                v = getattr(vs, name)
                values[name] = float(v) if hasattr(v, "Value") else v
    dispatch.build_all(proj, values)
    asm = os.path.join(proj.dist, proj.name + ".FCStd")
    if os.path.exists(asm):
        App.openDocument(asm)
