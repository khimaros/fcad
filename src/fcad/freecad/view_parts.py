"""open the built part files in the freecad gui, view-ready, and stay open.

freecadcmd writes no gui view state, so a freecadcmd-built part opens with every
object hidden (it looks empty, holes and all). this runs in the gui (via
`fcad view-parts`): it opens each part (or just FCAD_PART), shows the solid, hides
its defining sketch, frames it, and saves so the file also opens view-ready on a
later plain double-click. it deliberately does not exit, so the documents stay
open for inspection; run it in the background. the parts dir comes from
FCAD_DIST.
"""

import glob
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets

SOLID_TYPES = ("Part::Extrusion", "Part::Feature", "Part::FeaturePython")
DIST = os.environ.get("FCAD_DIST", os.path.join(os.getcwd(), "dist"))


def _pump():
    for _ in range(20):
        QtWidgets.QApplication.processEvents()


def _open_view_ready(path):
    doc = App.openDocument(path)
    Gui.setActiveDocument(doc.Name)
    _pump()
    for o in doc.Objects:
        if o.ViewObject is not None:
            o.ViewObject.Visibility = o.TypeId in SOLID_TYPES
    _pump()
    view = Gui.activeDocument().activeView()
    view.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    _pump()
    doc.save()
    # App.Document.save() writes the GuiDocument (so the part opens view-ready
    # next time) but leaves the gui's own Modified flag set, so the window still
    # reads "unsaved changes"; clear it explicitly.
    Gui.getDocument(doc.Name).Modified = False
    print("view-ready:", path)


def _paths():
    # a specific part if FCAD_PART names one that exists, else every part
    # (so the assembly default / no target just opens them all).
    part = os.environ.get("FCAD_PART", "").strip()
    parts_dir = os.path.join(DIST, "parts")
    one = os.path.join(parts_dir, part + ".FCStd")
    if part and os.path.exists(one):
        return [one]
    return sorted(glob.glob(os.path.join(parts_dir, "*.FCStd")))


def main():
    for fcstd in _paths():
        if os.path.exists(fcstd):
            _open_view_ready(fcstd)
    # no exit/close: leave the documents open in the gui for inspection.
