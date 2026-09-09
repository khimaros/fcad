"""end-to-end test for the bounds fcad puts on a FEM solve's appetite.

R6.5. a solve is the one fcad step whose cost is set by the mesh rather than the
model, and an unbounded one takes the machine down with it: CalculiX factors
directly, so its memory grows as roughly nodes^(4/3), and a mesh a project
casually asked for has been OOM-killed (exit -9) at 282k nodes after driving a
64 GB desktop under 1 GB free. gmsh fails from the other end, sizing elements off
curvature until a part with a few dozen fastener holes grinds for a quarter of an
hour at several gigabytes with no output at all.

so this asserts the three bounds actually bite, over the real stack: a solve too
big for the budget is refused *before* CalculiX runs and says by how much, a
mesher that overruns is killed and says so, and the child fcad spawns carries an
inherited heap ceiling and sits in its own process group so one signal reaps the
mesher and solver with it.

needs FreeCAD + gmsh: run with `freecadcmd tests/test_limits.py`. results go to
$RESULT_FILE (freecadcmd swallows stdout and exits 0 on error, so a file is the
reliable channel), matching the other tests.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# make the in-repo fcad package importable under FreeCAD's bundled python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import resource

from fcad import config, limits
from fcad._run import entry_path, popen

# a plain steel bar with a stated mesh_size, so the node count is repeatable and
# the whole solve is a couple of seconds when it is allowed to run.
PROJECT = '''
from fcad import fem_select as fs
import fcad, Part, FreeCAD as App

PARAMS = {"length": 200.0, "width": 20.0, "height": 20.0}
MATERIAL = "steel"
FEM = {"__default__": {"fixed": [fs.min_along("x")],
                       "loads": [{"faces": fs.max_along("x"), "magnitude": 200.0,
                                  "direction": "-z"}],
                       "mesh_size": 10.0}}

def compute(p):
    return [fcad.PartSpec("bar", placements=[App.Placement()],
                          solid=lambda: Part.makeBox(p["length"], p["width"], p["height"]))]
'''
NAME = "bar"
TARGET = "bar"
TINY_BUDGET = "1M"          # smaller than any real solve, so the guard must fire
AMPLE_BUDGET = "8G"         # far above it, so the same solve must still run
MESH_TIMEOUT = "0.05"       # seconds; gmsh cannot finish a solid in 50ms
MARK = "FCADLIMITS"         # child's answer, told apart from FreeCAD's greeting
KEEP_WORK = "FCAD_KEEP_WORK"

# the fit itself, checked at the node counts it was measured over: peak ccx RSS
# on a 2nd-order steel cantilever was 96 MB at 8181 nodes and 2.25 GB at 100855.
FIT = [(8181, 96.1e6), (100855, 2249.7e6)]
FIT_TOL = 0.35


def _project(root):
    path = os.path.join(root, NAME + ".fcad")
    with open(path, "w") as f:
        f.write(PROJECT)
    return config.resolve(project=path)


def _fem(cfg, env_extra):
    """one `fem` run spawned the way the cli spawns it; (rc, output).

    through `popen` rather than plain subprocess, so the inherited ceiling is
    part of what is under test: a ceiling set to the budget alone stops FreeCAD
    itself from starting, and the run then fails for the wrong reason - with none
    of the diagnosis the preflight exists to give."""
    env = {**os.environ, **cfg.env(), "FCAD_TARGET": TARGET, **env_extra}
    proc = popen([cfg.freecad, entry_path(), "fem"], env=env,
                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.communicate()[0].decode()
    return proc.returncode, out


def _npz(cfg):
    return os.path.join(cfg.dist, TARGET + ".fem.npz")


def _unit_checks():
    """the pure-python arithmetic every bound is decided by."""
    fit = [(n, abs(limits.ccx_bytes(n) - want) <= FIT_TOL * want)
           for n, want in FIT]
    return [
        ("parse_size reads a suffix", limits.parse_size("8G") == 8 << 30),
        ("parse_size reads a fraction", limits.parse_size("1.5G") == 3 << 29),
        ("parse_size reads plain bytes", limits.parse_size("4096") == 4096),
        ("parse_size rejects nonsense", limits.parse_size("lots") is None),
        ("human round-trips through parse_size",
         limits.parse_size(limits.human(9 << 30)) == 9 << 30),
        ("ccx estimate is superlinear: 4x the nodes costs over 6x the memory",
         limits.ccx_bytes(200000) > 6 * limits.ccx_bytes(50000)),
    ] + [("ccx estimate matches measured RSS at %d nodes" % n, ok) for n, ok in fit]


def _budget_checks():
    """the ceiling: an explicit one is exact, an inferred one leaves headroom."""
    keep = os.environ.pop(limits.MEM_ENV, None)
    try:
        inferred = limits.budget()
        avail = limits.mem_available()
        os.environ[limits.MEM_ENV] = AMPLE_BUDGET
        override = limits.budget()
    finally:
        os.environ.pop(limits.MEM_ENV, None)
        if keep is not None:
            os.environ[limits.MEM_ENV] = keep
    return [
        ("MemAvailable is readable", avail is not None and avail > 0),
        ("inferred budget leaves the machine headroom",
         inferred is not None and inferred < avail),
        ("%s overrides it exactly" % limits.MEM_ENV, override == 8 << 30),
    ]


def _preflight_checks(cfg):
    """R6.5: a solve too big for the budget is refused before CalculiX starts."""
    shutil.rmtree(cfg.dist, ignore_errors=True)
    t0 = time.time()
    rc, out = _fem(cfg, {limits.MEM_ENV: TINY_BUDGET})
    refused, partial = time.time() - t0, os.path.exists(_npz(cfg))
    ok_rc, good = _fem(cfg, {limits.MEM_ENV: AMPLE_BUDGET})
    return [
        ("a solve over budget exits non-zero", rc != 0),
        ("it explains itself", "fcad fem:" in out),
        ("it names the node count", "nodes" in out),
        ("it names the ceiling it was measured against", TINY_BUDGET in out),
        ("it names the knob that raises the ceiling", limits.MEM_ENV in out),
        ("it names mesh_size, which is what actually shrinks the solve",
         "mesh_size" in out),
        ("it writes no partial result", not partial),
        ("it refuses in seconds, not after the solve (%.1fs)" % refused,
         refused < 120.0),
        ("the same solve inside budget still runs", ok_rc == 0),
        ("and writes its result", os.path.exists(_npz(cfg))),
        ("and reports it", "fem ok:" in good),
    ]


def _workdir_checks(cfg):
    """R6.5: a solve leaves no scratch behind.

    FreeCAD hands the mesher and the solver a fresh mkdtemp each and removes
    neither, so every solve used to leak two directories - and on linux /tmp is
    tmpfs, which makes those resident memory held until reboot rather than files
    on a disk. a few hundred KB for this beam, hundreds of MB for a real mesh,
    once per solve, forever. the mesher's is the easy one to miss: its
    `prepare()` calls `get_tmp_file_paths()` with no argument, which ignores the
    mesh object's WorkingDirectory entirely."""
    shutil.rmtree(cfg.dist, ignore_errors=True)
    before = _scratch()
    rc, _ = _fem(cfg, {limits.MEM_ENV: AMPLE_BUDGET})
    after = _scratch()
    kept_rc, kept = _fem(cfg, {limits.MEM_ENV: AMPLE_BUDGET, KEEP_WORK: "1"})
    held = _scratch() - after
    for path in held:
        shutil.rmtree(path, ignore_errors=True)
    return [
        ("the solve ran", rc == 0 and kept_rc == 0),
        ("and left no working directory behind %s" % sorted(after - before),
         after == before),
        ("%s keeps them instead" % KEEP_WORK, len(held) == 1),
        ("and says where", kept_rc == 0 and "fem work kept:" in kept),
    ]


