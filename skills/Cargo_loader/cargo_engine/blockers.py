"""
Minimum 1 SCU cells that stop Auto from placing containers above a size.

Auto places a container only when some whole orientation of it is free
(see packing._valid_rotations). One occupied cell inside every such
placement makes that size impossible. Every larger container in the schema
contains a placement of the next size down, so it is enough to block the
smallest size Auto could still use above the cap. The tests check that
against the packer.

A single orientation is blocked by a lattice: along an axis of length N, one
cell every ``span`` cells, sitting on the high end of each window
(``range(span - 1, N, span)``). That many cells is also the number of
disjoint copies of the window, so none can be removed.

Several orientations of a container whose sides are S, S and L (S divides L)
collapse onto a coarser grid. Any window of length S holds exactly one
coordinate congruent to S - 1 (mod S), and a window of length L holds exactly
K = L / S of them in a row. Blocking the brick is then blocking straight
K-ominoes on that coarse grid, along each axis the long side can point.
K = 2 is a checkerboard (a minimum vertex cover of the grid graph). A longer
omino uses the diagonal when that meets the per-line lower bound. Two axes
that miss the bound are solved by an exact column DP. Three axes that miss
it keep the diagonal: still a cover, and no ship grid in the datamine is in
that case.
"""

from cargo_engine.packing import _valid_rotations
from cargo_engine.schema import (
    CONTAINER_DIMS,
    CONTAINER_MAX_STACK_HEIGHT,
    CONTAINER_SIZES,
)

# Column DP state is k ** n_row. Past this the diagonal (a valid cover) is used.
_DP_MAX_ROWS = 12


def blocker_cells(
    w: int, h: int, l: int, max_size: int, slot_max: int | None = None,
) -> list[tuple[int, int, int]]:
    """Slot-local cells to occupy with 1 SCU boxes.

    After these cells are taken, Auto cannot place any container larger than
    *max_size* that this slot would otherwise accept. *slot_max* is the grid's
    own max container size (None when the grid does not limit it). 32 SCU, or
    a slot that cannot hold anything larger, returns no cells.

    The chosen size still fits whenever the empty slot could hold it: blockers
    sit on the high end of each window, leaving the origin side open.
    """
    if w < 1 or h < 1 or l < 1:
        return []
    if max_size not in CONTAINER_SIZES:
        raise ValueError(f"unknown container size {max_size}")
    shapes = _target_rotations(w, h, l, max_size, slot_max)
    if not shapes:
        return []
    if len(shapes) == 1:
        cw, ch, cl = shapes[0]
        return _lattice(w, h, l, cw, ch, cl)
    return _block_brick(w, h, l, shapes)


def _target_rotations(
    w: int, h: int, l: int, max_size: int, slot_max: int | None,
) -> list[tuple[int, int, int]]:
    """Orientations of the smallest size above the cap that actually fits."""
    for size in CONTAINER_SIZES:
        if size <= max_size:
            continue
        if slot_max is not None and size > slot_max:
            continue
        rots = _valid_rotations(
            CONTAINER_DIMS[size], w, h, l,
            max_ch=CONTAINER_MAX_STACK_HEIGHT.get(size),
        )
        if rots:
            return rots
    return []


def _axis_hits(length: int, span: int) -> list[int]:
    """Positions that meet every window of *span* along a line of *length*."""
    if span > length:
        return []
    return list(range(span - 1, length, span))


def _lattice(
    w: int, h: int, l: int, cw: int, ch: int, cl: int,
) -> list[tuple[int, int, int]]:
    xs = _axis_hits(w, cw)
    ys = _axis_hits(h, ch)
    zs = _axis_hits(l, cl)
    return [(x, y, z) for x in xs for y in ys for z in zs]


