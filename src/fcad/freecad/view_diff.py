"""open a precomputed 3d diff document in the gui.

`fcad diff-build` computes the green/red/grey split headless and saves it as a
plain document of baked solids (freecadcmd cannot set view colors). this opens
that document, colors each solid by which diff layer it came from, and fits the
view. nothing is parametric, so the open is instant and no booleans re-run. the
path comes from FCAD_DIFF (or is derived from FCAD_DIST and DIFF_TARGET)
so the gui does not treat it as a file to open on the command line.
"""

import os

import FreeCAD as App

try:
    import FreeCADGui as Gui
    from PySide import QtWidgets
except Exception:
    Gui = QtWidgets = None

GREEN = (0.0, 0.8, 0.0)
RED = (0.85, 0.0, 0.0)
GREY = (0.6, 0.6, 0.6)
_DIST = os.environ.get("FCAD_DIST", os.path.join(os.getcwd(), "dist"))
_TARGET = os.environ.get("DIFF_TARGET", "assembly")
path = os.environ.get("FCAD_DIFF", os.path.join(_DIST, _TARGET + ".diff.FCStd"))


def _color(name):
    if "_added" in name:
        return GREEN
    if "_removed" in name:
        return RED
    return GREY  # _unchanged


def main():
    doc = App.openDocument(path)
    App.setActiveDocument(doc.Name)
    # the diff doc is baked solids (nothing parametric), so opening is read-only.
    if Gui is None:
        return
    # a freecadcmd-built document carries no gui state, so *every* object opens
    # hidden - the layer groups as well as the solids in them. showing only the
    # solids drew the geometry but left each layer's own row switched off, so the
    # tree said hidden while the model said visible, and toggling a layer did
    # nothing until it had been clicked twice.
    for o in doc.Objects:
        if o.ViewObject is None:
            continue
        try:
            if o.TypeId == "Part::Feature":
                o.ViewObject.ShapeColor = _color(o.Name)
            o.ViewObject.Visibility = True
        except Exception:
            pass
    for _ in range(20):
        QtWidgets.QApplication.processEvents()
    view = Gui.activeDocument().activeView()
    view.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    # the colors/visibility we set are display-only; clear the modified flag so
    # closing the gui doesn't prompt to save (see view.py for the full rationale).
    Gui.getDocument(doc.Name).Modified = False
