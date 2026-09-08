---
name: freecad-python
description: workflow for scripting freecad from python, with a build-accurate api reference (TypeIds, property names, enum values) generated from the installed freecad, plus a curated wiki subset for scripting semantics
---

# freecad python scripting

<!-- installed by `fcad install-skill`, which rewrites this file, `api/` and
     the 52 pages it ships under `wiki/`. edits here are lost on the next
     install: send them upstream to fcad's
     resources/skills/freecad-python/SKILL.md instead. -->

knowledge sources, in order of preference:

1. **the generated api reference at `api/`** - introspected from the
   *installed build*, so it cannot be stale. exact TypeIds, property
   names, types, defaults and enum values. this answers "what is this
   property called, and what may it hold". `api/index.md` records which
   freecad produced it.
2. **the wiki subset at `wiki/`** - 52 pages of scripting semantics and
   worked examples. this answers "what does this *mean*, and how is it
   normally done", which introspection cannot.
3. **the live python interpreter** - for anything neither file answers.
   always available and always authoritative.

they divide cleanly, so reach for the right one first: `api/` for
names, `wiki/` for meaning. when they disagree, `api/` and the
interpreter win - the wiki is a snapshot, the build is now.

do not fall back on training-data memory of the freecad api. it's
stale and wrong often enough that guessing costs more turns than
looking up.

## the generated api reference

```
api/index.md        which freecad build this describes, and what loaded
api/typeids.md      every TypeId doc.addObject() accepts, grouped by module
api/factories.md    each workbench's make* functions, with call signatures
api/fem.md          every ObjectsFem maker + its full property table
api/properties.md   property tables for commonly instantiated TypeIds
```

every name in there was read off a real object via `PropertiesList` /
`getTypeIdOfProperty` / `getEnumerationsOfProperty`. nothing is
transcribed, so it is exactly as correct as the build it ran against.

refresh it after a freecad upgrade - this reinstalls the skill and
regenerates `api/`, and is a no-op when the build has not changed:

```
fcad install-skill
```

`fcad api-docs [DIR]` writes the same reference anywhere else you want
it, without touching the skill.

## the one rule: look it up or introspect, don't guess

**before you write any line that names an api, you must have just seen
that name in `api/`, in wiki text, or in interpreter output in this
session.**

signs you are guessing:
- trying synonyms after an error (`Support`, `support`, `AttachmentSupport`).
  **stop and look it up.**
- retrying with a different string literal after an enum rejection.
  **stop and call `getEnumerationsOfProperty`.**
- writing code that mixes a fresh introspection call with its use.
  split them - observe first, then write against observed values.

cost calculus: one grep or one introspection call is one tool turn.
guessing wrong and error-chasing is 3-5 turns and often a corrupted
document.

## two root modules

- `FreeCAD` (alias `App`) - document model, geometry.
- `FreeCADGui` (alias `Gui`) - views, selection, toolbars, gui commands.

`App` imports everywhere; `Gui` only in a gui session (`App.GuiUp`
tells you). every document object has a paired `ViewObject`
(`obj.ViewObject`) holding the gui properties - the `App` side is the
source of truth and the gui side is regenerated on recompute.

## universal object lifecycle

```python
doc = App.newDocument()                  # or App.ActiveDocument
obj = doc.addObject(TypeId, "Name")      # e.g. "PartDesign::Pad"
# configure properties on obj ...
doc.recompute()                          # now it renders / solves
```

inside a `PartDesign::Body`, use `body.newObject(TypeId, name)` so the
new feature joins the body's tip group. `App.Vector(x, y, z)` is the
standard 3d point. every object has a `Placement`.

some workbenches want a **factory** rather than a bare `addObject`: the
factory attaches the python feature class behind the object, and the
raw TypeId alone gives you an inert shell. `ObjectsFem.make*` is the
main case - `api/factories.md` lists them with signatures, `api/fem.md`
gives each one's properties.

## build with the App-side api, not gui commands

`Gui.runCommand("Sketcher_CreateHexagon")` waits for mouse clicks;
`PartDesign_Pad` pops a modal dialog. neither is scriptable. the
scripted path is always `addObject` / `newObject` + property assignment
+ `recompute`.

what gui commands *are* useful for:
- enumerating capabilities: `[c for c in Gui.listCommands() if "Polygon" in c]`
  tells you the feature exists, so you can go find its App-side TypeId.
- macro recording: enable **Edit -> Preferences -> Python -> Macro ->
  Show script commands in python console**, click once, read the
  emitted python, lift out the App-side calls.

## runtime introspection primitives

for confirming a name or enum value that `api/` does not cover.

