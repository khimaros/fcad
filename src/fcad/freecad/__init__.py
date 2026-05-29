"""modules that import FreeCAD: only ever imported inside freecadcmd/freecad.

kept import-free at package level so `fcad.freecad._entry` can be located from
the cli's plain python without dragging in FreeCAD.
"""
