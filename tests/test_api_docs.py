"""end-to-end test for `fcad api-docs` (fcad.freecad.api_docs).

the command exists so that nobody - human or agent - has to guess a FreeCAD
property name, so the test asserts the two ways it can quietly fail to deliver
that rather than merely that it wrote some files.

first, coverage. `doc.supportedTypes()` only reports types whose module is
*loaded*, and freecadcmd loads almost nothing, so a generator that forgets to
preload the workbenches emits a plausible-looking index holding `App` and
`Image` and nothing else. exit status and file count look identical either way.

second, the property names themselves. `Fem::ConstraintDisplacement` carries its
per-axis freedoms as `xFree`/`yFree`/`zFree` - lowercase, absent from the wiki -
which is exactly the kind of name this file is supposed to make discoverable.

it drives the real cli (so the `<dist>/api` default and the entry wiring are
covered too), and covers `install-skill`, which is how the reference actually
reaches an agent: it ships the skill and regenerates `api/` for the freecad in
front of it, skipping the work when that build has not changed.

needs FreeCAD: run with `freecadcmd tests/test_api_docs.py`. results go to
$RESULT_FILE, matching the other tests.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile

# make the in-repo fcad package importable under FreeCAD's bundled python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import cli
from fcad.freecad import api_docs

# TypeIds from workbenches that only register once their module is imported: the
# canary for a missing preload. App:: types would pass without one.
PRELOADED_TYPES = ("Part::Feature", "PartDesign::Body", "Sketcher::SketchObject",
                   "TechDraw::DrawPage", "Mesh::Feature", "Fem::FemAnalysis")
# the properties that motivated the command, none of which the wiki names.
DISPLACEMENT_PROPS = ("xFree", "yFree", "zFree", "rotxFree", "xDisplacement")
MIN_TYPES = 200      # 337 on FreeCAD 1.1.1; 38 with the preload removed
# the wiki subset carries what introspection cannot: semantics and worked
# examples. these four are the load-bearing ones.
WIKI_CORE = ("Code_snippets.md", "Topological_data_scripting.md",
             "Scripted_objects.md", "Sketcher_scripting.md")
MIN_WIKI_PAGES = 40  # 52 shipped + whatever a fuller local mirror adds


def _read(path):
    with open(path) as f:
        return f.read()


def _checks(dist):
    rc = cli.main(["-d", dist, "api-docs"])
    out = os.path.join(dist, "api")
    present = {f: os.path.exists(os.path.join(out, f)) for f in api_docs.FILES}
    checks = [("api-docs exits 0", rc == 0),
              ("writes into <dist>/api by default", os.path.isdir(out))]
    checks += [("writes %s" % f, ok) for f, ok in sorted(present.items())]
    if not all(present.values()):
        return checks

    index = _read(os.path.join(out, "index.md"))
    typeids = _read(os.path.join(out, "typeids.md"))
    fem = _read(os.path.join(out, "fem.md"))
    props = _read(os.path.join(out, "properties.md"))
    factories = _read(os.path.join(out, "factories.md"))
    n_types = typeids.count("\n- `")
    version = api_docs.version()

    return checks + [
        ("index names the running build: %s" % version, version in index),
        ("every file is marked generated",
         all("do not hand-edit" in _read(os.path.join(out, f))
             for f in api_docs.FILES if f != "index.md")),
        ("no workbench missing from this build",
         "not available on this build" not in index + typeids + props),
        ("typeids covers the loaded workbenches, not just App: %d types" % n_types,
         n_types >= MIN_TYPES),
    ] + [
        ("typeids includes %s" % t, "`%s`" % t in typeids) for t in PRELOADED_TYPES
    ] + [
        ("fem.md documents ConstraintDisplacement.%s" % p, "`%s`" % p in fem)
        for p in DISPLACEMENT_PROPS
    ] + [
        ("fem.md records each maker's TypeId",
         "`makeConstraintForce" in fem and "`Fem::ConstraintForce`" in fem),
        ("fem.md gives a signature for makers it cannot instantiate",
         "makeMeshRegion(doc, base_mesh" in fem),
        ("factories.md lists makers with signatures",
         "makeConstraintDisplacement(doc" in factories),
        ("properties.md tables a common TypeId",
         "## `App::Link`" in props and "`LinkedObject`" in props),
        ("enum choices are enumerated, not guessed",
         "App::PropertyEnumeration" in props and "enum values" in props),
    ]


def _cli(argv):
    """run the cli, capturing this process's stdout; (rc, output).

    a child freecadcmd writes straight to fd 1 and is not captured, which is
    fine - the verdict lines under test are printed by the parent."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def _skill_checks(root):
    """`install-skill` installs, then refreshes only when it must."""
    import fcad
    shipped = _read(os.path.join(os.path.dirname(os.path.abspath(fcad.__file__)),
                                 "resources", "skills", cli.SKILL_NAME, "SKILL.md"))
    out = os.path.join(root, cli.SKILL_NAME)
    api = os.path.join(out, "api")
    stamp = os.path.join(api, cli.SKILL_STAMP)

    # a page only a user would have: install must refresh its own 52 without
    # touching a fuller mirror someone dropped in beside them.
    wiki = os.path.join(out, "wiki")
    os.makedirs(wiki, exist_ok=True)
    mine = os.path.join(wiki, "PartDesign_Pad.md")
    with open(mine, "w") as f:
        f.write("a page fcad does not ship\n")

    rc, first = _cli(["install-skill", "--dir", root])
    installed = os.path.exists(os.path.join(out, "SKILL.md"))
    pages = [f for f in os.listdir(wiki) if f.endswith(".md")]
    # fcad's own contract ships as a second skill beside the api one. it needs no
    # generated half, so it must install without one rather than being skipped.
    own = os.path.join(root, "fcad", "SKILL.md")
    own_shipped = _read(os.path.join(
        os.path.dirname(os.path.abspath(fcad.__file__)),
        "resources", "skills", "fcad", "SKILL.md"))
    checks = [
        ("install-skill exits 0", rc == 0),
        ("installs SKILL.md", installed),
        ("installs the fcad skill too, verbatim",
         os.path.exists(own) and _read(own) == own_shipped),
        ("and that one carries no api/ it does not need",
         not os.path.exists(os.path.join(root, "fcad", "api"))),
        ("the fcad skill declares itself as such",
         own_shipped is not None and "name: fcad" in own_shipped),
        ("installs the shipped SKILL.md verbatim",
         installed and _read(os.path.join(out, "SKILL.md")) == shipped),
        ("generates the api reference beside it",
         all(os.path.exists(os.path.join(api, f)) for f in api_docs.FILES)),
        ("records the freecad build it generated for",
         os.path.exists(stamp) and "FreeCAD" in _read(stamp)),
        ("reports the install", "installed skill" in first),
        ("installs the curated wiki subset: %d pages" % len(pages),
         len(pages) >= MIN_WIKI_PAGES),
        ("ships the semantics pages api/ cannot replace",
         all(os.path.exists(os.path.join(wiki, p)) for p in WIKI_CORE)),
        ("ships the provenance/licence notice",
         "CC0" in (_read(os.path.join(wiki, "NOTICE.md")) or "")),
        ("leaves a fuller mirror's other pages alone",
         _read(mine) == "a page fcad does not ship\n"),
    ]
    if not installed:
        return checks

    rc_again, again = _cli(["install-skill", "--dir", root])
    rc_force, forced = _cli(["install-skill", "--dir", root, "--force"])
    with open(stamp, "w") as f:
        f.write("FreeCAD 0.21 Revision: stale\n")
    rc_stale, stale = _cli(["install-skill", "--dir", root])
    # each skill reports for itself, so these name the one they mean: a stale api
    # stamp must refresh `freecad-python` while `fcad`, whose prose has not
    # changed, correctly stays a no-op.
    current = "%s already current" % cli.SKILL_NAME
    return checks + [
        ("rerunning is a no-op on an unchanged build",
         rc_again == 0 and current in again and "fcad already current" in again),
        ("--force regenerates anyway",
         rc_force == 0 and "already current" not in forced),
        ("a changed freecad build refreshes the api skill",
         rc_stale == 0 and current not in stale),
        ("without disturbing the skill that does not depend on the build",
         "fcad already current" in stale),
        ("the refresh restores the current build stamp",
         "FreeCAD" in _read(stamp) and "stale" not in _read(stamp)),
    ]


def main():
    dist = tempfile.mkdtemp()
    try:
        checks = _checks(dist) + _skill_checks(os.path.join(dist, "skills"))
    finally:
        shutil.rmtree(dist, ignore_errors=True)
    failed = [name for name, ok in checks if not ok]
    lines = ["%s %s" % ("ok  " if ok else "FAIL", name) for name, ok in checks]
    lines.append("RESULT %s" % ("PASS" if not failed else "FAIL"))
    text = "\n".join(lines) + "\n"
    rf = os.environ.get("RESULT_FILE")
    if rf:
        with open(rf, "w") as f:
            f.write(text)
    print(text)
    return 0 if not failed else 1


if any(a.endswith("test_api_docs.py") for a in sys.argv):
    raise SystemExit(main())
