"""a project can joint its own assembly, so a hinge is a hinge.

fcad mates every instance to the datum with a Fixed joint, which is the honest
default for a model whose positions python already computed - the solver has
nothing left to resolve. but Fixed is one of the thirteen joint types this
FreeCAD ships, and a lap joint on a single screw is physically a pivot, not a
weld. a project that declares `assemble` is handed the real
`Assembly::AssemblyObject` once the links exist and the anchors are grounded,
and joints it itself.

pinned here: the default still fixes everything (nothing changed for a project
that says nothing), a hook can make a Revolute instead, and either way `check`'s
"every component is grounded or jointed" still holds - a custom joint is a real
joint, not an escape from the assertion.

needs FreeCAD: run with `freecadcmd tests/test_assemble_hook.py`. results go to
$RESULT_FILE.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App

from fcad import testing
from fcad.freecad import build_assembly, build_parts

V = App.Vector
FORMATS = ("fcstd",)


class _Spec:
    def __init__(self, name, placements, grounded=False):
        self.name = name
        self.placements = placements
        self.grounded = grounded
        self.holes = ()
        self.fastener_holes = ()
        self.profile2d = ([(-20.0, -10.0), (20.0, -10.0), (20.0, 10.0),
                           (-20.0, 10.0)], 8.0)
        self.embeds = False

    @property
    def qty(self):
        return len(self.placements)

    def solid(self):
        from fcad.freecad.partdesign import shape_of
        return shape_of(self)

    @property
    def declared(self):
        return True

    def build_into(self, doc, body):
        from fcad.freecad.partdesign import pad_and_bore
        pts, t = self.profile2d
        return pad_and_bore(doc, body, pts, t, (), name=self.name)


class _Project:
    """two plates, the second offset - a lap that could pivot."""

    def __init__(self, root, assemble=None):
        self.name = "thinge"
        self.root = root
        self.assemble = assemble

    def compute(self, values):
        return {"specs": [
            _Spec("base", [App.Placement()], grounded=True),
            _Spec("arm", [App.Placement(V(30, 0, 8), App.Rotation())]),
        ]}

    def from_spec(self, spec):
        return spec.solid()

    def profile(self, spec):
        return spec.profile2d

    def defaults(self):
        return {}

    @property
    def schema(self):
        return []

    @property
    def enum_choices(self):
        return {}

    @property
    def dist(self):
        return os.path.join(self.root, "dist")


def _build(project, tmp):
    """build the parts, then the assembly, and hand back the saved path."""
    dirs = {"parts": os.path.join(tmp, "parts")}
    os.makedirs(dirs["parts"], exist_ok=True)
    values = {}
    data = project.compute(values)
    for spec in data["specs"]:
        build_parts.build_one(project, spec, values, dirs, FORMATS)
    path = os.path.join(tmp, project.name + ".FCStd")
    build_assembly.build_jointed_doc(project, values, data, dirs["parts"], path)
    return path


def _joints(path):
    """(name, joint type) for every joint in a saved assembly."""
    doc = App.openDocument(path)
    try:
        return [(o.Name, getattr(o, "JointType", None)) for o in doc.Objects
                if o.Name.startswith(("Fix_", "Rev_", "Ground_"))]
    finally:
        App.closeDocument(doc.Name)


def main():
    c = testing.Checks()
    tmp = tempfile.mkdtemp(prefix="fcad_hinge_")
    try:
        # 1. no hook: fcad fixes everything to the datum, as it always did.
        path = _build(_Project(tmp), os.path.join(tmp, "default"))
        js = _joints(path)
        c("the default still grounds and fixes (%s)" % (js,),
          any(n.startswith("Ground_") for n, _ in js)
          and any(n.startswith("Fix_") for n, _ in js))
        c("... and every component is grounded or jointed",
          not build_assembly.find_unconstrained(path))

        # 2. a hook: the arm swings on the base instead of being welded to it.
        def hinge(doc, asm, links):
            sys.path.insert(0, build_assembly.ASSEMBLY_MOD)
            import JointObject
            joints = doc.getObject("Joints")
            j = joints.newObject("App::FeaturePython", "Rev_arm")
            JointObject.Joint(j, build_assembly.joint_type(JointObject, "Revolute"))
            j.Reference1 = build_assembly.whole_of(asm, links["base"][0])
            j.Reference2 = build_assembly.whole_of(asm, links["arm"][0])

        path = _build(_Project(tmp, assemble=hinge), os.path.join(tmp, "hinge"))
        js = _joints(path)
        names = [n for n, _ in js]
        c("a project can joint its own assembly (%s)" % (js,),
          "Rev_arm" in names)
        c("... as a Revolute, not fcad's Fixed",
          dict(js).get("Rev_arm") == "Revolute")
        c("... and fcad added no Fixed joint of its own",
          not any(n.startswith("Fix_") for n in names))
        c("... while the assembly is still fully constrained for `check`",
          not build_assembly.find_unconstrained(path))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
