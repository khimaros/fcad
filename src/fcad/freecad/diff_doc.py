"""3d geometry diff between the working tree and git HEAD.

sorts a design's material into three toggleable layers:

    green  = added material   (new minus old)
    red    = removed material (old minus new)
    grey   = unchanged

the diff is **per part**. each part type is diffed against its own previous
version once, in its own natural frame, and the resulting green/red/grey shapes
are then placed at every instance that kept its placement - a transform, not a
boolean. so the kernel work scales with the number of part types that changed,
not with the number of instances, and a revision that only moves things around
costs no booleans at all. diffing the assembly as one pile of solids instead
melts down: cutting a compound of dozens of interpenetrating solids against
another is superlinear in their combined faces, and on a real assembly it does
not finish.

pairing by part also stops parts contaminating each other. a whole-assembly cut
subtracts every old solid from every new one, so a screw that moved carves a
spurious void out of a board that grew; here a part is only ever cut against
itself. that does mean the layers answer "what changed about each part" rather
than "what matter is present now": material replaced by a *different* part shows
as both red and green in the same place, which is the honest per-part answer.

instances pair by placement: identical placements pair up, leftovers pair by
nearest translation (so an instance that moved is diffed against where it came
from), and a true surplus or shortfall of instances is wholly green or wholly
red.

reads only `parts/<part>.step` and `<name>-placements.json` from each dist. the
builder writes that manifest because the diff cannot call the project's
`compute` twice: the two answers live in two different revisions of its code.

this always runs headless, under freecadcmd, whether it was reached through
`fcad diff-build` or the git driver: it bakes the three layers as plain groups of
static solids and saves them. there is no ViewObject here to colour, so the
green/red/grey is `view_diff`'s job when the result is opened. inputs come from
env vars rather than argv so the gui launcher cannot mistake a path for a
document to open: DIFF_NEW, DIFF_OLD, DIFF_DOCS, DIFF_TARGET, DIFF_OUT and
FCAD_NAME (the assembly stem).
"""

import os
from collections import defaultdict

import FreeCAD as App
import Part

from fcad import config
from fcad.freecad import util as fcutil

EPS = 1e-6
# rounding for the "did this change?" comparisons: loose enough that float noise
# from a re-export does not split something genuinely unchanged off, tight enough
# to catch a feature that merely moved.
GEOM_PLACES = 3
POS_PLACES = 3
ROT_PLACES = 6
NAME = os.environ.get("FCAD_NAME", "assembly")
# (group name, object suffix), in green/red/grey order throughout. the colors are
# `view_diff`'s business: this module only ever runs headless, where there is no
# ViewObject to put one on.
LAYERS = (("added", "_added"),
          ("removed", "_removed"),
          ("unchanged", "_unchanged"))
# objects in a saved document that carry their own solid (as opposed to an
# App::Link, which carries a placement and borrows its shape from a part file).
SOLID_TYPES = ("Part::Extrusion", "Part::Feature", "Part::FeaturePython")
IDENTITY = App.Placement()


def _load(path):
    s = Part.Shape()
    s.read(path)
    return s


def _nonempty(shape):
    return shape is not None and not shape.isNull() and shape.Volume > EPS


def _sig(shape):
    """geometry signature: volume, bounding box, and every face's area and
    centroid. volume and bbox alone would miss a hole that merely moved, and a
    part wrongly called unchanged drops out of the diff silently."""
    q = lambda x: round(x, GEOM_PLACES)
    bb = shape.BoundBox
    faces = sorted((q(f.Area), q(f.CenterOfMass.x), q(f.CenterOfMass.y),
                    q(f.CenterOfMass.z)) for f in shape.Faces)
    return (q(shape.Volume), q(bb.XLength), q(bb.YLength), q(bb.ZLength),
            q(bb.XMin), q(bb.YMin), q(bb.ZMin), tuple(faces))


