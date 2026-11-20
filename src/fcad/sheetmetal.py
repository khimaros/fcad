"""sheet-metal bend-allowance math and flat-pattern SVG export (pure stdlib).

models bent sheet parts with K-factor bend allowances so unfolded blanks feed
directly into 2D sheet nesting (`cutlist.plan_sheets`) and export cut-ready
`dist/sketches/<part>-flat.svg` drawings with dashed bend lines.
"""

from dataclasses import dataclass, field
import math

DEFAULT_K_FACTOR = 0.44


def bend_allowance(angle_deg, radius, thickness, k_factor=DEFAULT_K_FACTOR):
    """arc length along the neutral axis for one bend (mm)."""
    return math.radians(float(angle_deg)) * (
        float(radius) + float(k_factor) * float(thickness))


@dataclass
class FlatPattern:
    """unfolded 2D blank: overall bounds, outer contour, bend lines, cutouts."""
    width: float
    height: float
    outline: list
    bend_lines: list = field(default_factory=list)   # [(x1, y1, x2, y2, angle)]
    cutouts: list = field(default_factory=list)      # [closed polygon pts]

    def export_svg(self, path):
        """write cut contour (solid black) and bend lines (dashed red) to SVG."""
        w, h = self.width, self.height
        pad = 2.0

        def poly(pts):
            return " ".join("%s%.3f,%.3f" % ("M" if i == 0 else "L",
                                             x + pad, h - y + pad)
                            for i, (x, y) in enumerate(pts)) + " Z"

        cuts = [poly(self.outline)] + [poly(c) for c in self.cutouts]
        bends = [
            '<line x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f"/>'
            % (x1 + pad, h - y1 + pad, x2 + pad, h - y2 + pad)
            for x1, y1, x2, y2, *_ in self.bend_lines
        ]
        cut_els = "\n".join('<path d="%s"/>' % d for d in cuts)
        bend_els = "\n".join(bends)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="%.3fmm" height="%.3fmm" viewBox="0 0 %.3f %.3f">\n'
            '<g fill="none" stroke="black" stroke-width="0.4">\n%s\n</g>\n'
            '<g fill="none" stroke="#cc2222" stroke-width="0.35" '
            'stroke-dasharray="4,2">\n%s\n</g>\n</svg>\n'
            % (w + 2 * pad, h + 2 * pad, w + 2 * pad, h + 2 * pad,
               cut_els, bend_els))
        with open(path, "w") as f:
            f.write(svg)


def unfold_u_channel(web_w, flange_h, length, thickness, bend_r,
                     k_factor=DEFAULT_K_FACTOR):
    """unfold a 90-deg U-channel of outer web `web_w`, outer flange `flange_h`,
    and extrusion `length` into a rectangular flat blank with 2 bend lines."""
    sb = float(bend_r) + float(thickness)
    ba = bend_allowance(90.0, bend_r, thickness, k_factor)
    flat_web = float(web_w) - 2.0 * sb
    flat_flange = float(flange_h) - sb
    total_w = flat_web + 2.0 * flat_flange + 2.0 * ba
    h = float(length)
    outline = [(0.0, 0.0), (total_w, 0.0), (total_w, h), (0.0, h)]
    b1 = flat_flange + ba / 2.0
    b2 = total_w - b1
    bends = [(b1, 0.0, b1, h, 90.0), (b2, 0.0, b2, h, 90.0)]
    return FlatPattern(total_w, h, outline, bends)


def unfold_box_tray(base_w, base_d, wall_h, thickness, bend_r,
                    k_factor=DEFAULT_K_FACTOR):
    """unfold a 4-walled open sheet-metal tray with corner reliefs + 4 bends."""
    sb = float(bend_r) + float(thickness)
    ba = bend_allowance(90.0, bend_r, thickness, k_factor)
    fw = float(base_w) - 2.0 * sb
    fd = float(base_d) - 2.0 * sb
    fl = float(wall_h) - sb
    flap = fl + ba
    tw = fw + 2.0 * flap
    td = fd + 2.0 * flap
    # notched cross contour (corner tabs removed for clean 90-deg folds).
    outline = [
        (flap, 0.0), (flap + fw, 0.0),
        (flap + fw, flap), (tw, flap),
        (tw, flap + fd), (flap + fw, flap + fd),
        (flap + fw, td), (flap, td),
        (flap, flap + fd), (0.0, flap + fd),
        (0.0, flap), (flap, flap),
    ]
    bx1, bx2 = fl + ba / 2.0, tw - (fl + ba / 2.0)
    by1, by2 = fl + ba / 2.0, td - (fl + ba / 2.0)
    bends = [
        (bx1, flap, bx1, flap + fd, 90.0),
        (bx2, flap, bx2, flap + fd, 90.0),
        (flap, by1, flap + fw, by1, 90.0),
        (flap, by2, flap + fw, by2, 90.0),
    ]
    return FlatPattern(tw, td, outline, bends)
