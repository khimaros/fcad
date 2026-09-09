"""TypeId names checked against the installed FreeCAD.

FreeCAD makes document objects from a TypeId string and, for the workbenches
fcad drives, offers no factory to call instead - so the string stays, but a
wrong one should fail at the point it is written rather than inside a recompute
later. what is pinned here is that the names come from FreeCAD's own registry
and not from a list fcad keeps: a real type resolves, a plausible typo does not,
and nothing had to teach fcad what a Pocket is.

needs FreeCAD: run with `freecadcmd tests/test_types.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App

from fcad import testing, types
from fcad.freecad import build_assembly


def main():
    c = testing.Checks()

    # the value is exactly the string, so it stays cross-referenceable with the
    # FreeCAD docs and interchangeable with a literal.
    c("a name resolves to the plain TypeId (%s)" % types.PartDesign.Pad,
      types.PartDesign.Pad == "PartDesign::Pad")
    c("... and works for the whole set fcad uses",
      [types.PartDesign.Hole, types.Sketcher.SketchObject,
       types.Assembly.AssemblyObject, types.App.Link]
      == ["PartDesign::Hole", "Sketcher::SketchObject",
          "Assembly::AssemblyObject", "App::Link"])

    # a typo fails here, not three recomputes later.
    try:
        bad = types.PartDesign.Poket
        c("a typo is rejected (got %r)" % bad, False)
    except AttributeError as e:
        c("a typo is rejected", True)
        c("... and suggests the real name (%s)" % e, "PartDesign::Pocket" in str(e))

    # the point of resolving against the registry: fcad never enumerated these.
    c("a type fcad's source never mentions still resolves (%s)"
      % types.PartDesign.Groove, types.PartDesign.Groove == "PartDesign::Groove")
    c("... and dir() lists what this build actually has (%d PartDesign types)"
      % len(dir(types.PartDesign)), len(dir(types.PartDesign)) > 20)

    # and the joint type: an index into JointObject.JointTypes, by name.
    sys.path.insert(0, build_assembly.ASSEMBLY_MOD)
    import JointObject
    idx = build_assembly.joint_type(JointObject, build_assembly.JOINT_FIXED)
    c("the fixed joint is found by name (index %d of %d)"
      % (idx, len(JointObject.JointTypes)),
      JointObject.JointTypes[idx] == "Fixed")

    try:
        build_assembly.joint_type(JointObject, "Teleport")
        c("an unknown joint type is refused", False)
    except SystemExit as e:
        c("an unknown joint type is refused, naming the real ones",
          "Revolute" in str(e))

    return c.report()


testing.main(main, __file__)
