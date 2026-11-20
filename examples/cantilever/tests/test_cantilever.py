# -*- mode: python -*- vim: ft=python
"""end-to-end geometry and closed-form FEM checks for `cantilever.fcad`."""

from fcad import testing

E_STEEL_MPA = 210000.0


def main():
    c = testing.Checks()
    mod = testing.load(testing.find())
    p = mod.PARAMS
    parts = testing.specs(mod)
    beam = testing.solids(mod, parts["beam"])[0]
    bb = beam.BoundBox

    l, w, h, f_n = p["length"], p["width"], p["height"], p["tip_load"]
    c("beam length matches PARAMS (%.2f)" % bb.XLength,
      testing.near(bb.XLength, l, 1e-4))
    c("beam width matches PARAMS (%.2f)" % bb.YLength,
      testing.near(bb.YLength, w, 1e-4))
    c("beam height matches PARAMS (%.2f)" % bb.ZLength,
      testing.near(bb.ZLength, h, 1e-4))
    c("beam is centered on the origin",
      testing.near(bb.Center.x, 0.0, 1e-4)
      and testing.near(bb.Center.y, 0.0, 1e-4)
      and testing.near(bb.Center.z, 0.0, 1e-4))

    # closed-form Euler-Bernoulli cantilever deflection & root bending stress.
    inertia = w * (h ** 3) / 12.0
    want_disp = f_n * (l ** 3) / (3.0 * E_STEEL_MPA * inertia)
    want_sigma = (f_n * l) * (h / 2.0) / inertia
    res = testing.fem_solve("assembly")
    disp_err = abs(res.max_disp - want_disp) / want_disp
    c("FEM tip deflection matches F*L^3/(3*E*I) (%.4f vs %.4f mm, err=%.2f%%)"
      % (res.max_disp, want_disp, 100.0 * disp_err), disp_err < 0.03)
    c("FEM root stress (p99=%.1f MPa) agrees with M*c/I (%.1f MPa)"
      % (res.vm_p99, want_sigma),
      0.75 * want_sigma <= res.vm_p99 <= 1.25 * want_sigma)

    # verify reactive FEM load doubles deflection when tip_load doubles.
    res2 = testing.fem_solve("assembly", values={"tip_load": 2.0 * f_n})
    ratio = res2.max_disp / res.max_disp if res.max_disp else 0.0
    c("doubling tip_load doubles FEM deflection (%.3fx)" % ratio,
      testing.near(ratio, 2.0, 0.02))

    return c.report()


testing.main(main, __file__)
