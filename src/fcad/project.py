"""the contract a project hands to fcad.

fcad knows how to drive FreeCAD to build, export and inspect a parametric
model, but nothing about any particular model. a project describes itself with a
defaults dict and a `compute` function; everything else fcad needs is inferred
or carried on the parts `compute` returns, so the instrumentation holds zero
project knowledge.

two equivalent ways to declare a project (see `fcad.loader`):
  - the minimal, convention way: module-level `PARAMS` + `compute` (returning a
    list of `fcad.PartSpec`), with optional `PARAM_META`/`FEM`/`MATERIAL`/...; fcad
    assembles the `Project` for you. a whole project can be one file.
  - the explicit way: build a `Project(...)` yourself and expose it as `PROJECT`.

a *spec* (each item `compute` returns) is duck-typed; fcad reads these
attributes: name, placements, qty, length, profile (a bom label), holes,
grounded, embeds, and either a `solid()` method (and optional `profile2d`) or is
realized by a project-supplied `from_spec`/`profile`. `fcad.PartSpec` is the ready
made spec; a project may use its own type as long as it exposes the same surface.
"""

import os

from fcad import config
from fcad.cutlist import lengths_for, sheets_for

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


def normalize(data):
    """a `compute` return, in the one shape the rest of fcad reads.

    a project may return the bare list of specs (the common case) or a dict
    carrying derived values alongside its "specs"; everything downstream --
    including a project's own tests, via `fcad.testing` -- has to accept both."""
    return data if isinstance(data, dict) else {"specs": list(data)}


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


