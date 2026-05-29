"""open the assembly in the freecad gui with everything visible and fitted.

documents written by freecadcmd carry no gui view state, so when the gui opens
them the components can come up hidden. this runs inside the gui (via
`fcad view`), forces every component visible, and fits the view. the assembly is
saved fully baked, so this opens it read-only (no recompute). the assembly path
is passed in FCAD_ASM (or derived from FCAD_DIST/FCAD_NAME) to avoid the gui
treating it as a file to open on the command line.
"""

import os
import sys

import FreeCAD as App

try:
    import FreeCADGui as Gui
except Exception:
    Gui = None

VISIBLE_TYPES = ("App::Link", "Part::Feature", "Assembly::AssemblyObject")
_DIST = os.environ.get("FCAD_DIST", os.path.join(os.getcwd(), "dist"))
_NAME = os.environ.get("FCAD_NAME", "assembly")
asm = os.environ.get("FCAD_ASM", os.path.join(_DIST, _NAME + ".FCStd"))


def _harden_joint_viewprovider():
    """make the Assembly joints tolerate being built headless.

    the assembly's joints are created by freecadcmd, which has no gui, so their
    view objects never get a real ViewProviderJoint proxy and attach() (which
    builds the switch_JCS* coin nodes) never runs. when the gui later restores
    the document two things blow up:
    - `Joint.redrawJointPlacements` calls `joint.ViewObject.Proxy.redraw...`,
      but Proxy is still the default int -> AttributeError.
    - if a ViewProviderJoint *is* present, restore replays Placement1/2 and
      fires `updateData` before attach builds the coin nodes -> AttributeError.
    we guard both entry points to no-op until things are ready (the jcs markers
    are gui decoration we don't need), removing the tracebacks for good."""
    sys.path.insert(0, "/usr/share/freecad/Mod/Assembly")
    import JointObject

    vp = JointObject.ViewProviderJoint
    for name in ("updateData", "redrawJointPlacements"):
        orig = getattr(vp, name)

        def vp_guarded(self, *a, _orig=orig):
            if not hasattr(self, "switch_JCS1"):
                return
            return _orig(self, *a)

        setattr(vp, name, vp_guarded)

    j_orig = JointObject.Joint.redrawJointPlacements

    def joint_guarded(self, joint, _orig=j_orig):
        vo = getattr(joint, "ViewObject", None)
        proxy = getattr(vo, "Proxy", None) if vo is not None else None
        if not hasattr(proxy, "redrawJointPlacements"):
            return
        return _orig(self, joint)

    JointObject.Joint.redrawJointPlacements = joint_guarded


def main():
    if Gui is not None:
        _harden_joint_viewprovider()
    doc = App.openDocument(asm)
    App.setActiveDocument(doc.Name)
    # the assembly is saved fully baked (build purges the joint touched flags),
    # so opening is read-only: no recompute, which would only re-run the joints'
    # unsettleable dependency cycle and log "still touched" once per joint.
    if Gui is None:
        return
    for o in doc.Objects:
        if o.ViewObject is not None and o.TypeId in VISIBLE_TYPES:
            try:
                o.ViewObject.Visibility = True
            except Exception:
                pass
    view = Gui.activeDocument().activeView()
    view.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    # forcing Visibility marks the gui document modified (a freecadcmd build
    # writes no GuiDocument, so the components open hidden and we must show them).
    # that view state is display-only and the build owns the file, so clear the
    # flag, otherwise closing the gui prompts to save changes we never want.
    Gui.getDocument(doc.Name).Modified = False
