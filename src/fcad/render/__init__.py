"""plain-python rendering (numpy/numpy-stl/matplotlib): the [render] extra.

import-free at package level so installing fcad without the extra never trips
over a missing numpy/matplotlib until render/animate is actually called. the
animation vocabulary lives here for that reason: the cli has to name these
choices in `--help`, and must not drag matplotlib in to do it.

an animation is a camera crossed with a subject, and the camera means the same
thing to both animators. the subjects differ because the models do: a built stl
can sit still or assemble itself from its parts, a solved FEM result can flex,
hold its deflected shape or ring in a mode.

the three cameras are one motion at decreasing amplitude, not three behaviours:
`orbit` turns and sweeps its elevation, `turntable` turns level, `fixed` does
neither.
"""

CAMERAS = ("orbit", "turntable", "fixed")
SUBJECTS = ("all", "flex", "static", "modes")
MESH_SUBJECTS = ("static", "assemble")
ORDERS = ("grounded", "bottom-up", "top-down", "declared")
