"""Fail-closed eligibility and RFC v2 area-first candidate ranking.

The ranked area is ``routed_core_area_um2``: allocated floorplan core area,
including placement whitespace, required physical cells, PDN, and routing
channels. It is distinct from a router sandbox allocation or ``A_cell``.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


_REQUIRED_TRUE_FIELDS = (
    "process_legal",
    "functional_correctness",
    "strict_drc",
    "strict_lvs",
    "pex",
    "postlayout",
    "route_connectivity",
    "power_connectivity",
    "pin_access",
    "routed_core_area_complete",
)
_ROUTING_COST_FIELDS = (
    "crossing_count",
    "longest_detour",
    "pin_conflicts",
    "search_difficulty",
)
_PHYSICAL_COST_FIELDS = (
    "local_resistance_ohm_proxy",
    "contact_count",
    "power_cost",
)


def _number(value: Any, *, positive: bool = False, integer: bool = False) -> tuple[int | float | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "not_a_number"
    number = float(value)
    if not math.isfinite(number):
        return None, "not_finite"
    if positive and number <= 0:
        return None, "must_be_positive"
    if not positive and number < 0:
        return None, "must_be_nonnegative"
    if integer:
        if not number.is_integer():
            return None, "must_be_an_integer_count"
        return int(number), None
    return number, None


def _required_number(record: Mapping[str, Any], field: str, *, positive: bool = False,
                     integer: bool = False) -> tuple[int | float | None, str | None]:
    if field not in record or record[field] is None:
        return None, "missing_or_null"
    return _number(record[field], positive=positive, integer=integer)


def _optional_cost(record: Mapping[str, Any], field: str, *, integer: bool = False) -> tuple[int | float | None, str | None]:
    if field not in record or record[field] is None:
        return None, "unmeasured"
    value, error = _number(record[field], integer=integer)
    return (value, None) if error is None else (None, error)


def _validate_limit(value: Any, name: str, *, positive: bool) -> float:
    number, error = _number(value, positive=positive)
    if error is not None:
        qualifier = "finite positive" if positive else "finite nonnegative"
        raise ValueError(f"{name} must be a {qualifier} number")
    return float(number)


def assess_candidate(record: Mapping[str, Any], target_clock_period_ns: float | None = None,
                     core_available_area_um2: float | None = None) -> dict[str, Any]:
    """Return an evidence assessment without mutating or promoting the record.

    RFC v2 record contract (all booleans must be the literal JSON/Python
    ``true``): ``process_legal``, ``functional_correctness``, ``strict_drc``,
    ``strict_lvs``, ``pex``, ``postlayout``, ``route_connectivity``,
    ``power_connectivity``, ``pin_access``, and ``routed_core_area_complete``.
    The latter asserts that finite positive ``routed_core_area_um2`` is the
    allocated core including required physical cells, PDN, and routing channels.
    ``overflow_count`` and ``short_count`` must be measured integer counts of
    exactly zero. ``storage_present`` must be an explicit boolean; when true,
    ``preservation_correctness``, ``pulse_correctness``, and
    ``hold_correctness`` are also required literal ``true``. A supplied capacity
    limit is an inclusive upper bound on routed-core area.
    Frame-targeted experiments additionally set ``require_frame_polygon_fit``;
    literal true then requires ``frame_polygon_fit`` from exact containment,
    not merely a scalar area comparison. This does not establish frame IO/power.

    Optional routing costs are ``crossing_count``, ``longest_detour``,
    ``pin_conflicts``, and ``search_difficulty``; optional physical costs are
    ``local_resistance_ohm_proxy``, ``contact_count``, and ``power_cost``.
    These finite nonnegative values are ordered when measured; missing, null,
    or invalid values remain explicitly unmeasured and sort after measured
    values without making an otherwise verified candidate ineligible. They are
    not fabricated zeros or hard pass criteria. When a clock target is given,
    at least one of ``clock_period_ns`` (no greater than the target) or
    ``slack_ns`` (nonnegative against that target) is required; if both are
    supplied, both must pass. Without a target, timing is ignored and never
    ranks candidates.

    ``reasons`` contains only failed eligibility gates. ``unmeasured_costs``
    and ``cost_status`` preserve missing optional metrics separately.
    """
    if target_clock_period_ns is not None:
        target = _validate_limit(target_clock_period_ns, "target_clock_period_ns", positive=True)
    else:
        target = None
    if core_available_area_um2 is not None:
        capacity = _validate_limit(core_available_area_um2, "core_available_area_um2", positive=False)
    else:
        capacity = None

    if not isinstance(record, Mapping):
        return {
            "eligible": False,
            "reasons": ["record:must_be_a_mapping"],
            "unmeasured_costs": list(_ROUTING_COST_FIELDS + _PHYSICAL_COST_FIELDS),
            "cost_status": {},
            "sort_key": None,
            "timing_used": target is not None,
        }

    reasons: list[str] = []
    for field in _REQUIRED_TRUE_FIELDS:
        if record.get(field) is not True:
            reasons.append(f"{field}:must_be_true")

    storage_present = record.get("storage_present")
    if type(storage_present) is not bool:
        reasons.append("storage_present:must_be_boolean")
    elif storage_present:
        for field in ("preservation_correctness", "pulse_correctness", "hold_correctness"):
            if record.get(field) is not True:
                reasons.append(f"{field}:must_be_true_for_storage")

    requires_frame = record.get("require_frame_polygon_fit", False)
    if type(requires_frame) is not bool:
        reasons.append("require_frame_polygon_fit:must_be_boolean")
    elif requires_frame and record.get("frame_polygon_fit") is not True:
        reasons.append("frame_polygon_fit:must_be_true_when_required")

    area, area_error = _required_number(record, "routed_core_area_um2", positive=True)
    if area_error is not None:
        reasons.append(f"routed_core_area_um2:{area_error}")
    elif capacity is not None and float(area) > capacity:
        reasons.append("routed_core_area_um2:exceeds_core_available_area")

    hard_counts: dict[str, int | None] = {}
    for field in ("overflow_count", "short_count"):
        value, error = _required_number(record, field, integer=True)
        hard_counts[field] = value if error is None else None
        if error is not None:
            reasons.append(f"{field}:{error}")
        elif value != 0:
            reasons.append(f"{field}:must_be_zero")

    if target is not None:
        timing_values = {field: record[field] for field in ("clock_period_ns", "slack_ns")
                         if field in record}
        valid_timing_count = 0
        if "clock_period_ns" in timing_values:
            period, error = _required_number(record, "clock_period_ns", positive=True)
            if error is not None:
                reasons.append(f"clock_period_ns:{error}")
            else:
                valid_timing_count += 1
                if float(period) > target:
                    reasons.append("clock_period_ns:exceeds_target")
        if "slack_ns" in timing_values:
            slack, error = _required_number(record, "slack_ns")
            if error is not None:
                reasons.append(f"slack_ns:{error}")
            else:
                valid_timing_count += 1
                if float(slack) < 0:
                    reasons.append("slack_ns:negative_for_target")
        if valid_timing_count == 0:
            reasons.append("timing:missing_for_target")

    costs: dict[str, int | float | None] = {}
    cost_status: dict[str, dict[str, Any]] = {}
    unmeasured_costs = []
    for field in _ROUTING_COST_FIELDS + _PHYSICAL_COST_FIELDS:
        value, status = _optional_cost(record, field, integer=field in {"crossing_count", "pin_conflicts", "contact_count"})
        costs[field] = value
        if status is None:
            cost_status[field] = {"measured": True, "value": value}
        else:
            unmeasured_costs.append(field)
            cost_status[field] = {"measured": False, "reason": status}

    sort_key = None
    if area_error is None and hard_counts["overflow_count"] is not None:
        sort_key = [float(area), int(hard_counts["overflow_count"])] + [
            costs[field] for field in _ROUTING_COST_FIELDS + _PHYSICAL_COST_FIELDS
        ]

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "unmeasured_costs": unmeasured_costs,
        "cost_status": cost_status,
        "sort_key": sort_key,
        "timing_used": target is not None,
        "target_clock_period_ns": target,
        "capacity_area_um2": capacity,
    }


def _ranking_key(assessment: Mapping[str, Any]) -> tuple[Any, ...]:
    """Order None cost slots after real measurements while preserving metric precedence."""
    values = assessment["sort_key"]
    # Eligibility ensures area and overflow have values.  The following cost
    # slots use a separate measured marker, never a report-side substitute zero.
    return (values[0], values[1]) + tuple(
        (value is None, 0 if value is None else value) for value in values[2:]
    )


def rank_candidates(records: Sequence[Mapping[str, Any]],
                    target_clock_period_ns: float | None = None,
                    core_available_area_um2: float | None = None) -> list[Mapping[str, Any]]:
    """Return eligible input records sorted by RFC v2 area-first precedence.

    The record contract and hard gates are defined in :func:`assess_candidate`.
    Ordering is routed-core area first; then zero overflow, crossings, longest
    detour, pin conflicts, search difficulty; then local resistance proxy,
    contact count, and power cost. Missing optional cost metrics sort last.
    Timing is a hard feasibility filter only when a target is supplied and is
    never a ranking key. Ties retain input order for deterministic results.
    """
    ranked = []
    for record in records:
        assessment = assess_candidate(record, target_clock_period_ns,
                                      core_available_area_um2)
        if assessment["eligible"]:
            ranked.append((record, assessment))
    ranked.sort(key=lambda item: _ranking_key(item[1]))
    return [record for record, _ in ranked]