```python
obj.TypeId                               # e.g. 'Sketcher::SketchObject'
obj.PropertiesList                       # every property on obj
obj.getTypeIdOfProperty("MapMode")       # e.g. 'App::PropertyEnumeration'
obj.getEnumerationsOfProperty("MapMode") # valid strings for an enum prop
obj.isDerivedFrom("Part::Feature")
App.ActiveDocument.supportedTypes()      # every TypeId addObject accepts
help(Sketcher.Constraint)                # constructor signatures
Gui.listCommands()                       # every gui command id
```

`supportedTypes()` only reports types whose **module is loaded**, and a
headless `freecadcmd` loads almost nothing: `import Part`,
`import Sketcher` and friends first or you will conclude a type does
not exist when it merely is not registered yet.

naming convention in `dir` output: capitalized -> attribute (value),
lowercase -> method (callable), leading `_` -> internal.

**that convention does not hold for properties.** plenty of real
properties start lowercase - `xFree`, `yFree`, `zFree`, `rotxFree`,
`hasXFormula`, `xDisplacementFormula` on `Fem::ConstraintDisplacement`
alone. never filter `PropertiesList` by case, and never assume a
lowercase name in `dir()` is a method.

## always recompute, always verify

- nothing renders until `doc.recompute()`. body origin planes don't
  populate until the first recompute after creating the body.
- after recompute: `obj.Shape.isValid()`, `obj.Shape.Volume > 0`,
  `sketch.FullyConstrained`, `sketch.solve() == 0`. check before
  declaring the task done.

## common traps

- `Gui.ActiveDocument` != `App.ActiveDocument`. paired but different
  members.
- gui representation is rebuilt on recompute; `ViewObject` edits may
  be lost when the `App` object changes.
- gui command ids and python api names are different namespaces.
- property names drift between freecad versions (e.g. `Support` <->
  `AttachmentSupport`). when `AttributeError` hits, read
  `PropertiesList` rather than trying synonyms.
- enum-valued properties (`MapMode`, `AttacherType`, `Role`) accept
  only a fixed string set. enumerate, don't guess.
- constraint constructors vary in arity by type - `help(Sketcher.Constraint)`
  or copy from a macro recording.
- **a property you set can be silently overwritten by the object's own
  recompute.** `Fem::ConstraintForce.DirectionVector` re-derives itself
  from the referenced face's normal every time the constraint executes,
  and `analysis.addObject(c)` alone is enough to trigger it - so a
  direction assigned at construction is gone before any solver reads
  it, and the run *succeeds* with the wrong load. assign such
  properties immediately before the consumer reads them, and verify
  downstream (for FEM, grep the emitted `.inp`). when a value "doesn't
  take", re-read it right after each call rather than assuming the
  assignment stuck.

## the wiki subset

52 pages of the freecad documentation wiki, one file per page named
after its title (spaces -> underscores). grep it for concepts, not for
property names - `api/` is authoritative for those.

```
Grep(pattern="MapMode", path="wiki/", output_mode="files_with_matches")
Grep(pattern="def execute", path="wiki/Scripted_objects.md")
Glob(pattern="wiki/*_API.md")
```

what is here, and what each is for:

- `FreeCAD_Scripting_Basics.md`, `Python_scripting_tutorial.md` - start here
- `Code_snippets.md` - copy-pasteable recipes, the highest-yield single page
- `Topological_data_scripting.md`, `TopoShape_API.md`, `Part_API.md` -
  Vertex/Edge/Wire/Face/Shell/Solid, and how to walk a shape
- `Scripted_objects*.md`, `FeaturePython_*.md`, `App_FeaturePython.md` -
  custom objects: `execute()`, properties, attachment, saving state
- `Expressions.md`, `Property.md` - the expression language and property types
- `Sketcher_scripting.md` - geometry + constraint construction (arity varies
  by constraint type, which no property table can tell you)
- `*_API.md` per workbench (`Draft`, `Mesh`, `TechDraw`, `Selection`,
  `Placement`, `Vector`, `Matrix`, `Object`, `ViewObject`, ...)
- `FEM_Tutorial_Python.md` - the headless analysis path end to end

**what is not here**: per-feature gui pages (`PartDesign_Pad.md` and
friends), macros, tutorials, translations, release notes. that is
deliberate - see `wiki/NOTICE.md` for the provenance, the CC0 licence,
and how to drop the full 2630-page export in beside this if you want
it. `install-skill` only writes the 52 pages it ships and never deletes,
so a fuller mirror composes with it.

the snapshot is **2025-01-15** and the export it came from is dormant,
so treat it as fixed prose rather than something to re-pull. it also
documents the *gui*, so it frequently never names the property behind a
control - `xFree` appears nowhere in even the full 2600-page export
outside an unrelated 0.19 release note. that gap is exactly what `api/`
fills.
