"""3d geometry diff between the working tree and git HEAD.

loads the committed (old) and working (new) STEP of a target (the whole
assembly, or a single part) and sorts the material into three toggleable layers:

    green  = added material   (new minus old)
    red    = removed material (old minus new)
    grey   = unchanged

the diff runs per solid, not on the whole compound at once: each instance is
matched to its old counterpart by a placement+geometry signature, and matched
(unchanged) instances are emitted straight to grey with no boolean. only the
residual (solids that were added, removed, or resized) is actually cut. that
keeps the combined assembly view while avoiding the meltdown of booleaning
dozens of interpenetrating solids together (which made `fcad diff` take minutes).

invoked by `fcad diff`, which builds the HEAD outputs in a temp worktree and
opens this in the FreeCAD gui. headless it still builds the diff document
(without colors) so the booleans can be verified. inputs come from env vars so
the gui launcher does not treat the directory paths as documents to open:
DIFF_NEW, DIFF_OLD, DIFF_TARGET, and FCAD_NAME (the assembly stem).
"""

import os
from collections import defaultdict

import FreeCAD as App
import Part

GREEN = (0.0, 0.8, 0.0)
RED = (0.85, 0.0, 0.0)
GREY = (0.6, 0.6, 0.6)
EPS = 1e-6
NAME = os.environ.get("FCAD_NAME", "assembly")


def _load(path):
    s = Part.Shape()
    s.read(path)
    return s


def _nonempty(shape):
    return shape is not None and not shape.isNull() and shape.Volume > EPS


def _sig(solid):
    """placement+geometry signature: identical instances collide, while resized
    or moved ones do not. rounded so float noise from a re-export does not split
    a genuinely-unchanged instance off into the residual."""
    bb = solid.BoundBox
    q = lambda x: round(x, 3)
    return (q(solid.Volume), q(bb.XLength), q(bb.YLength), q(bb.ZLength),
            q(bb.XMin), q(bb.YMin), q(bb.ZMin))


def _match(new_solids, old_solids):
    """split into (unchanged, new_only, old_only) by signature. unchanged
    instances need no boolean; only the residual is actually diffed."""
    old_by = defaultdict(list)
    for s in old_solids:
        old_by[_sig(s)].append(s)
    unchanged, new_only = [], []
    for s in new_solids:
        bucket = old_by.get(_sig(s))
        if bucket:
            bucket.pop()
            unchanged.append(s)
        else:
            new_only.append(s)
    old_only = [s for bucket in old_by.values() for s in bucket]
    return unchanged, new_only, old_only


def _layer(doc, name, color):
    if App.GuiUp:
        import Draft
        lyr = Draft.make_layer(name)
        lyr.ViewObject.ShapeColor = color
        lyr.ViewObject.LineColor = color
        return lyr
    return doc.addObject("App::DocumentObjectGroup", name.split(" ")[0])


def _add(layer, doc, shape, label, color):
    if not _nonempty(shape):
        return
    obj = doc.addObject("Part::Feature", label)
    obj.Shape = shape
    if App.GuiUp:
        obj.ViewObject.ShapeColor = color
    if hasattr(layer, "addObject"):
        layer.addObject(obj)
    else:
        layer.Group = layer.Group + [obj]


def _rel(target):
    return NAME + ".step" if target in ("assembly", NAME) else \
        os.path.join("parts", target + ".step")


def diff(new_dir, old_dir, target="assembly", doc=None):
    doc = doc or App.newDocument("diff")
    added = _layer(doc, "added green", GREEN)
    removed = _layer(doc, "removed red", RED)
    unchanged = _layer(doc, "unchanged grey", GREY)

    rel = _rel(target)
    new_s = _load(os.path.join(new_dir, rel))
    old_path = os.path.join(old_dir, rel)
    old_s = _load(old_path) if os.path.exists(old_path) else None

    if old_s is None or old_s.isNull():
        _add(added, doc, new_s, target + "_added", GREEN)
        doc.recompute()
        return doc

    same, new_only, old_only = _match(new_s.Solids or [new_s],
                                      old_s.Solids or [old_s])
    if same:
        _add(unchanged, doc, Part.makeCompound(same), target + "_unchanged", GREY)
    if new_only or old_only:
        nr = Part.makeCompound(new_only)
        orr = Part.makeCompound(old_only)
        if new_only and old_only:
            _add(added, doc, nr.cut(orr), target + "_added", GREEN)
            _add(removed, doc, orr.cut(nr), target + "_removed", RED)
            _add(unchanged, doc, nr.common(orr), target + "_changed", GREY)
        elif new_only:
            _add(added, doc, nr, target + "_added", GREEN)
        else:
            _add(removed, doc, orr, target + "_removed", RED)
    doc.recompute()
    return doc


def main():
    doc = diff(os.environ.get("DIFF_NEW", "dist"),
               os.environ.get("DIFF_OLD", "dist"),
               os.environ.get("DIFF_TARGET", "assembly"))
    # the booleans are now baked into static solids; saving lets the gui open the
    # result instantly (diff-open) without recomputing anything.
    out = os.environ.get("DIFF_OUT")
    if out:
        doc.saveAs(out)
        print("diff saved: %s" % out)
