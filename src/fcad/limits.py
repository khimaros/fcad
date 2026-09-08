"""how much memory and wall clock fcad lets its children spend.

a FEM solve is the one fcad step whose cost is set by the mesh rather than by the
model, and the mesh a project casually asks for is easily larger than the machine
can factor. CalculiX solves the stiffness matrix directly, so its peak memory
follows the fill-in of a 3d sparse cholesky - about nodes^(4/3), which means
halving `mesh_size` costs roughly ten times the RAM - and there is no warning on
the way: the run dies at exit -9 several minutes in, having driven everything
else on the machine into reclaim first. gmsh fails from the other end, sizing
elements off curvature until a part carrying a few dozen fastener holes grinds
for a quarter of an hour at several gigabytes and produces nothing.

fcad bounds that twice, deliberately, because the two bounds do different jobs.
comparing `ccx_bytes` against `budget` refuses a solve that cannot fit *before*
CalculiX starts: it is the fast failure, and the only one that can say what to
change. `limit_child` is the backstop for when the estimate is wrong or the
runaway is the mesher - an rlimit is inherited, so setting it on freecadcmd is
what reaches gmsh and ccx, two levels down and spawned by FreeCAD rather than by
us. they then fail with an allocation error instead of taking the desktop with
them.
"""

import os
import resource

MEMINFO = "/proc/meminfo"
MEM_ENV = "FCAD_MEM"                    # ceiling override: "24G", "512M", bytes
MESH_TIMEOUT_ENV = "FCAD_FEM_MESH_TIMEOUT"
MESH_TIMEOUT = 900.0                    # seconds gmsh may run before it is killed
HEADROOM = 0.8                          # of MemAvailable; the rest stays the desktop's
MIN_BUDGET = 2 << 30                    # below this, refusing helps nobody
# heap the toolchain maps before doing any work, which the ceiling must sit above
# or nothing starts at all. OpenBLAS reserves a buffer pool up front and
# RLIMIT_DATA counts the mapping whether or not it is ever touched: ccx solving
# 1671 nodes reserves 2.29 GiB of VmData against 0.03 GiB resident, and the same
# 2.2 GiB offset still stands at 100855 nodes (4.38 vs 2.20), so it is a constant
# and not a share. it does not vary with thread count either - forcing OpenBLAS
# to 1, 2, 4 or 16 threads changes nothing - so this travels between machines.
# measured by bisection at 3 GiB for a whole freecadcmd solve; 4 leaves margin.
RESERVED = 4 << 30
UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
# fitted to measured peak RSS of ccx 2.21 on a 2nd-order steel cantilever at six
# mesh sizes, 1671 to 100855 nodes (26 MB to 2.25 GB): the coefficient holds to
# +-2% above 20k nodes, which is the range where refusing matters, and reads high
# by about a third below that. it extrapolates honestly onto another machine's
# figures too - 11.2G for a 343k-node solve there whose peak RSS was measured at
# 10.5 GB before the kernel killed it.
CCX_BYTES, CCX_EXP = 500.0, 4.0 / 3.0


def parse_size(text):
    """bytes from "24G" / "512M" / a plain integer, or None if it is not a size."""
    text = str(text).strip().upper().rstrip("B")
    scale = UNITS.get(text[-1:], 1)
    try:
        return int(float(text[:-1] if scale > 1 else text) * scale)
    except ValueError:
        return None


def human(size):
    """a size written the way MEM_ENV accepts it back: "9.3G", "512M", "4096"."""
    for suffix in ("T", "G", "M", "K"):
        if size >= UNITS[suffix]:
            return "%.3g%s" % (size / float(UNITS[suffix]), suffix)
    return str(int(size))


def mem_available():
    """MemAvailable in bytes - what can be had without reclaiming hard - or None
    where the kernel does not publish it, which turns every bound here off."""
    try:
        with open(MEMINFO) as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def budget():
    """the bytes one fcad child may use, or None when that cannot be known.

    an explicit MEM_ENV is taken exactly, since a user raising the ceiling for a
    known-big job means the number they wrote. an inferred one leaves the rest of
    the machine its headroom, and never drops below MIN_BUDGET: a moment when
    little is free should not turn an ordinary build into a hard failure."""
    override = parse_size(os.environ.get(MEM_ENV, ""))
    if override:
        return override
    avail = mem_available()
    return max(MIN_BUDGET, int(avail * HEADROOM)) if avail else None


def ccx_bytes(nodes):
    """estimated peak memory of a direct CalculiX solve of a tet mesh."""
    return int(CCX_BYTES * float(nodes) ** CCX_EXP)


def mesh_timeout():
    """seconds gmsh is allowed before it is killed."""
    try:
        return float(os.environ.get(MESH_TIMEOUT_ENV) or MESH_TIMEOUT)
    except ValueError:
        return MESH_TIMEOUT


def limit_child():
    """cap a child's heap, as a subprocess preexec_fn.

    RLIMIT_DATA rather than RLIMIT_AS: it counts the brk and anonymous mappings a
    solver really allocates, where address space also counts every shared library
    and thread stack FreeCAD maps and would refuse runs that were never going to
    be large. the ceiling is the budget plus RESERVED, because this is the crash
    barrier and not the accounting - `ccx_bytes` against `budget` is what decides
    whether a solve is reasonable, and a ceiling tight enough to stop the tools
    starting would only turn "this is too big" into "fcad is broken". the hard
    limit is left alone, so this can only ever narrow."""
    cap, (_, hard) = budget(), resource.getrlimit(resource.RLIMIT_DATA)
    if not cap:
        return
    cap += RESERVED
    if hard != resource.RLIM_INFINITY:
        cap = min(cap, hard)
    resource.setrlimit(resource.RLIMIT_DATA, (cap, hard))
