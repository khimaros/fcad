"""end-to-end test for dimensioned multi-view SVG sheets (`dist/<name>.svg` and
`dist/parts/<part>.svg`).

drives `fcad build svg sketches` on a small two-part fixture and parses the
emitted SVGs as XML to verify all four projection-aligned views (TOP, FRONT,
RIGHT, ISO), hidden lines, overall extent measurements, hole callouts, title
block, and assembly BOM, while confirming `dist/sketches/<part>.svg` stays a
plain 1:1 defining sketch.
"""

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import cli, testing

FIXTURE = '''
from FreeCAD import Placement, Vector
import fcad
from fcad.freecad import partdesign

PARAMS = {"length": 120.0, "width": 60.0, "height": 20.0, "bore": 16.0}

def _block(doc, body, p):
    l, w = p["length"] / 2.0, p["width"] / 2.0
    partdesign.pad_and_bore(doc, body, [(-l, -w), (l, -w), (l, w), (-l, w)],
                            p["height"], [(0.0, 0.0, p["bore"])])

def _cap(doc, body, p):
    partdesign.pad_and_bore(doc, body, [(-20, -15), (20, -15), (20, 15), (-20, 15)],
                            10.0)

def compute(p):
    return [
        fcad.PartSpec("block", grounded=True, profile="al plate",
                      length=p["length"],
                      build=lambda doc, body: _block(doc, body, p),
                      dimension_sketches=["*_bore*"],
                      openings=["*_bore*"]),
        fcad.PartSpec("cap", placements=[Placement(Vector(0, 0, p["height"] / 2.0 + 5.0),
                                                   Placement().Rotation)],
                      profile="al bar", length=40.0,
                      build=lambda doc, body: _cap(doc, body, p)),
    ]
'''


def _parse_svg(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return text, ET.fromstring(text)


def _find_by_id(root, elem_id):
    for el in root.iter():
        if el.attrib.get("id") == elem_id:
            return el
    return None


def _path_count(el):
    if el is None:
        return 0
    return sum(1 for ch in el.iter() if ch.tag.endswith("path"))


def main():
    c = testing.Checks()
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "widget.fcad")
        with open(src, "w", encoding="utf-8") as f:
            f.write(FIXTURE)

        rc = cli.main(["-p", src, "build", "svg", "sketches"])
        c("fcad build svg sketches exits 0", rc == 0)

        part_svg = os.path.join(tmp, "dist", "parts", "block.svg")
        asm_svg = os.path.join(tmp, "dist", "widget.svg")
        sk_svg = os.path.join(tmp, "dist", "sketches", "block.svg")
        c("part svg exists", os.path.exists(part_svg))
        c("assembly svg exists", os.path.exists(asm_svg))
        c("defining sketch svg exists", os.path.exists(sk_svg))

        if os.path.exists(part_svg):
            ptxt, proot = _parse_svg(part_svg)
            c("part svg is A4 landscape",
              proot.attrib.get("width") == "297mm"
              and proot.attrib.get("height") == "210mm"
              and proot.attrib.get("viewBox") == "0 0 297 210")
            for v in ("top", "front", "right", "iso"):
                g = _find_by_id(proot, "view-" + v)
                c("part svg has %s view with geometry" % v, _path_count(g) > 0)
            c("part svg draws hidden edges on ortho views",
              "stroke-dasharray" in ptxt)
            dims = _find_by_id(proot, "dims")
            dim_text = " ".join(dims.itertext()) if dims is not None else ""
            c("part svg dimensions length/width/height (120, 60, 20)",
              "120" in dim_text and "60" in dim_text and "20" in dim_text)
            c("part svg includes bore callout (16 and THRU)",
              "16" in dim_text and "THRU" in dim_text)
            tb = _find_by_id(proot, "title-block")
            tb_text = " ".join(tb.itertext()) if tb is not None else ""
            c("part svg title block names part, project, and size",
              "block" in tb_text and "widget" in tb_text
              and "120 x 60 x 20" in tb_text)

        if os.path.exists(asm_svg):
            atxt, aroot = _parse_svg(asm_svg)
            c("assembly svg is A4 landscape",
              aroot.attrib.get("width") == "297mm"
              and aroot.attrib.get("height") == "210mm")
            for v in ("top", "front", "right", "iso"):
                g = _find_by_id(aroot, "view-" + v)
                c("assembly svg has %s view with geometry" % v, _path_count(g) > 0)
            dims = _find_by_id(aroot, "dims")
            dim_text = " ".join(dims.itertext()) if dims is not None else ""
            c("assembly svg dimensions overall X/Y/Z (120, 60, 30)",
              "120" in dim_text and "60" in dim_text and "30" in dim_text)
            bom = _find_by_id(aroot, "bom-table")
            bom_text = " ".join(bom.itertext()) if bom is not None else ""
            c("assembly svg carries BOM table with both parts",
              "block" in bom_text and "cap" in bom_text)
            tb = _find_by_id(aroot, "title-block")
            tb_text = " ".join(tb.itertext()) if tb is not None else ""
            c("assembly svg title block names assembly, project, and size",
              "assembly" in tb_text and "widget" in tb_text
              and "120 x 60 x 30" in tb_text)

        if os.path.exists(sk_svg):
            _, sroot = _parse_svg(sk_svg)
            c("defining sketch svg remains bare 1:1 (no title block)",
              _find_by_id(sroot, "title-block") is None)

    return c.report()


testing.main(main, __file__)
