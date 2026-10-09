"""Entity-level CONTROL / CHECK partition and spatial bookkeeping.

Hard requirements enforced and test-enforced here:

  * The split happens BEFORE any hypothesis is generated. The matrix stage
    calls this once and then never lets CHECK reach a fitting or ranking step.
  * Partition is at ENTITY granularity. A single entity is never divided; its
    vertices are not individual evidence. Dividing vertices inflated
    independence in an earlier stage and produced CHECK results that collapsed
    relative to CONTROL.
  * Both pools span the occupied cells of a grid over the drawing, so neither
    pool is a single local cluster. The partition order is by grid cell, then
    alternate.
  * Deterministic: the same input always yields the same partition. A seed
    exists for future randomised strategies and is recorded in the output, but
    the default strategy consumes no randomness at all.

A Set B2 defect is specifically avoided: an earlier version took
`control[:250]` as a "spatial sample" for selectivity calibration. Because the
control list is cell-ordered, that prefix is the top quarter of the sheet, not
a representative sample. `spread_sample` is the correct tool for subsampling
and is used by the selectivity stage.
"""

import math
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import ControlCheckSplit, GeometryEntity

DEFAULT_CELLS = 8


def _entity_centroid(entity: GeometryEntity) -> Optional[Tuple[float, float]]:
    if not entity.has_segments:
        return None
    n = 2 * len(entity.segments)
    sx = 0.0
    sy = 0.0
    for a, b in entity.segments:
        sx += a[0] + b[0]
        sy += a[1] + b[1]
    return (sx / n, sy / n)


def entity_cell(entity: GeometryEntity, bounds: Tuple[float, float, float,
                                                        float],
                n_cells: int) -> Optional[Tuple[int, int]]:
    c = _entity_centroid(entity)
    if c is None:
        return None
    x0, x1, y0, y1 = bounds
    dx = (x1 - x0) / n_cells if x1 > x0 else 1.0
    dy = (y1 - y0) / n_cells if y1 > y0 else 1.0
    gx = int((c[0] - x0) / dx)
    gy = int((c[1] - y0) / dy)
    gx = 0 if gx < 0 else (n_cells - 1 if gx >= n_cells else gx)
    gy = 0 if gy < 0 else (n_cells - 1 if gy >= n_cells else gy)
    return gy, gx


def entity_bounds(entities: Sequence[GeometryEntity]
                  ) -> Optional[Tuple[float, float, float, float]]:
    xs: List[float] = []
    ys: List[float] = []
    for e in entities:
        bb = e.bbox()
        if bb is None:
            continue
        xs.extend((bb[0], bb[1]))
        ys.extend((bb[2], bb[3]))
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def split_control_check(entities: Sequence[GeometryEntity],
                        n_cells: int = DEFAULT_CELLS,
                        seed: int = 0) -> ControlCheckSplit:
    """Balanced, spatially distributed, entity-level partition.

    Entities without segment geometry (POINT, TEXT anchors) cannot be placed
    in a cell and are assigned to CONTROL by entity id order. They still never
    appear in both pools.
    """
    placeable = [e for e in entities if e.has_segments]
    others = [e for e in entities if not e.has_segments]
    bounds = entity_bounds(placeable)

    control: List[GeometryEntity] = []
    check: List[GeometryEntity] = []

    if bounds is not None and placeable:
        keyed = []
        for e in placeable:
            cell = entity_cell(e, bounds, n_cells)
            keyed.append((cell[0] if cell else -1,
                          cell[1] if cell else -1, e.entity_id, e))
        keyed.sort(key=lambda t: (t[0], t[1], t[2]))
        for i, item in enumerate(keyed):
            (control if i % 2 == 0 else check).append(item[3])
    else:
        control = list(placeable)

    others.sort(key=lambda e: e.entity_id)
    for i, e in enumerate(others):
        (control if i % 2 == 0 else check).append(e)

    split = ControlCheckSplit(control=control, check=check, n_cells=n_cells,
                              seed=seed)
    split.verify(list(entities))
    return split