def _block_brick(
    w: int, h: int, l: int, shapes: list[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    """Block every orientation of one S×S×L container."""
    sides = tuple(sorted(shapes[0]))
    same = all(tuple(sorted(s)) == sides for s in shapes)
    if not (same and sides[0] == sides[1] and sides[2] % sides[0] == 0):
        # Not the container the coarse grid describes. The union of the
        # one-orientation lattices is still a cover.
        cells: set[tuple[int, int, int]] = set()
        for cw, ch, cl in shapes:
            cells.update(_lattice(w, h, l, cw, ch, cl))
        return sorted(cells)

    short, long = sides[0], sides[2]
    k = long // short
    axes: set[str] = set()
    for cw, ch, cl in shapes:
        if cw == long:
            axes.add("x")
        if ch == long:
            axes.add("y")
        if cl == long:
            axes.add("z")
    nx, ny, nz = w // short, h // short, l // short
    points = _coarse_points(nx, ny, nz, k, axes)
    off = short - 1
    return sorted(
        (short * i + off, short * j + off, short * m + off)
        for i, j, m in points
    )


def _coarse_points(
    nx: int, ny: int, nz: int, k: int, axes: set[str],
) -> list[tuple[int, int, int]]:
    if nx < 1 or ny < 1 or nz < 1 or not axes:
        return []
    if k <= 1:
        return [(i, j, m) for i in range(nx) for j in range(ny) for m in range(nz)]
    if k == 2:
        return _checkerboard(nx, ny, nz, axes)
    diag = _diagonal(nx, ny, nz, k, axes)
    if len(diag) == _line_lower_bound(nx, ny, nz, k, axes):
        return diag
    if len(axes) == 2:
        exact = _exact_two_axes(nx, ny, nz, k, axes)
        if exact is not None:
            return exact
    if len(axes) < 3:
        return diag
    return _strip_redundant(diag, nx, ny, nz, k, axes)


def _sum_axes(i: int, j: int, m: int, axes: set[str]) -> int:
    total = 0
    if "x" in axes:
        total += i
    if "y" in axes:
        total += j
    if "z" in axes:
        total += m
    return total


def _diagonal(
    nx: int, ny: int, nz: int, k: int, axes: set[str],
) -> list[tuple[int, int, int]]:
    """One residue class of (i + j + m). Meets every K-omino on a required axis."""
    residue = k - 1
    return [
        (i, j, m)
        for i in range(nx) for j in range(ny) for m in range(nz)
        if _sum_axes(i, j, m, axes) % k == residue
    ]


def _checkerboard(
    nx: int, ny: int, nz: int, axes: set[str],
) -> list[tuple[int, int, int]]:
    """Smaller colour of the grid graph on the required axes.

    A tie keeps the odd colour, so the origin cell stays free.
    """
    even: list[tuple[int, int, int]] = []
    odd: list[tuple[int, int, int]] = []
    for i in range(nx):
        for j in range(ny):
            for m in range(nz):
                (odd if _sum_axes(i, j, m, axes) % 2 else even).append((i, j, m))
    if len(odd) <= len(even):
        return odd
    return even


def _line_lower_bound(
    nx: int, ny: int, nz: int, k: int, axes: set[str],
) -> int:
    """Disjoint K-ominoes along the busiest required axis."""
    bound = 0
    if "x" in axes:
        bound = max(bound, (nx // k) * ny * nz)
    if "y" in axes:
        bound = max(bound, nx * (ny // k) * nz)
    if "z" in axes:
        bound = max(bound, nx * ny * (nz // k))
    return bound


def _exact_two_axes(
    nx: int, ny: int, nz: int, k: int, axes: set[str],
) -> list[tuple[int, int, int]] | None:
    """Exact cover when the long side points along only two axes.

    The third coarse axis does not appear in any window, so each of its
    layers is the same 2D problem.
    """
    present = [a for a in ("x", "y", "z") if a in axes]
    if len(present) != 2:
        return None
    free = next(a for a in ("x", "y", "z") if a not in axes)
    dims = {"x": nx, "y": ny, "z": nz}
    a, b = present
    n_a, n_b = dims[a], dims[b]
    if min(n_a, n_b) > _DP_MAX_ROWS:
        return None
    if n_a <= n_b:
        pairs = _dp_2d(n_a, n_b, k)
    else:
        swapped = _dp_2d(n_b, n_a, k)
        pairs = None if swapped is None else [(p, q) for q, p in swapped]
    if pairs is None:
        return None
    cells = []
    for f in range(dims[free]):
        for ia, ib in pairs:
            coord = {a: ia, b: ib, free: f}
            cells.append((coord["x"], coord["y"], coord["z"]))
    return cells


def _mask_hits(mask: int, n_row: int, k: int) -> bool:
    for start in range(n_row - k + 1):
        window = ((1 << k) - 1) << start
        if mask & window == 0:
            return False
    return True


def _dp_2d(n_row: int, n_col: int, k: int) -> list[tuple[int, int]] | None:
    """Minimum cells on an n_row × n_col grid that hit every K-omino both ways.

    Returns (row, col) pairs, or None if no cover is found (it always should
    be: the diagonal is one). Columns grow along the second axis. The state
    at a column is, per row, how many trailing columns are still empty; k of
    those in a row would be an empty window.
    """
    if n_row < k or n_col < k:
        return None
    valid = [mask for mask in range(1 << n_row) if _mask_hits(mask, n_row, k)]
    start = tuple(0 for _ in range(n_row))
    cost: dict[tuple[int, ...], int] = {start: 0}
    pred: list[dict[tuple[int, ...], tuple[tuple[int, ...], int]]] = []
    inf = 10 ** 9
    for _col in range(n_col):
        nxt: dict[tuple[int, ...], int] = {}
        back: dict[tuple[int, ...], tuple[tuple[int, ...], int]] = {}
        for state, so_far in cost.items():
            for mask in valid:
                counts: list[int] = []
                bits = 0
                ok = True
                for r in range(n_row):
                    if (mask >> r) & 1:
                        counts.append(0)
                        bits += 1
                    else:
                        free = state[r] + 1
                        if free >= k:
                            ok = False
                            break
                        counts.append(free)
                if not ok:
                    continue
                ns = tuple(counts)
                nc = so_far + bits
                if nc < nxt.get(ns, inf):
                    nxt[ns] = nc
                    back[ns] = (state, mask)
        if not nxt:
            return None
        pred.append(back)
        cost = nxt

    state = min(cost, key=cost.get)
    masks: list[int] = []
    for col in range(n_col - 1, -1, -1):
        state, mask = pred[col][state]
        masks.append(mask)
    masks.reverse()
    return [
        (r, col)
        for col, mask in enumerate(masks)
        for r in range(n_row)
        if (mask >> r) & 1
    ]


def _lines(nx: int, ny: int, nz: int, k: int, axes: set[str]):
    if "x" in axes and nx >= k:
        for j in range(ny):
            for m in range(nz):
                yield [(i, j, m) for i in range(nx)]
    if "y" in axes and ny >= k:
        for i in range(nx):
            for m in range(nz):
                yield [(i, j, m) for j in range(ny)]
    if "z" in axes and nz >= k:
        for i in range(nx):
            for j in range(ny):
                yield [(i, j, m) for m in range(nz)]


def _covers(
    chosen: set[tuple[int, int, int]],
    nx: int, ny: int, nz: int, k: int, axes: set[str],
) -> bool:
    for line in _lines(nx, ny, nz, k, axes):
        hits = [t for t, cell in enumerate(line) if cell in chosen]
        cursor = 0
        n = len(line)
        for start in range(n - k + 1):
            while cursor < len(hits) and hits[cursor] < start:
                cursor += 1
            if cursor >= len(hits) or hits[cursor] >= start + k:
                return False
    return True


def _strip_redundant(
    points: list[tuple[int, int, int]],
    nx: int, ny: int, nz: int, k: int, axes: set[str],
) -> list[tuple[int, int, int]]:
    """Drop cells the cover does not need. Low indices go first, so a cell
    that can be spared near the origin is the one that goes."""
    chosen = set(points)
    for cell in sorted(chosen):
        chosen.remove(cell)
        if not _covers(chosen, nx, ny, nz, k, axes):
            chosen.add(cell)
    return sorted(chosen)
