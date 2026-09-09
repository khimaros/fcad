"""fcad: reusable FreeCAD build + instrumentation for parametric models.

a project depends on fcad and describes itself with a single `Project`
descriptor (conventionally `PROJECT` in its `project.py`); the `fcad` cli then
builds, exports, validates and inspects it. fcad carries zero model knowledge.

only the lightweight, dependency-free pieces are exported here. the FreeCAD-bound
code lives in `fcad.freecad` (imported only inside freecadcmd/freecad) and the
matplotlib renderer in `fcad.render` (the `[render]` extra).
"""

# `Part` is the name PartSpec shipped under, kept working for existing projects.
from fcad.project import Part, PartSpec, Project

__all__ = ["Part", "PartSpec", "Project"]
__version__ = "0.1.0"
