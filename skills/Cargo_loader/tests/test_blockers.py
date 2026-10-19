"""Minimum 1 SCU cells that stop Auto placing anything above a chosen size."""

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')))
import shared.path_setup  # noqa: E402
shared.path_setup.ensure_path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from cargo_engine.blockers import blocker_cells  # noqa: E402
from cargo_engine.packing import fill_slot_around, place_containers_3d, set_long_side_only  # noqa: E402
from cargo_engine.schema import CONTAINER_SIZES  # noqa: E402


@pytest.fixture(autouse=True)
def _size_cap_on():
    """These tests describe the size cap, so the flat-container rule is on."""
    set_long_side_only(True)
    yield


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

    def test_8scu_sits_on_the_floor_of_a_short_grid(self):
        # Cap 4 blocks the 2x2x2 cube. On a 2-high grid that cube stands on
        # the floor, so the floor cell of each window is the whole blocker.
        assert blocker_cells(4, 2, 4, 4) == [
            (1, 0, 1), (1, 0, 3), (3, 0, 1), (3, 0, 3),
        ]

    def test_400i_cap_4_marks_three_floor_boxes(self):
        # The 400i's 3x2x7 hold. Three floor boxes, not three boxes in the air.
        assert blocker_cells(3, 2, 7, 4) == [(1, 0, 1), (1, 0, 3), (1, 0, 5)]

    def test_tall_grid_keeps_the_column_under_an_upper_blocker(self):
        # Height 4. An 8 SCU can sit on the upper layers, so a blocker stays
        # at y=2, and y=0 and y=1 under it are filled. The very top is spare.
        assert blocker_cells(4, 4, 4, 4) == [
            (1, 0, 1), (1, 0, 3), (1, 1, 1), (1, 1, 3), (1, 2, 1), (1, 2, 3),
            (3, 0, 1), (3, 0, 3), (3, 1, 1), (3, 1, 3), (3, 2, 1), (3, 2, 3),
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
        # Flat boxes (cap 1 and 2) already mark every layer. Taller boxes gain
        # the cells under the upper blockers, and a spare top cell is dropped.
        assert len(blocker_cells(8, 4, 25, 1)) == 400   # checkerboard, half the cells
        assert len(blocker_cells(8, 4, 25, 2)) == 192   # flat 4 SCU lattice
        assert len(blocker_cells(8, 4, 25, 4)) == 144   # 2x2x2, columns of height 3
        assert len(blocker_cells(8, 4, 25, 8)) == 72    # 16 SCU lies flat, so the column stops at y=2
        assert len(blocker_cells(8, 4, 25, 16)) == 48   # 24 SCU
        assert len(blocker_cells(8, 4, 25, 24)) == 36   # 32 SCU

    def test_line_bound_is_not_always_achievable(self):
        # The cover itself is 9 columns on 8x3x15 and 9 on 8x4x15: a line of 7
        # cannot be covered by two cells and still hit the short side. Each
        # column is then filled down to the floor, and a spare top cell on the
        # taller grid is dropped. Height 3 keeps 2 cells per column; height 4
        # keeps 3.
        assert len(blocker_cells(8, 3, 15, 16)) == 18
        assert len(blocker_cells(8, 4, 15, 16)) == 27


class TestPacker:
    GRIDS = [
        (3, 2, 7), (4, 2, 4), (4, 4, 4), (6, 2, 8),
        (5, 3, 7), (8, 3, 15), (8, 4, 25), (8, 8, 8),
    ]

    @pytest.mark.parametrize("w,h,l", GRIDS)
    @pytest.mark.parametrize("cap", CONTAINER_SIZES)
    def test_nothing_larger_fits_and_the_cap_still_does(self, w, h, l, cap):
        cells = blocker_cells(w, h, l, cap)
        assert cells == blocker_cells(w, h, l, cap)
        for x, y, z in cells:
            assert 0 <= x < w and 0 <= y < h and 0 <= z < l
        occupied = set(cells)
        assert all(y == 0 or (x, y - 1, z) in occupied for x, y, z in occupied)
        got = fill_slot_around(_slot(w, h, l), occupied)
        assert all(size <= cap for size in got)
        assert len(cells) + sum(size * n for size, n in got.items()) == w * h * l

    def test_slot_max_still_stops_a_bigger_box(self):
        # The grid itself would take a 32. Capping at 8, and telling the solver
        # the grid only goes to 16, still leaves no room for a 32: every 32
        # contains a 16.
        cells = blocker_cells(8, 4, 16, 8, slot_max=16)
        assert not _fits(32, 8, 4, 16, cells)
        assert not _fits(16, 8, 4, 16, cells)
        got = fill_slot_around(_slot(8, 4, 16, slot_max=16), set(cells))
        assert all(size <= 8 for size in got)
        assert len(cells) + sum(size * n for size, n in got.items()) == 8 * 4 * 16

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
        occupied = set(cells)
        assert not _larger_fits(w, h, l, cap, cells)
        for drop in cells:
            kept = occupied - {drop}
            if (drop[0], drop[1] + 1, drop[2]) in occupied:
                assert any(
                    y > 0 and (x, y - 1, z) not in kept for x, y, z in kept
                )
            else:
                assert _larger_fits(w, h, l, cap, kept)