def _psig(pl):
    return (round(pl.Base.x, POS_PLACES), round(pl.Base.y, POS_PLACES),
            round(pl.Base.z, POS_PLACES),
            tuple(round(v, ROT_PLACES) for v in pl.Rotation.Q))


def _place(shape, pl):
    s = shape.copy()
    s.Placement = pl
    return s


def _match(new_pls, old_pls):
    """(kept, moved, added, removed) instance placements.

    identical placements pair up first; the leftovers pair by nearest
    translation, so an instance that moved is diffed against where it came from
    instead of showing as an unrelated add and delete stacked on each other."""
    old_by = defaultdict(list)
    for p in old_pls:
        old_by[_psig(p)].append(p)
    kept, spare = [], []
    for p in new_pls:
        bucket = old_by.get(_psig(p))
        if bucket:
            bucket.pop()
            kept.append(p)
        else:
            spare.append(p)
    left = [p for bucket in old_by.values() for p in bucket]
    moved = []
    for p in spare:
        if not left:
            break
        i = min(range(len(left)), key=lambda k: p.Base.distanceToPoint(left[k].Base))
        moved.append((p, left.pop(i)))
    return kept, moved, spare[len(moved):], left


def _part_diff(new_shape, old_shape):
    """(added, removed, common) for a part against its previous version, in the
    part's own frame: the one boolean a changed part costs, whatever its
    instance count. an unchanged part costs none."""
    if _sig(new_shape) == _sig(old_shape):
        return None, None, new_shape
    return (new_shape.cut(old_shape), old_shape.cut(new_shape),
            new_shape.common(old_shape))


def _part_layers(new_shape, old_shape, new_pls, old_pls):
    """(green, red, grey) shapes for one part type, in assembly coordinates."""
    if new_shape is None and old_shape is None:
        return [], [], []  # in the manifest but never exported
    if old_shape is None:
        return [_place(new_shape, p) for p in new_pls], [], []
    if new_shape is None:
        return [], [_place(old_shape, p) for p in old_pls], []

    layers = ([], [], [])
    kept, moved, added, removed = _match(new_pls, old_pls)
    for shape, bucket in zip(_part_diff(new_shape, old_shape), layers):
        if _nonempty(shape):
            bucket += [_place(shape, p) for p in kept]
    # an instance that moved cannot reuse the part-local result, so it pays its
    # own (small, two-solid) boolean where it sits.
    for new_pl, old_pl in moved:
        ns, os_ = _place(new_shape, new_pl), _place(old_shape, old_pl)
        for shape, bucket in zip((ns.cut(os_), os_.cut(ns), ns.common(os_)), layers):
            if _nonempty(shape):
                bucket.append(shape)
    layers[0].extend(_place(new_shape, p) for p in added)
    layers[1].extend(_place(old_shape, p) for p in removed)
    return layers


def _placements(dist):
    path = config.placements_path(dist, NAME)
    if not os.path.exists(path):
        raise RuntimeError("no placement manifest at %s: build the assembly "
                           "first (fcad build)" % path)
    return fcutil.read_placements(path)


def _part_shape(dist, name):
    path = os.path.join(dist, "parts", name + ".step")
    return _load(path) if os.path.exists(path) else None


def _combine(new_parts, old_parts):
    """(green, red, grey) over every part named on either side.

    both inputs are {part name: (shape, [placement])}, whether they came from a
    built dist/ or straight out of a saved document."""
    layers = ([], [], [])
    for name in sorted(set(new_parts) | set(old_parts)):
        new_shape, new_pls = new_parts.get(name, (None, []))
        old_shape, old_pls = old_parts.get(name, (None, []))
        for bucket, shapes in zip(layers,
                                  _part_layers(new_shape, old_shape,
                                               new_pls, old_pls)):
            bucket += shapes
    return layers


def _dist_parts(dist):
    """{part: (shape, [placement])} from a built dist/: part STEPs + manifest."""
    out = {}
    for name, placements in _placements(dist).items():
        shape = _part_shape(dist, name)
        if shape is not None:
            out[name] = (shape, placements)
    return out


