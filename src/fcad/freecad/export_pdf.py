"""export dimensioned TechDraw pages to PDF using a freecad gui instance.

pdf/svg page export lives in TechDrawGui (gui-only), so this is run via
`fcad pdf` (which launches the freecad gui), builds a drawing page for each
part and the assembly, exports each to dist/drawings/<name>.pdf, then quits.
the dxf drawings are still produced headless by `fcad build drawings`.
"""

import os
import time

import FreeCAD as App
import Part

from fcad.loader import load_project
from fcad.freecad import util as fcutil, build_parts, build_assembly, partdesign


def _pump(n=20):
    from PySide import QtWidgets
    for _ in range(n):
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def _wait_views(page, timeout=60.0):
    """block until every view has finished its (gui-threaded) HLR projection.

    in the gui techdraw projects each DrawViewPart on a worker thread, filling
    in its geometry only when that thread completes; exportPageAsPdf renders the
    scene as-is, so firing it early drops the still-computing views from the pdf
    at random. getVisibleEdges() stays empty until a view's projection lands, so
    we pump the event loop until all views report geometry, then settle so the
    scene repaints the now-complete views."""
    from PySide import QtWidgets
    parts = [v for v in page.Views if v.isDerivedFrom("TechDraw::DrawViewPart")]
    deadline = time.time() + timeout
    while time.time() < deadline:
        QtWidgets.QApplication.processEvents()
        try:
            if all(len(v.getVisibleEdges()) for v in parts):
                break
        except Exception:
            pass
        time.sleep(0.05)
    _pump(60)


def _export(page, doc, path, holes=()):
    import TechDrawGui
    import FreeCADGui
    # exportPageAsPdf renders the page's qgraphicsscene, which only exists once
    # the page is opened in an mdi view and every view has finished projecting.
    FreeCADGui.setActiveDocument(doc.Name)
    page.ViewObject.doubleClicked()
    for v in page.Views:
        try:
            v.requestPaint()
        except Exception:
            pass
    page.requestPaint()
    _wait_views(page)
    # hole callouts must be added after the view geometry is realized (on paint),
    # or makeDistanceDim segfaults adding a cosmetic vertex in the gui.
    if holes:
        view = fcutil._view_by_caption(page, "TOP")
        if view is not None:
            fcutil.add_hole_dims(view, holes)
            doc.recompute()
            _wait_views(page)
    TechDrawGui.exportPageAsPdf(page, path)
    print("pdf:", path)


def run(project):
    values = project.defaults()
    data = project.compute(values)
    draw_dir = os.path.join(project.dist, "drawings")
    os.makedirs(draw_dir, exist_ok=True)

    for spec in data["specs"]:
        doc = App.newDocument(spec.name + "_pdf")
        obj = doc.addObject("Part::Feature", spec.name)
        obj.Shape = project.from_spec(spec)
        doc.recompute()
        page = fcutil._page(doc, [obj], build_parts.PART_VIEW, tag="_pdf", dims=True,
                            title={"part": spec.name, "project": project.name})
        _export(page, doc, os.path.join(draw_dir, spec.name + ".pdf"),
                holes=partdesign.dimensions_of(spec)
                if getattr(spec, "declared", False)
                else list(getattr(spec, "dimension_circles", ()) or ()))
        App.closeDocument(doc.Name)

    doc = App.newDocument("assembly_pdf")
    obj = doc.addObject("Part::Feature", project.name)
    obj.Shape = Part.makeCompound(
        build_assembly.placed_shapes(project, data["specs"]))
    doc.recompute()
    page = fcutil._page(doc, [obj], build_assembly.ASM_VIEWS, tag="_pdf", dims=True,
                        title={"part": "assembly", "project": project.name})
    _export(page, doc, os.path.join(draw_dir, "assembly.pdf"))
    App.closeDocument(doc.Name)
    print("pdf export done")


def _close_docs():
    """drop the throwaway export docs so no unsaved-changes dialog can appear."""
    for d in list(App.listDocuments().values()):
        try:
            App.closeDocument(d.Name)
        except Exception:
            pass


def main():
    # the pdfs are written synchronously by run(); tearing down the techdraw gui
    # scenes on a normal qt exit segfaults, so once the work is done we close the
    # docs and hard-exit, skipping the fragile c++ teardown.
    import traceback
    try:
        run(load_project())
    except Exception:
        traceback.print_exc()
        _close_docs()
        os._exit(1)
    _close_docs()
    os._exit(0)
