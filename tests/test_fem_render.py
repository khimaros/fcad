"""end-to-end test for what the FEM renderers actually draw.

R6.4. the two defects here are the same one the percentiles already fixed for the
reported *number*, still present in the *picture*: scaling the colormap to the
nodal maximum puts the whole model in the bottom of the scale, because that
maximum sits on a singularity and can be orders of magnitude above the field
anyone wants to see. a planter assembly peaking at 3.5 MPa against a p95 of 0.50
rendered as a uniform slab, and a reseed that moved a peak 350x would have moved
the picture from readable to flat without a byte of the design changing. so the
scale is clamped to p99 and the plot says so.

the other is the camera. a loaded structure deflects where it is supported, and
the supports are underneath - the still renderer could be pointed there and the
animator could not, honouring neither FCAD_ELEV nor FCAD_AZIM.

drives the real matplotlib path over a synthetic bundle, since what is under test
is the mapping from bundle to figure, not the solver. needs FreeCAD's bundled
numpy + matplotlib: run with `freecadcmd tests/test_fem_render.py`. results go to
$RESULT_FILE, matching the other tests.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np

from fcad.render import CAMERAS, SUBJECTS, fem_render

# a single triangle carrying one singular node: the field is 1 MPa everywhere
# except one corner at 1000, which is exactly the shape a clamped face produces.
FIELD = np.array([1.0, 1.0, 1000.0], np.float32)
P99 = 2.0
PEAK = 1000.0


def _bundle(**over):
    n = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], np.float32)
    data = {"nodes": n, "tris": np.array([[0, 1, 2]], np.int32),
            "von_mises": FIELD, "von_mises_p95": np.float32(1.0),
            "von_mises_p99": np.float32(P99),
            "disp": np.zeros((3, 3), np.float32),
            "disp_mag": np.zeros(3, np.float32),
            "bbox_diag": np.float32(14.1), "n_modes": 0}
    data.update(over)
    return data


def _scale_checks():
    """the colour scale clamps off the singularity, and says that it did."""
    plain = _bundle()
    vmax, note = fem_render.stress_scale(plain, FIELD)
    keep = os.environ.pop(fem_render.VMAX_ENV, None)
    try:
        no_p99 = {k: v for k, v in plain.items() if k != "von_mises_p99"}
        fallback, fnote = fem_render.stress_scale(no_p99, FIELD)
        os.environ[fem_render.VMAX_ENV] = "7.5"
        over, onote = fem_render.stress_scale(plain, FIELD)
    finally:
        os.environ.pop(fem_render.VMAX_ENV, None)
        if keep is not None:
            os.environ[fem_render.VMAX_ENV] = keep
    return [
        ("the scale clamps to p99, not the singular peak", vmax == P99),
        ("the plot says it clamped, and still reports the true max",
         "clamp" in note and "1e+03" in note),
        ("a bundle without p99 falls back to the peak", fallback == PEAK),
        ("and then claims no clamp", "clamp" not in fnote),
        ("%s overrides the clamp" % fem_render.VMAX_ENV, over == 7.5),
        ("and is named in the note", fem_render.VMAX_ENV in onote),
    ]


def _degenerate_checks():
    """a clamp that would hide everything, or nothing, is not applied."""
    # a field with no tail: its peak is already below the bundle's p99, so
    # clamping would only crush a plot that was perfectly readable.
    flat = np.array([1.0, 1.0, 1.0], np.float32)
    same, snote = fem_render.stress_scale(_bundle(), flat)
    zero, _ = fem_render.stress_scale(
        _bundle(von_mises_p99=np.float32(0.0)), FIELD)
    return [
        ("a p99 at or above the peak is not a clamp", same == 1.0),
        ("and is not described as one", "clamp" not in snote),
        ("a zero p99 is ignored rather than blanking the plot", zero == PEAK),
    ]


def _figure_checks(root):
    """the drawn figure carries the clamp and the requested viewpoint."""
    import matplotlib.pyplot as plt
    data = _bundle()
    fig = plt.figure()
    ax, _, norm = fem_render.field_axes(fig, data["nodes"], data["tris"],
                                        FIELD, vmax=P99)
    ax.view_init(elev=-32.0, azim=10.0)
    drawn = (float(ax.elev), float(ax.azim))
    plt.close(fig)

    np.savez_compressed(os.path.join(root, "bar.fem.npz"), **data)
    fem_render.render_static("bar", name="bar", dist=root)
    png = os.path.join(root, "fem_bar.png")
    return [
        ("the collection is normalized to the clamp", float(norm.vmax) == P99),
        ("a below-horizon viewpoint is honoured", drawn == (-32.0, 10.0)),
        ("render_static writes its png", os.path.exists(png)
         and os.path.getsize(png) > 1000),
    ]


def _camera_checks():
    """R4.3/R6.4: one camera, shared, and a fixed view is an orbit that holds.

    FCAD_ELEV used to mean a fixed elevation to the still renderers and the
    centre of a sweep to animate.py, and fem-animate honoured neither - so the
    one view that shows a load path, from underneath where the supports are, was
    reachable for a still and not for a clip."""
    import matplotlib.pyplot as plt
    from fcad.render import render

    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    render.aim(ax)
    still = (round(float(ax.elev), 6), round(float(ax.azim), 6))
    poses = []
    for frac in (0.0, 0.25, 0.5, 0.75):
        render.aim(ax, frac)
        poses.append((round(float(ax.elev), 6), round(float(ax.azim), 6)))
    render.aim(ax, 1.0)
    wrapped = (round(float(ax.elev), 6), round(float(ax.azim) % 360.0, 6))
    plt.close(fig)
    start = (round(poses[0][0], 6), round(poses[0][1] % 360.0, 6))

    keep = render.TILT
    try:
        render.TILT = 0.0
        fig = plt.figure()
        ax = fig.add_subplot(111, projection="3d")
        elevs = []
        for frac in (0.0, 0.3, 0.7):
            render.aim(ax, frac)
            elevs.append(round(float(ax.elev), 6))
        plt.close(fig)
    finally:
        render.TILT = keep

    # the three cameras over the same frame: how much of the loop each uses.
    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    by_camera = {}
    for cam in CAMERAS:
        render.aim(ax, 0.3, cam)
        by_camera[cam] = (round(float(ax.elev), 6), round(float(ax.azim), 6))
    plt.close(fig)

    return [
        ("a still uses the shared FCAD_ELEV/FCAD_AZIM",
         still == (render.ELEV, render.AZIM)),
        ("an orbit starts from that same pose", poses[0] == still),
        ("an orbit turns a full circle", {p[1] for p in poses} ==
         {render.AZIM, render.AZIM + 90.0, render.AZIM + 180.0,
          render.AZIM + 270.0}),
        ("and closes on itself, so the loop has no seam", wrapped == start),
        ("its elevation sweeps above and below the centre",
         poses[1][0] > render.ELEV and poses[3][0] < render.ELEV),
        ("FCAD_TILT=0 collapses the orbit's elevation to the fixed one",
         elevs == [render.ELEV] * 3),
        ("a turntable turns without rising or diving",
         by_camera["turntable"] == (render.ELEV, render.AZIM + 108.0)),
        ("an orbit turns by the same amount but does rise",
         by_camera["orbit"][1] == by_camera["turntable"][1]
         and by_camera["orbit"][0] != render.ELEV),
        ("a fixed camera does neither",
         by_camera["fixed"] == (render.ELEV, render.AZIM)),
    ]


def _subject_checks(root):
    """R6.4: the subject axis, including the one clip an orbit is really for."""
    from fcad.render import fem_animate
    data = _bundle(disp=np.array([[0, 0, 0], [0, 0, 1], [0, 0, 2]], np.float32),
                   disp_mag=np.array([0.0, 1.0, 2.0], np.float32))
    np.savez_compressed(os.path.join(root, "bar.fem.npz"), **data)
    made = {}
    for subject in ("flex", "static"):
        stem = os.path.join(root, subject)
        fem_animate.animate("bar", stem, name="bar", dist=root,
                            camera="orbit", subject=subject)
        made[subject] = os.path.exists(stem + ".mp4") and os.path.exists(stem + ".gif")
    modes_only = None
    try:
        fem_animate.animate("bar", os.path.join(root, "m"), name="bar",
                            dist=root, subject="modes")
    except SystemExit as e:
        modes_only = str(e)
    return [
        ("subject=flex writes its clip", made["flex"]),
        ("subject=static writes its clip", made["static"]),
        ("a subject with nothing to show fails loudly, not silently",
         modes_only is not None and "no clips" in modes_only),
        ("and says how to get the modes it wanted",
         modes_only is not None and "--modal" in modes_only),
        ("the cli can name the vocabulary without importing matplotlib",
         set(SUBJECTS) >= {"all", "flex", "static", "modes"}
         and set(CAMERAS) == {"orbit", "turntable", "fixed"}),
    ]


def main():
    root = tempfile.mkdtemp()
    try:
        checks = (_scale_checks() + _degenerate_checks() + _figure_checks(root)
                  + _camera_checks() + _subject_checks(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)
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


if any(a.endswith("test_fem_render.py") for a in sys.argv):
    raise SystemExit(main())
