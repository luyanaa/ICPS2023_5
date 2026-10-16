#!/usr/bin/env python3
"""Deterministic tests for RFC v2 candidate eligibility and ordering."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import metrics


HARD_TRUE_FIELDS = (
    "process_legal", "functional_correctness", "strict_drc", "strict_lvs", "pex",
    "postlayout", "route_connectivity", "power_connectivity", "pin_access",
    "routed_core_area_complete",
)


def _candidate(name: str, *, area: float = 100.0, **overrides):
    record = {field: True for field in HARD_TRUE_FIELDS}
    record.update({
        "name": name,
        "storage_present": False,
        "routed_core_area_um2": area,
        "overflow_count": 0,
        "short_count": 0,
        "crossing_count": 2,
        "longest_detour": 10.0,
        "pin_conflicts": 0,
        "search_difficulty": 1.0,
        "local_resistance_ohm_proxy": 5.0,
        "contact_count": 10,
        "power_cost": 2.0,
    })
    record.update(overrides)
    return record


class CandidateMetricsTests(unittest.TestCase):
    def test_missing_null_and_false_hard_evidence_fail_closed(self):
        for field in HARD_TRUE_FIELDS:
            candidate = _candidate("incomplete")
            candidate[field] = None
            assessment = metrics.assess_candidate(candidate)
            self.assertFalse(assessment["eligible"], field)
            self.assertIn(f"{field}:must_be_true", assessment["reasons"])

        candidate = _candidate("missing-area")
        candidate.pop("routed_core_area_um2")
        self.assertIn("routed_core_area_um2:missing_or_null",
                      metrics.assess_candidate(candidate)["reasons"])
        for area in (0.0, -1.0, float("nan"), float("inf"), True):
            candidate = _candidate("invalid-area", area=area)
            self.assertFalse(metrics.assess_candidate(candidate)["eligible"], area)
            self.assertTrue(any(reason.startswith("routed_core_area_um2:")
                                for reason in metrics.assess_candidate(candidate)["reasons"]))


        candidate = _candidate("failed-process", process_legal=False)
        self.assertIn("process_legal:must_be_true", metrics.assess_candidate(candidate)["reasons"])

    def test_overflow_shorts_capacity_and_storage_are_hard_gates(self):
        for field in ("overflow_count", "short_count"):
            candidate = _candidate("bad-count", **{field: 1})
            self.assertIn(f"{field}:must_be_zero", metrics.assess_candidate(candidate)["reasons"])

        candidate = _candidate("unknown-overflow", overflow_count=None)
        self.assertIn("overflow_count:missing_or_null", metrics.assess_candidate(candidate)["reasons"])

        candidate = _candidate("over-capacity", area=101.0)
        self.assertIn("routed_core_area_um2:exceeds_core_available_area",
                      metrics.assess_candidate(candidate, core_available_area_um2=100.0)["reasons"])
        self.assertTrue(metrics.assess_candidate(_candidate("at-capacity", area=100.0),
                                                 core_available_area_um2=100.0)["eligible"])

        storage = _candidate("storage", storage_present=True)
        self.assertFalse(metrics.assess_candidate(storage)["eligible"])
        storage.update({"preservation_correctness": True, "pulse_correctness": True,
                        "hold_correctness": True})
        self.assertTrue(metrics.assess_candidate(storage)["eligible"])
        storage["hold_correctness"] = None
        self.assertIn("hold_correctness:must_be_true_for_storage",
                      metrics.assess_candidate(storage)["reasons"])

    def test_frame_target_requires_polygon_fit_not_just_sufficient_area(self):
        candidate = _candidate("small-but-unplaceable", area=50.0,
                               require_frame_polygon_fit=True, frame_polygon_fit=False)
        self.assertIn("frame_polygon_fit:must_be_true_when_required",
                      metrics.assess_candidate(candidate, core_available_area_um2=100.0)["reasons"])
        self.assertEqual(metrics.rank_candidates([candidate], core_available_area_um2=100.0), [])
        candidate["frame_polygon_fit"] = True
        self.assertTrue(metrics.assess_candidate(candidate, core_available_area_um2=100.0)["eligible"])
        candidate["require_frame_polygon_fit"] = "true"
        self.assertFalse(metrics.assess_candidate(candidate)["eligible"])

    def test_area_precedes_routing_and_tertiary_costs(self):
        lower_area = _candidate("area-first", area=90.0, crossing_count=99,
                                local_resistance_ohm_proxy=99.0)
        better_routing = _candidate("route-second", area=100.0, crossing_count=0,
                                     local_resistance_ohm_proxy=0.0)
        lower_crossings = _candidate("few-crossings", crossing_count=1,
                                     local_resistance_ohm_proxy=99.0)
        lower_resistance = _candidate("low-resistance", crossing_count=1,
                                       local_resistance_ohm_proxy=1.0)
        ranked = metrics.rank_candidates([better_routing, lower_area, lower_crossings,
                                          lower_resistance])
        self.assertEqual([record["name"] for record in ranked],
                         ["area-first", "route-second", "low-resistance", "few-crossings"])


    def test_routing_and_physical_cost_tuple_precedence(self):
        zero = {
            "crossing_count": 0,
            "longest_detour": 0.0,
            "pin_conflicts": 0,
            "search_difficulty": 0.0,
            "local_resistance_ohm_proxy": 0.0,
            "contact_count": 0,
            "power_cost": 0.0,
        }
        candidates = [_candidate("best", **zero)]
        for field in ("crossing_count", "longest_detour", "pin_conflicts", "search_difficulty",
                      "local_resistance_ohm_proxy", "contact_count", "power_cost"):
            costs = dict(zero)
            costs[field] = 1
            candidates.append(_candidate(field, **costs))
        ranked = metrics.rank_candidates(list(reversed(candidates)))
        self.assertEqual([record["name"] for record in ranked],
                         ["best", "power_cost", "contact_count",
                          "local_resistance_ohm_proxy", "search_difficulty",
                          "pin_conflicts", "longest_detour", "crossing_count"])

    def test_unmeasured_costs_are_preserved_and_sort_after_measured_values(self):
        measured = _candidate("measured")
        unknown = _candidate("unknown")
        for field in ("crossing_count", "longest_detour", "pin_conflicts", "search_difficulty",
                      "local_resistance_ohm_proxy", "contact_count", "power_cost"):
            unknown[field] = None
        assessment = metrics.assess_candidate(unknown)
        self.assertTrue(assessment["eligible"])
        self.assertEqual(set(assessment["unmeasured_costs"]), {
            "crossing_count", "longest_detour", "pin_conflicts", "search_difficulty",
            "local_resistance_ohm_proxy", "contact_count", "power_cost",
        })
        self.assertIsNone(assessment["sort_key"][2])
        self.assertEqual([record["name"] for record in metrics.rank_candidates([unknown, measured])],
                         ["measured", "unknown"])

    def test_timing_is_only_a_targeted_feasibility_filter(self):
        slow = _candidate("slow", clock_period_ns=20.0, slack_ns=-5.0)
        fast = _candidate("fast", clock_period_ns=5.0, slack_ns=5.0)
        self.assertEqual([record["name"] for record in metrics.rank_candidates([slow, fast])],
                         ["slow", "fast"])
        self.assertEqual([record["name"] for record in metrics.rank_candidates(
            [slow, fast], target_clock_period_ns=10.0)], ["fast"])

        missing = _candidate("no-clock")
        self.assertIn("timing:missing_for_target",
                      metrics.assess_candidate(missing, target_clock_period_ns=10.0)["reasons"])
        negative_slack = _candidate("bad-slack", slack_ns=-0.01)
        self.assertFalse(metrics.assess_candidate(negative_slack,
                                                  target_clock_period_ns=10.0)["eligible"])
        exceeded = _candidate("slow-period", clock_period_ns=10.01)
        self.assertIn("clock_period_ns:exceeds_target",
                      metrics.assess_candidate(exceeded, target_clock_period_ns=10.0)["reasons"])

    def test_invalid_area_and_target_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            metrics.assess_candidate(_candidate("x"), target_clock_period_ns=0)
        with self.assertRaises(ValueError):
            metrics.assess_candidate(_candidate("x"), core_available_area_um2=-1)


if __name__ == "__main__":
    unittest.main()