class PartSpec:
    """a distinct part type: where it is placed and how to realize it.

    named for what it is. one of these is not a part - it carries every
    placement of one and derives a `qty`, so a single spec is eighteen deck
    boards - and the rest of fcad has always called them specs (`compute`
    returns `"specs"`, `from_spec`, `testing.specs`). it also stops the name
    colliding with FreeCAD's, which uses `Part` for the geometry kernel module
    and `App::Part` for a container object; this is neither.

    the ready-made unit of the project<->fcad contract. fcad reads name and
    placements, builds the solid lazily via `solid` (a thunk, so metadata-only
    passes like bom and interference needn't pay for the booleans) and, when given,
    the defining 2d sketch from `profile2d`. qty and length are derived. flags:
    `grounded` anchors the assembly; `embeds` excludes a part that intentionally
    sinks into others (e.g. screws) from the interference check and fem fuse;
    `disjoint` says the part is several separate pieces on purpose, which
    `check` otherwise reports as a part severed by its own joinery; `rides`
    names the part this one stays on when `render --explode` lifts them apart.

    **the well-lit path builds a PartDesign body.** `build` gets a live document
    and an empty `PartDesign::Body` and writes real FreeCAD code into it, so the
    part *is* its feature tree: the sketch, the solid and the drawing are one
    artifact rather than three that only agree because `check` says so.

    `build` is deliberately not a feature vocabulary. fcad has no `Pad` class and
    no `Feature` type to learn, because anything it defined would be a smaller,
    lossier copy of FreeCAD's own - no arcs, no attachment, no fillets. you write
    what the FreeCAD docs say, against the real objects::

        from fcad import types
        from fcad.freecad import partdesign

        def post(doc, body):
            sk = partdesign.add_sketch(doc, body, "outline", points=pts, z=-t/2)
            pad = body.newObject(types.PartDesign.Pad, "pad")
            pad.Profile = sk
            pad.Length = t
            doc.recompute()

        fcad.PartSpec("post", placements=[...], build=post,
                      dimension_sketches=["outline"])

    `partdesign.pad_and_bore` is the shape most parts are (an outline padded,
    with through bores) and is a helper to call from `build`, not a second way in.

    `solid` remains for geometry that is not a body at all - imported, or built
    with plain `Part` booleans.

    nothing here describes the geometry twice. the bores are features in the
    tree, so `dimension_sketches` names the sketches whose circles belong on the
    drawing rather than restating their coordinates, and a part with no body says
    `dimension_circles` instead."""

    def __init__(self, name, placements=None, build=None, solid=None, profile="",
                 length=None, grounded=False, embeds=False,
                 dimension_sketches=(), dimension_circles=(), openings=(),
                 profile2d=None, color=None, transparency=0, disjoint=False,
                 rides=None, material=None, sheet_size=None, flat_pattern=None,
                 cots=False, sku="", unit_cost=None, mass_g=None,
                 bom_group=""):
        if build is not None and solid is not None:
            raise ValueError("PartSpec %r: pass either `build` or `solid`, "
                             "not both" % name)
        if not (0 <= transparency <= 100):
            raise ValueError("PartSpec %r: transparency must be in 0..100, "
                             "got %r" % (name, transparency))
        if color is not None:
            col = tuple(color)
            if len(col) != 3 or not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    and 0.0 <= float(v) <= 1.0 for v in col):
                raise ValueError("PartSpec %r: color must be an (r, g, b) "
                                 "triple of 0..1 floats, got %r" % (name, color))
            color = tuple(float(v) for v in col)
        if placements is None:
            try:
                from FreeCAD import Placement
                placements = [Placement()]
            except ImportError:
                placements = []
        elif not isinstance(placements, (list, tuple)) \
                and not hasattr(placements, "__iter__"):
            placements = [placements]
        self.name = name
        self.disjoint = disjoint
        # optional per-part FEM material name or {E, nu, rho} dict.
        self.material = material
        # optional 2D sheet blank size `(w, h)` and sheet-metal `FlatPattern`.
        self._sheet_size = tuple(sheet_size) if sheet_size is not None else None
        self.flat_pattern = flat_pattern
        # off-the-shelf / vendor STEP metadata and handed-part BOM grouping.
        self.cots = bool(cots)
        self.sku = str(sku or "")
        self.unit_cost = float(unit_cost) if unit_cost is not None else None
        self._mass_g = mass_g
        self.bom_group = str(bom_group or "")
        # the part this one stays on when the model is taken apart: a chip on
        # its board, an insert pressed into its boss. the two are one piece to
        # whoever assembles it, and no geometry says so.
        self.rides = rides
        # how `fcad render` draws the part: an (r, g, b) of 0..1 floats, and how
        # see-through it is from 0 (solid) to 100. a look, not a material - it
        # is what lets a crystal show the display under it.
        self.color = color
        self.transparency = transparency
        self.placements = list(placements)
        self._solid = solid           # () -> Part.Shape  (or a Part.Shape)
        self.profile = profile        # bom label, e.g. nominal lumber name
        self._length = length         # explicit bom length; else the bbox x-extent
        self.grounded = grounded
        self.embeds = embeds
        # (doc, body) -> None: real FreeCAD code, called with a live document
        # when geometry is actually wanted.
        self._build = build
        # the drawing, not the geometry. a part built from a tree names the
        # sketches whose circles get dimensioned (fcad reads them back, so they
        # cannot disagree with what was cut); a part that hands over a solid has
        # no sketches to read and lists the circles itself.
        self.dimension_sketches = list(dimension_sketches)
        self.dimension_circles = list(dimension_circles)
        # which cuts are meant to stay empty. the void check asks whether a
        # part gave up material nothing fills, and that is the right question
        # for joinery and the wrong one for a nut's bore or a drainage hole -
        # a distinction no geometry carries, so the project states it. sketch
        # names for a part with a tree; `(cx, cy, dia)` circles for one that
        # hands over a solid and has no sketches to name.
        self.openings = list(openings)
        # the stock a part is cut from, for a part fcad cannot derive a blank
        # for (one supplying `solid`). a declared part's blank comes from
        # suppressing its own subtractive features and needs none of this.
        self.profile2d = profile2d

    @classmethod
    def from_step(cls, name, step_path, placements, profile="", sku="",
                  unit_cost=None, mass_g=None, length=None, grounded=False,
                  embeds=False, color=None, transparency=0, rides=None,
                  material=None, bom_group=""):
        """an off-the-shelf part loaded lazily from a vendor STEP file."""
        path = os.path.abspath(step_path)

        def _load():
            if not os.path.exists(path):
                raise FileNotFoundError("STEP file not found: %s" % path)
            import Part
            s = Part.Shape()
            s.read(path)
            return s

        return cls(name, placements=placements, solid=_load, profile=profile,
                   length=length, grounded=grounded, embeds=embeds,
                   color=color, transparency=transparency, rides=rides,
                   material=material, cots=True, sku=sku, unit_cost=unit_cost,
                   mass_g=mass_g, bom_group=bom_group)

    @property
    def mass_g(self):
        """explicit mass in grams (float or lazy thunk), or None."""
        val = self._mass_g() if callable(self._mass_g) else self._mass_g
        return float(val) if val is not None else None

    def with_placements(self, placements):
        """shallow copy of this spec carrying a new placements list."""
        import copy
        clone = copy.copy(self)
        clone.placements = list(placements)
        return clone

    @property
    def qty(self):
        return len(self.placements)

    @property
    def declared(self):
        """true when the part is a PartDesign body fcad can build."""
        return self._solid is None and self._build is not None

    def build_into(self, doc, body):
        """write this part's geometry into `body`, using real FreeCAD calls."""
        return self._build(doc, body)

    def solid(self):
        """the BREP solid in the part's natural (unplaced) frame.

        a declared part is built by FreeCAD from its own feature tree and the
        shape read back, so there is exactly one description of it and no second
        implementation to drift from. the import is local because `fcad.project`
        is pure stdlib (the cli imports it) and the geometry only runs under
        FreeCAD."""
        if self.declared:
            from fcad.freecad.partdesign import shape_of
            return shape_of(self)
        return self._solid() if callable(self._solid) else self._solid

    @property
    def sheet_dims(self):
        """2D blank `(w, h)` for sheet nesting: explicit, else from flat_pattern."""
        if self._sheet_size is not None:
            return (float(self._sheet_size[0]), float(self._sheet_size[1]))
        fp = self.flat_pattern() if callable(self.flat_pattern) else self.flat_pattern
        if fp is not None:
            return (float(fp.width), float(fp.height))
        return None

    @property
    def length(self):
        """bom length: the explicit value, else the solid's x-extent.

        state it whenever the model is swept: `optimize` asks hundreds of
        candidates for their lengths, and the fallback builds geometry to read a
        bounding box."""
        if self._length is not None:
            return self._length
        s = self.solid()
        return s.BoundBox.XLength if s is not None else 0.0


