"""fcad: reusable FreeCAD build + instrumentation for parametric models.

a project depends on fcad and describes itself with a single `Project`
descriptor (conventionally `PROJECT` in its `project.py`); the `fcad` cli then
builds, exports, validates and inspects it. fcad carries zero model knowledge.

only the lightweight, dependency-free pieces are exported here. the FreeCAD-bound
code lives in `fcad.freecad` (imported only inside freecadcmd/freecad) and the
matplotlib renderer in `fcad.render` (the `[render]` extra).
"""

from fcad.project import Project, Part

__all__ = ["Project", "Part"]
__version__ = "0.1.0"
