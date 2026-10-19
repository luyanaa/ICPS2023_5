#!/usr/bin/env python3
"""Analytic and fault regressions for terminal-voltage/thermal screening."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import analyze_voltage_thermal as analysis


TOOLS = Path(__file__).resolve().parent


class VoltageThermalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return path

    def network(self, mos=True):
        if mos:
            text = ".SUBCKT TOP D G S E P A B\nM1 D G S E nch\nM2 D G S E P nch\nR1 A B 10\n.ENDS TOP\n"
        else:
            text = ".SUBCKT TOP A B\nR1 A B 10\n.ENDS TOP\n"
        return self.write("network.sp", text)

    def thermal_config(self, network, source, **overrides):
        thermal = {
            "Rth_k_per_w": 2.0,
            "Cth_j_per_k": 5.0,
            "ambient_k": 300.0,
            "resistance_tempco_per_k": 0.0,
            "load_scale": [1.0],
            "window_s": [0.0, 2.0],
            "initial_delta_t_k": 0.0,
            "max_step_s": 0.01,
            "power_ownership": {
                "external_mos_devices": [],
                "internal_selfheat_devices": [],
                "excluded_devices": [],
                "shmod_by_device": {},
            },
        }
        thermal.update(overrides)
        return {
            "network": {"path": str(network), "top": "TOP"},
            "scenarios": [], "thermal_input": source, "thermal": thermal,
        }

    def branch_source(self, times, currents, resistance=10.0, device_power=None):
        return {
            "times_s": times,
            "branches": [{"name": "R1", "material": "TiN", "resistance_ohm": resistance,
                          "current_a": currents}],
            "device_power_w": device_power,
        }

    def test_terminal_polarity_limits_and_missing_backgate_are_explicit(self):
        network = self.network()
        csv_path = self.write(
            "terminals.csv",
            "Time_S,d_node,G_NODE,s_node,E_NODE,p_node\n"
            "0,4,1,1,-1,7\n"
            "1,4,3,1,-1,8\n"
            "2,4,1,1,-1,9\n",
        )
        config = {
            "network": {"path": str(network), "top": "TOP"},
            "scenarios": [{
                "name": "async-supply-up-loaded-pad-overshoot",
                "csv": str(csv_path), "time_column": "time_s", "window_s": [0, 2],
                "column_map": {"d": "D_NODE", "g": "g_node", "S": "s_NODE",
                               "p": "P_NODE"},
            }],
            "limits": {"vgs": {"max_v": 1.0, "source": "explicit test limit", "qualified": True}},
        }
        result = analysis.analyze(config)
        devices = result["scenarios"][0]["devices"]
        m1, m2 = devices
        vgs = m1["metrics"]["VGS"]
        self.assertEqual(vgs["history"]["voltage_v"], [0.0, 2.0, 0.0])
        self.assertEqual(vgs["limit_status"], "FAIL")
        self.assertAlmostEqual(vgs["outside_duration_s"], 1.0)
        self.assertEqual(vgs["violations"], [{"start_s": 0.5, "end_s": 1.5, "duration_s": 1.0}])
        self.assertEqual(m1["metrics"]["VGE"]["status"], "UNASSESSED")
        self.assertNotIn("VPS", m1["metrics"])
        self.assertEqual(m2["metrics"]["VPE"]["status"], "UNASSESSED")
        self.assertTrue(result["scenarios"][0]["coverage"]["full_window_coverage"])
        self.assertEqual(result["manufacturing_acceptance"].split(";")[0], "BLOCKED")

    def test_voltage_limit_reports_partial_coverage_not_full_duration(self):
        network = self.network(mos=False)
        csv_path = self.write("partial.csv", "t,a\n1,0\n2,2\n")
        config = {
            "network": {"path": str(network), "top": "TOP"},
            "scenarios": [{"name": "partial", "csv": str(csv_path), "time_column": "t",
                           "window_s": [0, 2], "column_map": {"A": "a"}}],
        }
        result = analysis.analyze(config)
        coverage = result["scenarios"][0]["coverage"]
        self.assertFalse(coverage["full_window_coverage"])
        self.assertEqual(coverage["covered_window_s"], [1.0, 2.0])

    def test_constant_load_matches_analytic_steady_and_transient(self):
        network = self.network(mos=False)
        source = self.branch_source([0.0, 1.0, 2.0], [1.0, 1.0, 1.0])
        result = analysis.analyze(self.thermal_config(network, source))
        thermal = result["thermal"]
        self.assertEqual(thermal["status"], "ASSESSED")
        response = thermal["results"][0]
        expected = 20.0 * (1.0 - math.exp(-2.0 / 10.0))
        self.assertAlmostEqual(response["response"]["delta_t_k"][-1], expected, places=7)
        self.assertAlmostEqual(response["steady_delta_t_k"], 20.0)
        self.assertAlmostEqual(response["energy_j"]["resistor_i2r"], 20.0)


    def test_explicit_thermal_sensitivity_grid_covers_all_declared_inputs(self):
        network = self.network(mos=False)
        source = self.branch_source([0.0, 1.0, 2.0], [1.0, 1.0, 1.0])
        config = self.thermal_config(
            network, source, Rth_k_per_w=[2.0, 4.0], Cth_j_per_k=[5.0, 10.0],
            ambient_k=[300.0, 310.0], resistance_tempco_per_k=[0.0, 0.001],
            load_scale=[0.0, 1.0],
        )
        report = analysis.analyze(config)["thermal"]
        self.assertEqual(len(report["results"]), 32)
        baseline = next(item for item in report["results"]
                        if item["parameters"]["Rth_k_per_w"] == 2.0
                        and item["parameters"]["Cth_j_per_k"] == 10.0
                        and item["parameters"]["ambient_k"] == 300.0
                        and item["parameters"]["resistance_tempco_per_k"] == 0.0
                        and item["parameters"]["load_scale"] == 1.0)
        expected = 20.0 * (1.0 - math.exp(-2.0 / 20.0))
        self.assertAlmostEqual(baseline["response"]["delta_t_k"][-1], expected, places=7)
        self.assertEqual(baseline["response"]["temperature_k"][-1],
                         300.0 + baseline["response"]["delta_t_k"][-1])

    def test_static_branch_stress_has_no_fabricated_thermal_history(self):
        network = self.network(mos=False)
        source = self.branch_source([0.0], [0.001], resistance=75.0)
        config = self.thermal_config(network, source, window_s=[0.0, 1.0])
        report = analysis.analyze(config)["thermal"]
        self.assertEqual(report["status"], "UNASSESSED")
    def test_pulse_accumulation_and_cooldown_are_integrated(self):
        network = self.network(mos=False)
        source = self.branch_source([0, 1, 2, 3, 4], [0, 2, 2, 0, 0], resistance=1.0)
        config = self.thermal_config(
            network, source, Rth_k_per_w=1.0, Cth_j_per_k=1.0,
            window_s=[0, 4], max_step_s=0.01,
        )
        result = analysis.analyze(config)["thermal"]["results"][0]
        pulse = result["pulse_accumulation"]
        self.assertGreater(pulse["peak_delta_t_k"], 0.0)
        self.assertLess(pulse["end_delta_t_k"], pulse["peak_delta_t_k"])
        self.assertLess(pulse["cooldown_delta_t_k"], 0.0)
        self.assertIsNone(result["steady_delta_t_k"])
        self.assertEqual(result["steady_status"], "UNDEFINED_TIME_VARYING_INPUT")

    def test_positive_electrothermal_feedback_is_reported_unstable(self):
        network = self.network(mos=False)
        source = self.branch_source([0, 1, 2], [1, 1, 1], resistance=1.0)
        config = self.thermal_config(
            network, source, Rth_k_per_w=10.0, Cth_j_per_k=1.0,
            resistance_tempco_per_k=1.0,
        )
        result = analysis.analyze(config)["thermal"]["results"][0]
        self.assertEqual(result["feedback_stability"], "UNSTABLE")
        self.assertEqual(result["steady_status"], "UNSTABLE")
        self.assertIsNone(result["steady_delta_t_k"])

    def test_missing_mos_ownership_and_unknown_shmod_stay_unassessed(self):
        network = self.network(mos=True)
        source = self.branch_source([0, 1, 2], [1, 1, 1])
        config = self.thermal_config(network, source)
        del config["thermal"]["power_ownership"]
        report = analysis.analyze(config)["thermal"]
        self.assertEqual(report["status"], "UNASSESSED")
        response = report["results"][0]
        self.assertEqual(response["status"], "UNASSESSED")
        self.assertIn("resistor_only", response["response_scope"])
        self.assertEqual(report["power_ownership"]["shmod_by_device"], {"M1": "unknown", "M2": "unknown"})

    def test_duplicate_internal_and_external_selfheat_is_rejected(self):
        network = self.network(mos=True)
        source = self.branch_source([0, 1, 2], [1, 1, 1], device_power=[0, 1, 0])
        config = self.thermal_config(network, source)
        config["thermal"]["power_ownership"] = {
            "external_mos_devices": ["M1"], "internal_selfheat_devices": [],
            "excluded_devices": ["M2"], "shmod_by_device": {"M1": "enabled", "M2": "unknown"},
            "external_power_source": {"quantity": "dissipative_mos_power", "source": "actual P waveform",
                                      "devices": ["M1"]},
        }
        with self.assertRaisesRegex(analysis.VoltageThermalError, "duplicates enabled"):
            analysis.analyze(config)

    def test_unpartitioned_supply_power_cannot_be_used_as_device_heat(self):
        network = self.network(mos=True)
        source = self.branch_source([0, 1, 2], [1, 1, 1], device_power=[0, 1, 0])
        config = self.thermal_config(network, source)
        config["thermal"]["power_ownership"] = {
            "external_mos_devices": ["M1"], "internal_selfheat_devices": [],
            "excluded_devices": ["M2"], "shmod_by_device": {"M1": "disabled", "M2": "unknown"},
            "external_power_source": {"quantity": "supply_power", "source": "VDD times current",
                                      "devices": ["M1"]},
        }
        with self.assertRaisesRegex(analysis.VoltageThermalError, "supply power is not valid"):
            analysis.analyze(config)

    def test_real_cli_reads_terminal_csv_and_fullnetwork_stress_export(self):
        network = self.network()
        self.write("terminals.csv", "time_s,D,G,S,E,P\n0,1,2,0,-1,3\n1,1,3,0,-1,4\n")
        self.write("ir.json", json.dumps({"thermal_input": {
            "times_s": [0, 1],
            "branches": [{"name": "RCNT1", "material": "CNT", "resistance_ohm": 75,
                          "current_a": [0.001, 0.002]}],
            "device_power_w": None,
        }}))
        config = {
            "network": {"path": "network.sp", "top": "TOP"},
            "scenarios": [{"name": "async-supply-down", "csv": "terminals.csv",
                           "time_column": "TIME_S", "window_s": [0, 1],
                           "column_map": {"D": "d", "G": "g", "S": "s", "E": "e", "P": "p"}}],
            "thermal_input_file": "ir.json",
            "thermal": {
                "Rth_k_per_w": 10, "Cth_j_per_k": 1, "ambient_k": 300,
                "resistance_tempco_per_k": 0.0, "load_scale": [1],
                "window_s": [0, 1], "initial_delta_t_k": 0, "max_step_s": 0.01,
                "power_ownership": {
                    "external_mos_devices": [], "internal_selfheat_devices": [],
                    "excluded_devices": ["M1", "M2"], "shmod_by_device": {"M1": "unknown", "M2": "unknown"},
                },
            },
        }
        self.write("input.json", json.dumps(config))
        completed = subprocess.run(
            [sys.executable, str(TOOLS / "analyze_voltage_thermal.py"), "--input", "input.json"],
            cwd=self.base, check=False, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["scenarios"][0]["devices"][0]["metrics"]["VGS"]["history"]["voltage_v"],
                         [2.0, 3.0])
        self.assertEqual(report["thermal"]["results"][0]["status"], "UNASSESSED")


if __name__ == "__main__":
    unittest.main()
