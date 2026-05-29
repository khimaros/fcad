"""regression test for the multiview drawing sheet (fcad.freecad.util).

the part/assembly drawings must be reverse-engineerable: a projection-aligned
grid (top above front, right beside front), an isometric pictorial that is not
upside down, a standard snapped scale, per-view captions, overall extent dims on
every orthographic view (but not the iso), dimensioned multi-row hole patterns,
and a filled title block. this builds a real drawing off a synthetic board and
asserts those invariants, plus that the dxf actually writes.

needs FreeCAD: run with `freecadcmd tests/test_drawing.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App
import Part

from fcad.freecad import util as fcutil, build_parts

V = App.Vector


def main():
    checks = []

    def ck(name, ok):
        checks.append((name, bool(ok)))

    doc = App.newDocument("tdraw")
    obj = doc.addObject("Part::Feature", "board")
    obj.Shape = Part.makeBox(1200, 140, 19)
    doc.recompute()
    # two rows of holes through the thickness, exercising multi-row callouts.
    holes = [(100, 35, 5), (200, 35, 5), (300, 35, 5),
             (100, 105, 5), (200, 105, 5), (300, 105, 5)]

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "board.dxf")
    page = fcutil.make_drawing(doc, [obj], out, build_parts.PART_VIEW, holes=holes,
                               title={"part": "board", "project": "tproj"})

    vp = {v.Caption: v for v in page.Views if v.TypeId == "TechDraw::DrawViewPart"}
    ck("four captioned views (top/front/right/iso)",
       set(vp) == {"TOP", "FRONT", "RIGHT", "ISO"})

    if set(vp) == {"TOP", "FRONT", "RIGHT", "ISO"}:
        t, f, r, i = vp["TOP"], vp["FRONT"], vp["RIGHT"], vp["ISO"]
        ck("top aligned above front (shared vertical projection)",
           abs(t.X - f.X) < 1e-6 and t.Y > f.Y)
        ck("right aligned beside front (shared horizontal projection)",
           abs(f.Y - r.Y) < 1e-6 and r.X > f.X)
        ck("iso fills the free upper-right cell",
           abs(i.X - r.X) < 1e-6 and abs(i.Y - t.Y) < 1e-6)
        ck("all views share one scale", len({v.Scale for v in vp.values()}) == 1)
        ck("scale snapped to a standard ratio (%g)" % t.Scale,
           t.Scale in fcutil.PREFERRED_SCALES)

    # the isometric must not be upside down: its page-up axis points +Z.
    iso_dir, iso_xdir = next((d, x) for lbl, d, x, _c, _r in build_parts.PART_VIEW
                             if lbl == "iso")
    _u, up = fcutil._view_axes(iso_dir, iso_xdir)
    ck("iso is right-side up (page-up has +Z, %.2f)" % up.z, up.z > 0)

    ext = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewDimExtent"]
    hd = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewDimension"]
    # 3 orthographic views x (horizontal + vertical) extent dims; iso gets none.
    ck("extent dims on the 3 ortho views, none on iso (%d)" % len(ext), len(ext) == 6)
    # 2 hole rows x (diameter + pitch) + one shared edge offset.
    ck("multi-row hole pattern dimensioned (%d)" % len(hd), len(hd) == 5)

    tmpl = next(o for o in doc.Objects if o.TypeId == "TechDraw::DrawSVGTemplate")
    et = dict(tmpl.EditableTexts)
    ck("title block filled (part/project/scale/units/size)",
       et.get("Part") == "board" and et.get("Project") == "tproj"
       and et.get("Units") == "mm" and et.get("Scale", "").count(":") == 1
       and et.get("Size") == "1200 x 140 x 19")

    ck("dxf written non-empty", os.path.exists(out) and os.path.getsize(out) > 500)

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


if any(a.endswith("test_drawing.py") for a in sys.argv):
    raise SystemExit(main())