def _doc_parts(path):
    """{part: (shape, [placement])} from a saved .FCStd, or {} if there is none.

    a part document holds its own solid, so it diffs as geometry. an assembly
    document holds no geometry at all - only App::Links, each carrying a
    placement and resolving its shape from the part file on disk - so it diffs as
    placements, which is exactly what that file records. a part's *geometry*
    changing is a change to the part file, which git diffs separately."""
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    doc = App.openDocument(path)
    try:
        out = {}
        for o in doc.Objects:
            if o.TypeId == "App::Link":
                target = o.LinkedObject
                name = target.Name if target is not None else o.Name
                shape, placement = getattr(target, "Shape", None), o.Placement
            elif o.TypeId in SOLID_TYPES:
                # the solid already carries its own placement; do not apply it twice.
                name, shape, placement = o.Name, getattr(o, "Shape", None), IDENTITY
            else:
                continue
            if shape is None or shape.isNull():
                continue
            out.setdefault(name, (shape, []))[1].append(placement)
        return out
    finally:
        App.closeDocument(doc.Name)


def _single_part_layers(new_dir, old_dir, target):
    """`fcad diff PART`: one part in its own frame, no placements involved."""
    new_shape = _part_shape(new_dir, target)
    if new_shape is None:
        raise RuntimeError("no part %r under %s/parts" % (target, new_dir))
    old_shape = _part_shape(old_dir, target)
    if old_shape is None:
        return [new_shape], [], []
    return tuple([s] if _nonempty(s) else []
                 for s in _part_diff(new_shape, old_shape))


def _add(group, doc, shape, label):
    if not _nonempty(shape):
        return
    obj = doc.addObject("Part::Feature", label)
    obj.Shape = shape
    group.Group = group.Group + [obj]


def _bake(layers, label, doc=None):
    """put the three layers into a document of static solids."""
    doc = doc or App.newDocument("diff")
    for shapes, (name, suffix) in zip(layers, LAYERS):
        group = doc.addObject("App::DocumentObjectGroup", name)
        if shapes:
            _add(group, doc, Part.makeCompound(shapes), label + suffix)
    doc.recompute()
    return doc


def diff(new_dir, old_dir, target="assembly", doc=None):
    """diff two built dist/ trees (what `fcad diff` compares)."""
    layers = (_combine(_dist_parts(new_dir), _dist_parts(old_dir))
              if target in ("assembly", NAME)
              else _single_part_layers(new_dir, old_dir, target))
    return _bake(layers, target, doc)


def diff_docs(new_path, old_path, label="document", doc=None):
    """diff two saved revisions of one .FCStd (what `git diff` compares).

    either side may be missing, which is how git spells a file that was created
    or deleted; that side simply contributes nothing and the other is wholly
    green or wholly red."""
    new_parts, old_parts = _doc_parts(new_path), _doc_parts(old_path)
    if not new_parts and not old_parts:
        raise RuntimeError(
            "no geometry in either revision of %s: an assembly document carries "
            "only links, so its part files must be built to resolve them" % label)
    return _bake(_combine(new_parts, old_parts), label, doc)


def main():
    """DIFF_NEW/DIFF_OLD name two built dist/ trees, or two saved .FCStd
    revisions of one document when DIFF_DOCS is set (the git driver's case)."""
    new, old = os.environ.get("DIFF_NEW", "dist"), os.environ.get("DIFF_OLD", "dist")
    target = os.environ.get("DIFF_TARGET", "assembly")
    doc = (diff_docs(new, old, target) if os.environ.get("DIFF_DOCS")
           else diff(new, old, target))
    # the booleans are now baked into static solids; saving lets the gui open the
    # result instantly (diff-open) without recomputing anything.
    out = os.environ.get("DIFF_OUT")
    if out:
        doc.saveAs(out)
        print("diff saved: %s" % out)