# the name this was shipped under. kept so an existing project keeps working;
# `PartSpec` is what it is, and what the docs and examples use.
Part = PartSpec


class SubAssembly:
    """a reusable multi-part module placed at one or more assembly transforms.

    placing a sub-assembly multiplies each child part's local placement by the
    module's placement and merges repeated child names so the BOM and part files
    stay deduplicated."""

    def __init__(self, name, specs):
        self.name = name
        self.specs = list(specs)

    def place(self, placements):
        """realize this sub-assembly at every transform in `placements`."""
        pls = list(placements)
        out = []
        for spec in self.specs:
            combined = [pl.multiply(child_pl)
                        for pl in pls for child_pl in spec.placements]
            out.append(spec.with_placements(combined))
        return out


def compose(*items):
    """flatten PartSpecs, lists, and placed SubAssemblies, merging shared names."""
    by_name, order = {}, []

    def add(item):
        if isinstance(item, SubAssembly):
            for s in item.specs:
                add(s)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                add(sub)
        else:
            if item.name in by_name:
                prev = by_name[item.name]
                by_name[item.name] = prev.with_placements(
                    prev.placements + list(item.placements))
            else:
                order.append(item.name)
                by_name[item.name] = item

    for it in items:
        add(it)
    return [by_name[n] for n in order]


class Project:
    """adapts a project's declarations to the surface the instrumentation reads.

    every field but `name` has a sane default or is inferred, so the minimal
    project is a defaults dict plus a `compute`. the geometry-producing callables
    are the only required project code; the rest (schema, enum choices, per-spec
    realization) fcad derives or reads off the parts themselves."""

    def __init__(self, name, root=None, params=None, schema=None, compute=None,
                 from_spec=None, profile=None, enum_choices=None, fem=None,
                 param_meta=None, material=None, materials=None, stock=None,
                 constraints=None, assemble=None, motion=None, dist=None):
        self.name = name              # output stem: <name>.FCStd, <name>-bom.csv
        self.root = root or os.getcwd()   # project root; dist/ is created under it
        self._dist = dist             # explicit output dir; else env, else root/dist
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
        # optional (doc, asm, links) -> None: real Assembly-workbench code,
        # called once the links exist and the anchors are grounded. a project
        # that declares it owns the jointing, so a hinge can be a Revolute
        # instead of the Fixed joint computed placements imply.
        self.assemble = assemble
        # optional kinematic motion sweeps (`[{"name": ..., "keyframes": [...]}]`)
        # checked dynamically by `fcad check` and animated by `--subject motion`.
        self.motion = motion

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
        """where the build writes: explicit, else `FCAD_DIST`, else `<root>/dist`.

        the same precedence `config.resolve` uses, and it has to be read here
        rather than recomputed. the cli resolves `--dist`/`FCAD_DIST` and exports
        the answer to the child, but this property used to derive `<root>/dist`
        from scratch - so an out-of-tree build reported the requested directory,
        left it empty, and wrote over the project's own `dist/` instead. exit 0,
        no warning, tracked files gone."""
        if self._dist:
            return self._dist
        return os.environ.get(config.ENV_DIST) or os.path.join(self.root, "dist")

    def resolve_stock(self, values=None):
        """stock table for `values`, calling `self.stock(values)` when callable."""
        if callable(self.stock):
            return self.stock(self.defaults() if values is None else values)
        return self.stock

    def stock_for(self, profile, values=None):
        """stock lengths declared for a 1D bom profile; [] means not 1D stock."""
        return lengths_for(self.resolve_stock(values), profile)

    def sheets_for(self, profile, values=None):
        """2D buyable `Sheet` stock declared for a bom profile, or []."""
        return sheets_for(self.resolve_stock(values), profile)

    def defaults(self):
        return dict(self.params)

    def compute(self, values):
        """the project's specs, normalized to {"specs": [...], ...}.

        a project may return the bare list of specs (the common case) or a dict
        carrying extra derived values alongside its "specs"."""
        return normalize(self._compute(values))

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