def spread_sample(items: Sequence, k: int) -> List:
    """Evenly spaced subsample of an arbitrary sequence.

    Correct tool for 'take a spatially representative subset'. Not a filter:
    the result spans the whole input range, so a cell-ordered list yields
    coverage of the whole sheet rather than its first rows.
    """
    n = len(items)
    if k <= 0 or n == 0:
        return []
    if n <= k:
        return list(items)
    step = n / float(k)
    return [items[int(i * step)] for i in range(k)]


def cell_occupancy(entities: Sequence[GeometryEntity], n_cells: int = DEFAULT_CELLS,
                   bounds: Optional[Tuple[float, float, float, float]] = None
                   ) -> Counter:
    """Cell -> entity count, over a grid on the entities' own bounds."""
    placeable = [e for e in entities if e.has_segments]
    bounds = bounds or entity_bounds(placeable)
    occ: Counter = Counter()
    if bounds is None:
        return occ
    for e in placeable:
        cell = entity_cell(e, bounds, n_cells)
        if cell is not None:
            occ[cell] += 1
    return occ


def cell_coverage(all_entities: Sequence[GeometryEntity],
                  matched_entities: Sequence[GeometryEntity],
                  n_cells: int = DEFAULT_CELLS) -> Tuple[int, int]:
    """(cells with at least one match, occupied cells in the whole set)."""
    bounds = entity_bounds(all_entities)
    if bounds is None:
        return (0, 0)
    occ_all = cell_occupancy(all_entities, n_cells, bounds)
    if not occ_all:
        return (0, 0)
    occ_matched = cell_occupancy(matched_entities, n_cells, bounds)
    filled = sum(1 for c in occ_all if occ_matched.get(c, 0) > 0)
    return (filled, len(occ_all))


def unmatched_cell_report(all_entities: Sequence[GeometryEntity],
                          matched_ids: Iterable[str],
                          n_cells: int = DEFAULT_CELLS) -> str:
    """Human-readable per-cell unmatched/total table, in CAD units."""
    placeable = [e for e in all_entities if e.has_segments]
    bounds = entity_bounds(placeable)
    if bounds is None:
        return ""
    matched = set(matched_ids)
    occ_all = cell_occupancy(placeable, n_cells)
    un: Counter = Counter()
    for e in placeable:
        if e.entity_id in matched:
            continue
        c = entity_cell(e, bounds, n_cells)
        if c is not None:
            un[c] += 1
    x0, x1, y0, y1 = bounds
    dx = (x1 - x0) / n_cells if x1 > x0 else 1.0
    dy = (y1 - y0) / n_cells if y1 > y0 else 1.0
    parts = []
    for cell in sorted(occ_all):
        gy, gx = cell
        parts.append("%d/%d" % (un.get(cell, 0), occ_all[cell]))
    return "cells(unmatched/total): " + " ".join(parts)


def layer_stats(all_entities: Sequence[GeometryEntity],
                matched_ids: Iterable[str]
                ) -> Tuple[Dict[str, List[int]], Optional[str], Optional[float]]:
    """Per-layer matched/total, plus the single most productive layer.

    `top_layer_share` is the fraction of all matched entities that fall in one
    layer. It is the measured quantity that separates a genuine correspondence
    from a repeated-geometry artefact, where nearly every apparent match lands
    in one repetitive layer.
    """
    matched = set(matched_ids)
    per: Counter = Counter()
    tot: Counter = Counter()
    for e in all_entities:
        name = e.layer or "<none>"
        tot[name] += 1
        if e.entity_id in matched:
            per[name] += 1
    table = {name: [per[name], tot[name]] for name in tot}
    n_matched = sum(per.values())
    if n_matched == 0:
        return table, None, None
    top = per.most_common(1)[0]
    return table, top[0], top[1] / float(n_matched)


def type_stats(all_entities: Sequence[GeometryEntity],
               matched_ids: Iterable[str]
               ) -> Tuple[Dict[str, List[int]], int, int]:
    """Per-type matched/total plus counts of matched and offered types."""
    matched = set(matched_ids)
    per: Counter = Counter()
    tot: Counter = Counter()
    for e in all_entities:
        tot[e.entity_type] += 1
        if e.entity_id in matched:
            per[e.entity_type] += 1
    table = {t: [per[t], tot[t]] for t in tot}
    return table, sum(1 for t in tot if per[t] > 0), len(tot)
