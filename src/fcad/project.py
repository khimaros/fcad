"""the contract a project hands to fcad.

fcad knows how to drive FreeCAD to build, export and inspect a parametric
model, but nothing about any particular model. a project describes itself with a
defaults dict and a `compute` function; everything else fcad needs is inferred
or carried on the parts `compute` returns, so the instrumentation holds zero
project knowledge.

two equivalent ways to declare a project (see `fcad.loader`):
  - the minimal, convention way: module-level `PARAMS` + `compute` (returning a
    list of `fcad.Part`), with optional `PARAM_META`/`FEM`/`MATERIAL`/...; fcad
    assembles the `Project` for you. a whole project can be one file.
  - the explicit way: build a `Project(...)` yourself and expose it as `PROJECT`.

a *spec* (each item `compute` returns) is duck-typed; fcad reads these
attributes: name, placements, qty, length, profile (a bom label), holes,
grounded, embeds, and either a `solid()` method (and optional `profile2d`) or is
realized by a project-supplied `from_spec`/`profile`. `fcad.Part` is the ready
made spec; a project may use its own type as long as it exposes the same surface.
"""

import os

from fcad.cutlist import lengths_for

DEFAULT_GROUP = "Parameters"

# short property-type aliases a project may write in PARAM_META instead of the
# full "App::Property..." names.
_PTYPE_ALIASES = {
    "length": "App::PropertyLength", "float": "App::PropertyFloat",
    "integer": "App::PropertyInteger", "int": "App::PropertyInteger",
    "bool": "App::PropertyBool", "string": "App::PropertyString",
    "enumeration": "App::PropertyEnumeration", "enum": "App::PropertyEnumeration",
    "angle": "App::PropertyAngle",
}


def _expand_ptype(ptype):
    return _PTYPE_ALIASES.get(ptype.lower(), ptype) if "::" not in ptype else ptype


def _infer_ptype(value, has_choices):
    """the FreeCAD varset property type for a python default value.

    floats default to PropertyLength because fcad models physical geometry (mm
    dimensions dominate); a project overrides via PARAM_META for the occasional
    ratio, angle or count. a string with declared choices becomes an enumeration
    (a gui dropdown), otherwise a free string."""
    if has_choices:
        return "App::PropertyEnumeration"
    if isinstance(value, bool):       # before int: bool is an int subclass
        return "App::PropertyBool"
    if isinstance(value, int):
        return "App::PropertyInteger"
    if isinstance(value, float):
        return "App::PropertyLength"
    return "App::PropertyString"


class Part:
    """a distinct part type: where it is placed and how to realize it.

    the ready-made unit of the project<->fcad contract. fcad reads name and
    placements, builds the solid lazily via `solid` (a thunk, so metadata-only
    passes like bom and interference needn't pay for the booleans) and, when given,
    the defining 2d sketch from `profile2d`. qty and length are derived. flags:
    `grounded` anchors the assembly; `embeds` excludes a part that intentionally
    sinks into others (e.g. screws) from the interference check and fem fuse."""

    def __init__(self, name, placements, solid=None, profile2d=None, profile="",
                 holes=(), fastener_holes=(), length=None, grounded=False,
                 embeds=False):
        self.name = name
        self.placements = list(placements)
        self._solid = solid           # () -> Part.Shape  (or a Part.Shape)
        self.profile2d = profile2d    # (points, thickness) for the sketch | None
        self.profile = profile        # bom label, e.g. nominal lumber name
        self.holes = holes            # dimensioned (cx, cy, dia) circles (drainage)
        self.fastener_holes = fastener_holes  # in-plane screw circles, sketch only
        self._length = length         # explicit bom length; else the bbox x-extent
        self.grounded = grounded
        self.embeds = embeds

    @property
    def qty(self):
        return len(self.placements)

    def solid(self):
        """the BREP solid in the part's natural (unplaced) frame."""
        return self._solid() if callable(self._solid) else self._solid

    @property
    def length(self):
        """bom length: the explicit value, else the solid's x-extent."""
        if self._length is not None:
            return self._length
        s = self.solid()
        return s.BoundBox.XLength if s is not None else 0.0


