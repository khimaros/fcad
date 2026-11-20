# -*- mode: python -*- vim: ft=python
"""end-to-end geometry check for `cantilever.fcad`."""

from fcad import testing


def main():
    c = testing.Checks()
    mod = testing.load(testing.find())
    p = mod.PARAMS
    parts = testing.specs(mod)
    beam = testing.solids(mod, parts["beam"])[0]
    bb = beam.BoundBox

    c("beam length matches PARAMS (%.2f)" % bb.XLength,
      testing.near(bb.XLength, p["length"], 1e-4))
    c("beam width matches PARAMS (%.2f)" % bb.YLength,
      testing.near(bb.YLength, p["width"], 1e-4))
    c("beam height matches PARAMS (%.2f)" % bb.ZLength,
      testing.near(bb.ZLength, p["height"], 1e-4))
    c("beam is centered on the origin",
      testing.near(bb.Center.x, 0.0, 1e-4)
      and testing.near(bb.Center.y, 0.0, 1e-4)
      and testing.near(bb.Center.z, 0.0, 1e-4))

    # verify reactive FEM load magnitude reflects live parameter overrides.
    load_fn = mod.FEM["__default__"]["loads"][0]["magnitude"]
    c("FEM load magnitude reacts to tip_load changes",
      testing.near(load_fn({"tip_load": 1000.0}), 1000.0, 1e-6))

    return c.report()


testing.main(main, __file__)
