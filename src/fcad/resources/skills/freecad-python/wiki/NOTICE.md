# provenance of these pages

these 52 markdown files are a **curated subset** of the FreeCAD documentation
wiki, taken from the automated wiki->markdown export at
`github.com/FreeCAD/FreeCAD-documentation` (licensed **CC0 1.0 Universal**, so
redistributed here without restriction).

that export has been dormant since **2025-01-15**, which is the effective date of
this snapshot. the live wiki at wiki.freecad.org has continued to move; the
export has not, and reproducing it faithfully needs the repo's own `migrate.py`
(pandoc plus a pile of template post-processing) rather than a plain
`pandoc -f mediawiki -t gfm`, which mangles links, images and the GuiCommand
header block.

## why a subset

the full export is 2630 pages and 22 MB, and most of it earns nothing here: 599
pages are stubs under 600 bytes, 926 are gui command references with no python in
them at all, and the long tail is macro listings, compile guides and PySide/POV-ray
tutorials. only 446 pages contain a python code fence.

what is kept is the part that no amount of introspection can replace - semantics
and worked examples. `api/` states that a property exists, what type it is and
what values it accepts; these pages explain what it *means*: the FeaturePython
lifecycle, attachment and `MapMode`, topological traversal, expression syntax,
constraint construction, per-workbench python entry points.

selection was by filename: `*_API`, `*_scripting`, `Scripted_objects*`,
`FeaturePython*`, `Topological*`, `Code_snippets`, `Expressions`, `Property`, and
the python tutorials. 52 pages, ~650 KB - 2% of the files carrying essentially
all of the scripting value.

## what is deliberately absent

per-feature gui documentation (`PartDesign_Pad.md`, `Sketcher_CreateArc.md`, ...),
tutorials, macro listings, translations and release notes. if you want them,
clone the export above into this directory: `install-skill` only ever writes the
52 files listed here and never deletes anything else, so a fuller mirror composes
with it.