def _scratch():
    """the FEM scratch directories currently sitting in the temp dir."""
    tmp = tempfile.gettempdir()
    return {os.path.join(tmp, d) for d in os.listdir(tmp)
            if d.startswith(("fcfem_", "fcad_fem_", "fem_"))}


def _mesh_timeout_checks(cfg):
    """R6.5: a mesher that overruns is killed and named, not waited on forever."""
    shutil.rmtree(cfg.dist, ignore_errors=True)
    rc, out = _fem(cfg, {limits.MESH_TIMEOUT_ENV: MESH_TIMEOUT,
                         limits.MEM_ENV: AMPLE_BUDGET})
    return [
        ("a mesher over its wall clock exits non-zero", rc != 0),
        ("it explains itself", "fcad fem:" in out),
        ("it says the mesher was the thing that ran long", "gmsh" in out),
        ("it names the timeout it exceeded", MESH_TIMEOUT in out),
        ("it names the knob that raises it", limits.MESH_TIMEOUT_ENV in out),
        ("it writes no partial result", not os.path.exists(_npz(cfg))),
    ]


def _alive(pid):
    return os.path.exists("/proc/%d" % pid)


def _child_checks():
    """R6.5: what fcad spawns is bounded and reapable as a whole.

    the ceiling has to be inherited, because the processes that get big are the
    mesher and the solver, two levels down and spawned by FreeCAD rather than by
    fcad. the session is the other half: killing freecadcmd alone leaves a gmsh
    behind still holding gigabytes, which is how a runaway survived being killed
    by pid."""
    os.environ[limits.MEM_ENV] = AMPLE_BUDGET
    try:
        proc = popen([sys.executable, "-c",
                      "import os, resource;"
                      "print('%s', resource.getrlimit(resource.RLIMIT_DATA)[0],"
                      "os.getsid(0))" % MARK], env=dict(os.environ),
                     stdout=subprocess.PIPE)
        # by marker, not by position: FreeCAD's bundled python greets on startup.
        told = [ln for ln in proc.communicate()[0].decode().splitlines()
                if ln.startswith(MARK)]
        soft, sid = (int(x) for x in told[0].split()[1:])
        # a child of the child, to prove one signal reaps the whole tree.
        tree = popen(["sh", "-c", "sleep 60 & echo $!; wait"],
                     env=dict(os.environ), stdout=subprocess.PIPE)
        grandchild = int(tree.stdout.readline())
        os.killpg(tree.pid, signal.SIGKILL)
        tree.wait()
        time.sleep(0.5)
        reaped = not _alive(grandchild)
    finally:
        os.environ.pop(limits.MEM_ENV, None)
    ours = resource.getrlimit(resource.RLIMIT_DATA)[0]
    return [
        ("the child inherits a heap ceiling",
         soft == (8 << 30) + limits.RESERVED),
        ("which clears what the toolchain reserves before it works",
         soft > (8 << 30) + (2 << 30)),
        ("which fcad itself does not carry", ours != soft),
        ("the child gets its own process group", sid == proc.pid),
        ("so one signal reaps the tools it spawned", reaped),
    ]


def main():
    root = tempfile.mkdtemp()
    try:
        cfg = _project(root)
        checks = (_unit_checks() + _budget_checks() + _child_checks()
                  + _preflight_checks(cfg) + _workdir_checks(cfg)
                  + _mesh_timeout_checks(cfg))
    finally:
        shutil.rmtree(root, ignore_errors=True)
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


if any(a.endswith("test_limits.py") for a in sys.argv):
    raise SystemExit(main())