class Project:
    """adapts a project's declarations to the surface the instrumentation reads.

    every field but `name` has a sane default or is inferred, so the minimal
    project is a defaults dict plus a `compute`. the geometry-producing callables
    are the only required project code; the rest (schema, enum choices, per-spec
    realization) fcad derives or reads off the parts themselves."""

    def __init__(self, name, root=None, params=None, schema=None, compute=None,
                 from_spec=None, profile=None, enum_choices=None, fem=None,
                 param_meta=None, material=None, materials=None, stock=None,
                 constraints=None):
        self.name = name              # output stem: <name>.FCStd, <name>-bom.csv
        self.root = root or os.getcwd()   # project root; dist/ is created under it
        self.params = dict(params or {})  # default parameter values
        self._schema = schema             # explicit varset rows, else inferred
        self._compute = compute           # values -> [spec] | {"specs": [spec], ...}
        self._from_spec = from_spec       # optional spec -> solid (else spec.solid())
        self._profile = profile           # optional spec -> 2d profile (else .profile2d)
        self._enum_choices = enum_choices
        self.param_meta = param_meta or {}    # name -> {type,group,min,max,choices}
        # optional FEM inputs, duck-typed (see fcad.freecad.fem): None, a callable
        # (target, shape, values) -> case, or a {target_name: case} mapping.
        self.fem = fem
        self.material = material          # project-wide default fem material
        self.materials = dict(materials or {})  # project-defined named materials
        # purchasable stock lengths per bom profile (or one list for all). which
        # profiles appear here decides which parts the cut list plans at all.
        self.stock = stock
        # predicates `(values, computed) -> bool | str` that say when the model
        # still means what it says. fcad never learns what they mean; it only
        # asks. a parametric model has sizes at which it silently stops being
        # the thing it describes -- a dimension clamped, a member vanished --
        # and both `check` and `optimize` need to be able to tell.
        self.constraints = list(constraints or ())

    def violations(self, values, computed=None):
        """which constraints this parameter set breaks, as printable strings.

        a predicate may return False, or a string naming what went wrong; the
        string is what a person reads, so it is worth writing."""
        if not self.constraints:
            return []
        data = self.compute(values) if computed is None else computed
        out = []
        for i, rule in enumerate(self.constraints):
            try:
                verdict = rule(values, data)
            except Exception as exc:               # a broken rule is a failure
                out.append("constraint %d raised %s" % (i, exc))
                continue
            if verdict is False or verdict is None:
                name = getattr(rule, "__doc__", None) or getattr(
                    rule, "__name__", "constraint %d" % i)
                out.append(str(name).strip().splitlines()[0])
            elif isinstance(verdict, str):
                out.append(verdict)
        return out

    @property
    def dist(self):
        return os.path.join(self.root, "dist")

    def stock_for(self, profile):
        """stock lengths declared for a bom profile; [] means not cut from stock."""
        return lengths_for(self.stock, profile)

    def defaults(self):
        return dict(self.params)

    def compute(self, values):
        """the project's specs, normalized to {"specs": [...], ...}.

        a project may return the bare list of specs (the common case) or a dict
        carrying extra derived values alongside its "specs"."""
        data = self._compute(values)
        return data if isinstance(data, dict) else {"specs": list(data)}

    def from_spec(self, spec):
        """the BREP solid for a spec: the project's callable, else spec.solid()."""
        return self._from_spec(spec) if self._from_spec else spec.solid()

    def profile(self, spec):
        """(2d points, thickness) | None: the project's callable, else .profile2d."""
        if self._profile:
            return self._profile(spec)
        return getattr(spec, "profile2d", None)

    @property
    def enum_choices(self):
        if self._enum_choices is not None:
            return self._enum_choices
        return {n: m["choices"] for n, m in self.param_meta.items() if "choices" in m}

    @property
    def schema(self):
        """varset rows (name, ptype, group, min, max): explicit, else inferred
        from PARAMS (type from the default value) overlaid with PARAM_META."""
        if self._schema is not None:
            return self._schema
        choices = self.enum_choices
        rows = []
        for name, value in self.params.items():
            meta = self.param_meta.get(name, {})
            ptype = meta.get("type") or _infer_ptype(value, name in choices)
            rows.append((name, _expand_ptype(ptype), meta.get("group", DEFAULT_GROUP),
                         meta.get("min"), meta.get("max")))
        return rows
