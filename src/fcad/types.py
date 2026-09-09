"""TypeId names, checked against the FreeCAD that is actually installed.

FreeCAD builds document objects from a TypeId string - `doc.addObject(
"PartDesign::Pad", name)` - and for the workbenches fcad drives (PartDesign,
Sketcher, Assembly) there is no factory function to call instead: only
ObjectsFem, Draft and Arch ship makers. so the string is FreeCAD's interface
rather than fcad's invention, and it is what every wiki page and forum answer
writes.

what the bare string lacks is a check. `"PartDesign::Poket"` is accepted by
python, by the editor and by fcad, and fails much later inside a recompute with
a message that never mentions the typo::

    from fcad import types

    body.newObject(types.PartDesign.Pad, "pad")        # "PartDesign::Pad"
    body.newObject(types.PartDesign.Poket, "groove")   # AttributeError, here

**these names are not a list fcad maintains.** each is resolved against the
running FreeCAD's own registry, so it cannot go stale against your build, covers
workbenches fcad has never heard of, and offers nothing your FreeCAD cannot
make. the value *is* the string, so `types.PartDesign.Pad` and
`"PartDesign::Pad"` are interchangeable - cross-referencing the FreeCAD docs
still works.

outside FreeCAD (the cli imports this package) nothing can be checked, so names
resolve unvalidated rather than failing.
"""

import difflib

# `doc.supportedTypes()` reports only the types whose module is *loaded* - a
# fresh document knows 38 of them and no PartDesign at all - so a namespace is
# imported before its types are looked for. cached per namespace: the answer is
# a property of the installed build, not of any document.
_KNOWN = set()
_LOADED = set()
SUGGEST = 3          # how many "did you mean" candidates are worth printing


def _supported(app):
    """every TypeId this FreeCAD currently registers.

    read off an open document when there is one, so no document is created (and
    no active document disturbed) just to ask a question about the build."""
    open_docs = list(app.listDocuments())
    if open_docs:
        return set(app.getDocument(open_docs[0]).supportedTypes())
    doc = app.newDocument("fcad_types_probe", hidden=True)
    try:
        return set(doc.supportedTypes())
    finally:
        app.closeDocument(doc.Name)


def known(namespace):
    """the registered TypeIds, having loaded `namespace`; None outside FreeCAD."""
    try:
        import FreeCAD as app
    except ImportError:
        return None
    if namespace not in _LOADED:
        try:
            __import__(namespace)
        except ImportError:
            pass          # not every namespace is an importable module (App)
        _KNOWN.update(_supported(app))
        _LOADED.add(namespace)
    return _KNOWN


class Namespace:
    """one TypeId namespace: `types.PartDesign.Pad` -> `"PartDesign::Pad"`."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        type_id = "%s::%s" % (self._name, attr)
        registry = known(self._name)
        if registry is not None and type_id not in registry:
            close = difflib.get_close_matches(type_id, sorted(registry), SUGGEST)
            raise AttributeError(
                "%s is not a type this FreeCAD registers%s"
                % (type_id, "; did you mean %s?" % " / ".join(close) if close
                   else ""))
        return type_id

    def __dir__(self):
        registry = known(self._name) or ()
        prefix = self._name + "::"
        return sorted(t[len(prefix):] for t in registry if t.startswith(prefix))

    def __repr__(self):
        return "<fcad.types.%s>" % self._name


def __getattr__(name):
    if name.startswith("_"):
        raise AttributeError(name)
    return Namespace(name)
