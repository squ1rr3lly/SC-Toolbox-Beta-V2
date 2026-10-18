"""Minimum 1 SCU cells that stop Auto placing anything above a chosen size."""

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')))
import shared.path_setup  # noqa: E402
shared.path_setup.ensure_path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from cargo_engine.blockers import blocker_cells  # noqa: E402
from cargo_engine.packing import fill_slot_around, place_containers_3d  # noqa: E402
from cargo_engine.schema import CONTAINER_SIZES  # noqa: E402


def _slot(w, h, l, slot_max=None):
    return {
        "w": w, "h": h, "l": l, "capacity": w * h * l,
        "maxSize": slot_max, "minSize": None,
    }


def _fits(size, w, h, l, blocked=()):
    return bool(place_containers_3d(_slot(w, h, l), {size: 1}, set(blocked)))


def _larger_fits(w, h, l, cap, blocked):
    return any(
        _fits(size, w, h, l, blocked)
        for size in CONTAINER_SIZES if size > cap
    )


class TestClosedForms:
    def test_nothing_to_block(self):
        assert blocker_cells(8, 4, 25, 32) == []
        assert blocker_cells(8, 4, 25, 8, slot_max=8) == []
        # 2x2x3 holds an 8 but nothing bigger.
        assert blocker_cells(2, 2, 3, 8) == []
        assert blocker_cells(1, 1, 1, 1) == []

    def test_unknown_size(self):
        with pytest.raises(ValueError):
            blocker_cells(4, 2, 4, 3)

    def test_8scu_lattice(self):
        # Cap 4 blocks the 2x2x2 cube. High end of each window of 2.
        assert blocker_cells(4, 2, 4, 4) == [
            (1, 1, 1), (1, 1, 3), (3, 1, 1), (3, 1, 3),
        ]

    def test_4scu_lattice_on_every_layer(self):
        # 4 SCU lies flat (2 x 1 x 2), so every layer needs the floor lattice.
        assert blocker_cells(4, 2, 4, 2) == [
            (1, 0, 1), (1, 0, 3), (1, 1, 1), (1, 1, 3),
            (3, 0, 1), (3, 0, 3), (3, 1, 1), (3, 1, 3),
        ]

    def test_2scu_checkerboard_leaves_the_origin(self):
        # Both flat dominoes. A tie picks the odd colour, so (0, 0, 0) stays free.
        assert blocker_cells(2, 1, 2, 1) == [(0, 0, 1), (1, 0, 0)]

    def test_wide_grid_counts(self):
        # 8x4x25 is large enough for every orientation that fits the height.
        assert len(blocker_cells(8, 4, 25, 1)) == 400   # checkerboard, half the cells
        assert len(blocker_cells(8, 4, 25, 2)) == 192   # flat 4 SCU lattice
        assert len(blocker_cells(8, 4, 25, 4)) == 96    # 2x2x2 lattice
        assert len(blocker_cells(8, 4, 25, 8)) == 48    # 16 SCU, three axes
        assert len(blocker_cells(8, 4, 25, 16)) == 32   # 24 SCU, long side in x and z
        assert len(blocker_cells(8, 4, 25, 24)) == 24   # 32 SCU, long side in x and z

    def test_line_bound_is_not_always_achievable(self):
        # Disjoint copies suggest 8 cells on 8x3x15 and 16 on 8x4x15. A line
        # of 7 cannot be covered by two cells and still hit the short side,
        # so the minima are 9 and 18.
        assert len(blocker_cells(8, 3, 15, 16)) == 9
        assert len(blocker_cells(8, 4, 15, 16)) == 18


class TestPacker:
    GRIDS = [(4, 2, 4), (6, 2, 8), (5, 3, 7), (8, 3, 15), (8, 4, 25), (8, 8, 8)]

    @pytest.mark.parametrize("w,h,l", GRIDS)
    @pytest.mark.parametrize("cap", CONTAINER_SIZES)
    def test_nothing_larger_fits_and_the_cap_still_does(self, w, h, l, cap):
        cells = blocker_cells(w, h, l, cap)
        assert cells == blocker_cells(w, h, l, cap)
        for x, y, z in cells:
            assert 0 <= x < w and 0 <= y < h and 0 <= z < l
        got = fill_slot_around(_slot(w, h, l), set(cells))
        assert all(size <= cap for size in got)
        if _fits(cap, w, h, l):
            assert got.get(cap, 0) >= 1

    def test_slot_max_still_stops_a_bigger_box(self):
        # The grid itself would take a 32. Capping at 8, and telling the solver
        # the grid only goes to 16, still leaves no room for a 32: every 32
        # contains a 16.
        cells = blocker_cells(8, 4, 16, 8, slot_max=16)
        assert not _fits(32, 8, 4, 16, cells)
        assert not _fits(16, 8, 4, 16, cells)
        assert _fits(8, 8, 4, 16, cells)

    @pytest.mark.parametrize("w,h,l,cap", [
        (4, 2, 4, 4),
        (4, 2, 4, 2),
        (3, 2, 4, 1),
        (8, 3, 15, 16),
        (2, 2, 2, 1),
    ])
    def test_every_cell_is_needed(self, w, h, l, cap):
        cells = blocker_cells(w, h, l, cap)
        assert cells, "this grid can hold something above the cap"
        assert not _larger_fits(w, h, l, cap, cells)
        for drop in cells:
            kept = [c for c in cells if c != drop]
            assert _larger_fits(w, h, l, cap, kept)
