#!/usr/bin/env python3
"""Analytic circuit and fault regressions for extracted-network IR analysis."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_power_integrity as pi


class StaticNetworkTest(unittest.TestCase):
    def test_supply_return_divider_reports_drops_kcl_and_power(self):
        network = {
            "name": "divider",
            "nodes": ["VDD_PAD", "VDD_CELL", "VSS_CELL", "VSS_PAD"],
            "resistors": [
                {"name": "R_supply", "a": "VDD_PAD", "b": "VDD_CELL",
                 "resistance_ohm": 10.0, "material": "ML1"},
                {"name": "R_return", "a": "VSS_CELL", "b": "VSS_PAD",
                 "resistance_ohm": 20.0, "material": "TiN"},
            ],
            "capacitors": [], "mos": [],
        }
        report = pi.analyze_static(network, {
            "name": "single-cell-load",
            "fixed_sources_V": {"VDD_PAD": 3.3, "VSS_PAD": 0.0},
            "current_pairs": [{"name": "cell", "from_node": "VDD_CELL",
                                "to_node": "VSS_CELL", "current_a": 0.1,
                                "source_from_node": "VDD_PAD",
                                "source_to_node": "VSS_PAD"}],
        })["baseline"]
        self.assertAlmostEqual(report["node_voltage_V"]["VDD_CELL"], 2.3)
        self.assertAlmostEqual(report["node_voltage_V"]["VSS_CELL"], 2.0)
        pair = report["current_pairs"][0]
        self.assertAlmostEqual(pair["supply_drop_V"], 1.0)
        self.assertAlmostEqual(pair["return_drop_V"], 2.0)
        self.assertAlmostEqual(pair["pair_voltage_V"], 0.3)
        self.assertAlmostEqual(report["source_currents_a_into_network"]["VDD_PAD"], 0.1)
        self.assertLess(report["kcl"]["max_abs_residual_A"], 1e-12)
        self.assertLess(abs(report["power_balance"]["residual_W"]), 1e-12)
        self.assertEqual(report["branches"][1]["material_classification"], "TiN")
        self.assertEqual(report["branches"][0]["geometry_status"], "UNKNOWN")
        self.assertEqual(report["branches"][0]["stress_status"], "UNQUALIFIED_SCREENING")
        self.assertIsNone(report["thermal_input"]["device_power_w"])
        self.assertEqual(report["thermal_input"]["branches"][0]["current_a"], [0.1])

    def test_parallel_branches_obey_current_split_and_power_conservation(self):
        network = {
            "nodes": ["SUPPLY", "LOAD", "RETURN"],
            "resistors": [
                {"name": "R1", "a": "SUPPLY", "b": "LOAD",
                 "resistance_ohm": 10.0, "material": "ML1"},
                {"name": "R2", "a": "SUPPLY", "b": "LOAD",
                 "resistance_ohm": 10.0, "material": "ML1"},
            ],
            "capacitors": [], "mos": [],
        }
        report = pi.analyze_static(network, {
            "name": "parallel-pair",
            "fixed_sources_V": {"SUPPLY": 5.0, "RETURN": 0.0},
            "current_pairs": [{"name": "load", "from_node": "LOAD",
                                "to_node": "RETURN", "current_a": 0.1}],
        })["baseline"]
        currents = {branch["name"]: branch["current_a_from_a_to_b"]
                    for branch in report["branches"]}
        self.assertAlmostEqual(currents["R1"], 0.05)
        self.assertAlmostEqual(currents["R2"], 0.05)
        self.assertAlmostEqual(report["source_currents_a_into_network"]["SUPPLY"], 0.1)
        self.assertAlmostEqual(report["power_balance"]["resistor_dissipation_W"], 0.05)
        self.assertLess(abs(report["power_balance"]["residual_W"]), 1e-12)

    def test_floating_resistive_component_is_not_grounded(self):
        network = {
            "nodes": ["A", "B", "SOURCE"],
            "resistors": [{"name": "RAB", "a": "A", "b": "B",
                           "resistance_ohm": 4.0}],
            "capacitors": [], "mos": [],
        }
        with self.assertRaisesRegex(pi.PowerIntegrityError, "unreferenced/singular"):
            pi.analyze_static(network, {
                "name": "floating",
                "fixed_sources_V": {"SOURCE": 0.0},
                "current_pairs": [],
            })

    def test_access_sensitivity_is_explicit_and_does_not_readd_cnt_or_rdsw(self):
        network = {
            "nodes": ["PAD", "CELL", "RETURN"],
            "resistors": [
                {"name": "RCNT0001", "a": "PAD", "b": "CELL",
                 "resistance_ohm": 15.0, "material": "CNT"},
                {"name": "RRET", "a": "CELL", "b": "RETURN",
                 "resistance_ohm": 5.0, "material": "diffusion"},
            ],
            "capacitors": [],
            "mos": [{"name": "MN1", "model": "nmos",
                     "parameters": {"RDSW": 195.0}}],
        }
        scenario = {
            "name": "access-conditional",
            "fixed_sources_V": {"PAD": 3.0, "RETURN": 0.0},
            "current_pairs": [{"name": "cell", "from_node": "CELL",
                                "to_node": "RETURN", "current_a": 0.01}],
            "access_sensitivity_cases": [{
                "name": "extra-access-20ohm",
                "branches": [{"name": "RACCESS_SENS", "a": "PAD", "b": "CELL",
                              "resistance_ohm": 20.0,
                              "series_with": "RCNT0001",
                              "basis": "explicit bound on omitted diffusion resistance only",
                              "uncertainty": "uncharacterized external diffusion access",
                              "excludes_existing": ["measured_CNT", "fitted_RDSW"],
                              "material": "UNKNOWN"}],
            }],
        }
        result = pi.analyze_static(network, scenario)
        self.assertEqual(result["access_sensitivity_status"],
                         "PROVIDED_CONDITIONAL_UNQUALIFIED_CASES")
        provenance = result["access_resistance_provenance"]
        self.assertEqual(provenance["assembled_measured_CNT_resistors"][0]["name"], "RCNT0001")
        self.assertEqual(provenance["retained_fitted_RDSW_parameters"][0]["value_as_encoded"], 195.0)
        case = result["access_sensitivity_cases"][0]
        self.assertEqual(case["qualification"], "ILLUSTRATIVE_CONDITIONAL_UNQUALIFIED")
        baseline = {branch["name"]: branch for branch in result["baseline"]["branches"]}
        stressed = {branch["name"]: branch for branch in case["branches"]}
        self.assertEqual(baseline["RCNT0001"]["resistance_ohm"], 15.0)
        self.assertEqual(stressed["RCNT0001"]["resistance_ohm"], 35.0)
        self.assertEqual(stressed["RCNT0001"]["stress_status"], "UNQUALIFIED_SCREENING")
        self.assertAlmostEqual(result["baseline"]["node_voltage_V"]["CELL"], 0.7125)
        self.assertAlmostEqual(case["node_voltage_V"]["CELL"], 0.33125)
        self.assertGreater(stressed["RCNT0001"]["voltage_drop_V"],
                           baseline["RCNT0001"]["voltage_drop_V"])
        self.assertEqual(pi.analyze_static(network, {**scenario,
                         "access_sensitivity_cases": []})["access_sensitivity_status"],
                         "MISSING_EXPLICIT_SENSITIVITY")

    def test_series_access_rejects_unknown_targets_and_wrong_endpoints(self):
        network = {
            "nodes": ["PAD", "CELL", "RETURN"],
            "resistors": [{"name": "R1", "a": "PAD", "b": "CELL",
                           "resistance_ohm": 10.0}],
            "capacitors": [], "mos": [],
        }
        for target, endpoint, message in (
                ("absent", "CELL", "must name an extracted resistor"),
                ("R1", "RETURN", "endpoints must match")):
            with self.subTest(target=target, endpoint=endpoint):
                scenario = {
                    "fixed_sources_V": {"PAD": 3.0, "RETURN": 0.0},
                    "current_pairs": [],
                    "access_sensitivity_cases": [{
                        "name": "invalid-series",
                        "branches": [{
                            "name": "RACCESS", "series_with": target,
                            "a": "PAD", "b": endpoint, "resistance_ohm": 20.0,
                            "basis": "omitted diffusion only", "uncertainty": "unknown",
                            "excludes_existing": ["measured_CNT", "fitted_RDSW"],
                        }],
                    }],
                }
                with self.assertRaisesRegex(pi.PowerIntegrityError, message):
                    pi.analyze_static(network, scenario)

    def test_series_current_redistribution_keeps_physical_branch_limit(self):
        import postlayout_reliability as reliability
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "network.spice"
            path.write_text(".SUBCKT TOP PAD CELL RETURN\nR1 PAD CELL 10\nR2 PAD CELL 10\n.ENDS TOP\n")
            scenario = {
                "name": "redistribution",
                "fixed_sources_V": {"PAD": 3.0, "RETURN": 0.0},
                "current_pairs": [{"name": "load", "from_node": "CELL",
                                  "to_node": "RETURN", "current_a": 0.1}],
                "qualified_branch_limits": [{
                    "branch_name": "R1", "peak_current_limit_a": 0.06,
                    "qualified": True, "source": "Explicit regression comparison, not a PDK rating",
                }],
                "access_sensitivity_cases": [{
                    "name": "unequal-series-access",
                    "branches": [
                        {"name": "EXTRA1", "series_with": "R1", "a": "PAD", "b": "CELL",
                         "resistance_ohm": 1.0, "basis": "omitted diffusion only",
                         "uncertainty": "illustrative", "excludes_existing": ["measured_CNT", "fitted_RDSW"]},
                        {"name": "EXTRA2", "series_with": "R2", "a": "PAD", "b": "CELL",
                         "resistance_ohm": 90.0, "basis": "omitted diffusion only",
                         "uncertainty": "illustrative", "excludes_existing": ["measured_CNT", "fitted_RDSW"]},
                    ],
                }],
            }
            report = reliability.run_reliability({
                "top": "TOP", "artifacts": {"assembled": str(path)},
                "static_ir": {"scenarios": [scenario]},
            })
        analysis = report["screens"]["static_ir"]["analysis"]["scenarios"][0]
        baseline = next(branch for branch in analysis["baseline"]["branches"] if branch["name"] == "R1")
        stressed = next(branch for branch in analysis["access_sensitivity_cases"][0]["branches"]
                        if branch["name"] == "R1")
        self.assertAlmostEqual(baseline["peak_current_a"], 0.05)
        self.assertAlmostEqual(stressed["peak_current_a"], 0.1 * 100.0 / 111.0)
        self.assertEqual(baseline["stress_status"], "WITHIN_EXPLICIT_QUALIFIED_LIMIT")
        self.assertEqual(stressed["stress_status"], "EXCEEDS_EXPLICIT_QUALIFIED_LIMIT")
        self.assertEqual(report["screens"]["static_ir"]["status"], "FAIL")
        self.assertEqual(report["screening_status"], "FAIL")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")


class DynamicNetworkTest(unittest.TestCase):
    def _waveform(self, directory):
        path = Path(directory) / "currentwave.csv"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["time_s", "I_VDD", "VDD_V"])
            writer.writerows([[0.0, 0.0, 1.0], [1.0, 1.0, 1.0],
                              [2.0, 1.0, 1.0], [3.0, 0.0, 3.0]])
        return path

    def test_actual_current_rc_droop_recovery_energy_and_thermal_export(self):
        network = {
            "name": "one-pole",
            "nodes": ["VDD", "X", "0"],
            "resistors": [{"name": "RGRID", "a": "VDD", "b": "X",
                           "resistance_ohm": 1.0, "material": "TiN"}],
            "capacitors": [{"name": "CX", "a": "X", "b": "0",
                            "capacitance_f": 1.0}],
            "mos": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            waveform = self._waveform(directory)
            scenario = {
                "name": "pulse-and-source-recovery",
                "waveform_csv": str(waveform),
                "time_column": "time_s",
                "waveform_provenance": {"engine": "analytic transient regression",
                                         "signal": "actual I(VDD) column"},
                "fixed_sources": {
                    "VDD": {"kind": "waveform", "column": "VDD_V"},
                    "0": {"kind": "constant", "value_V": 0.0},
                },
                "current_waveforms": [{
                    "name": "supply-load",
                    "source_name": "I_VDD",
                    "source_node": "VDD",
                    "role": "supply",
                    "column": "I_VDD",
                    "current_polarity": "positive_out_of_source",
                    "pair": {"from_node": "X", "to_node": "0",
                             "source_from_node": "VDD", "source_to_node": "0"},
                }],
                "initial_state": {"kind": "explicit", "node_voltages_V": {"X": 1.0}},
                "numerical_method": "trapezoidal",
                "time_grid": "waveform_samples",
                "interpolation": "linear",
                "integration_window_s": [0.0, 3.0],
            }
            scenario["access_sensitivity_cases"] = [{
                "name": "extra-series-access",
                "branches": [{
                    "name": "RACCESS", "series_with": "RGRID",
                    "a": "VDD", "b": "X", "resistance_ohm": 1.0,
                    "basis": "omitted series diffusion only",
                    "uncertainty": "illustrative bound, not measured",
                    "excludes_existing": ["measured_CNT", "fitted_RDSW"],
                }],
            }]
            analysis = pi.analyze_dynamic(network, scenario)
            report = analysis["baseline"]
            stressed = analysis["access_sensitivity_cases"][0]
            self.assertLess(stressed["node_voltage_histories_V"]["X"][2],
                            report["node_voltage_histories_V"]["X"][2])
        history = report["node_voltage_histories_V"]["X"]
        self.assertLess(history[1], history[0])
        self.assertLess(history[2], history[1])
        self.assertGreater(history[3], history[2])
        recovery = report["pair_voltage_metrics"]["supply-load"]["recovery"]
        self.assertTrue(recovery["recovered_to_initial_voltage"])
        self.assertLess(report["residuals"]["max_abs_dynamic_KCL_A"], 1e-12)
        self.assertLess(report["residuals"]["max_abs_dynamic_power_balance_W"], 1e-12)
        self.assertGreater(report["branches"][0]["energy_J"], 0.0)
        self.assertEqual(report["branches"][0]["material_classification"], "TiN")
        self.assertEqual(report["branches"][0]["stress_status"], "UNQUALIFIED_SCREENING")
        exported = report["thermal_input"]
        self.assertEqual(exported["times_s"], [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(exported["branches"][0]["name"], "RGRID")
        self.assertIsNone(exported["device_power_w"])

    def test_square_current_energy_is_exact_for_linear_interpolation(self):
        # The area under I^2 for a linear 0→2 A ramp is 4/3 A^2 s,
        # not the 2 A^2 s trapezoid of the squared endpoint samples.
        measured = pi.waveint.integral_square(
            [0.0, 1.0], [0.0, 2.0], 0.0, 1.0).net
        self.assertAlmostEqual(measured, 4.0 / 3.0)

    def test_signed_current_crossing_has_exact_absolute_average_and_energy(self):
        network = {
            "nodes": ["VDD", "X", "0"],
            "resistors": [{"name": "RGRID", "a": "VDD", "b": "X",
                           "resistance_ohm": 1.0}],
            "capacitors": [], "mos": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reversal.csv"
            path.write_text("time,I_VDD\n0,1\n1,-1\n")
            report = pi.analyze_dynamic(network, {
                "name": "current-reversal", "waveform_csv": str(path),
                "time_column": "time", "waveform_provenance": "Analytic linear +1 to -1 A ramp",
                "fixed_sources": {"VDD": {"kind": "constant", "value_V": 3.0},
                                  "0": {"kind": "constant", "value_V": 0.0}},
                "current_waveforms": [{
                    "name": "load", "source_name": "I_VDD", "source_node": "VDD",
                    "role": "supply", "column": "I_VDD", "current_polarity": "positive_out_of_source",
                    "pair": {"from_node": "X", "to_node": "0"},
                }],
                "initial_state": {"kind": "dc"}, "numerical_method": "trapezoidal",
                "time_grid": "waveform_samples", "interpolation": "linear",
                "integration_window_s": [0.0, 1.0],
            })["baseline"]
        branch = report["branches"][0]
        self.assertAlmostEqual(branch["average_current_a"], 0.0)
        self.assertAlmostEqual(branch["average_absolute_current_a"], 0.5)
        self.assertAlmostEqual(branch["rms_current_a"], (1.0 / 3.0) ** 0.5)
        self.assertAlmostEqual(branch["energy_J"], 1.0 / 3.0)

    def test_missing_actual_current_polarity_is_rejected(self):
        network = {"nodes": ["S", "X", "0"],
                   "resistors": [{"name": "R", "a": "S", "b": "X",
                                  "resistance_ohm": 1.0}],
                   "capacitors": [{"name": "C", "a": "X", "b": "0",
                                   "capacitance_f": 1.0}],
                   "mos": []}
        with tempfile.TemporaryDirectory() as directory:
            waveform = self._waveform(directory)
            scenario = {
                "waveform_csv": str(waveform), "waveform_provenance": "actual simulator CSV",
                "fixed_sources": {"S": {"kind": "constant", "value_V": 1.0},
                                  "0": {"kind": "constant", "value_V": 0.0}},
                "current_waveforms": [{"name": "supply", "source_name": "I_VDD",
                                        "source_node": "S", "role": "supply",
                                        "column": "I_VDD",
                                        "pair": {"from_node": "X", "to_node": "0"}}],
                "initial_state": {"kind": "explicit", "node_voltages_V": {"X": 1.0}},
                "numerical_method": "trapezoidal", "time_grid": "waveform_samples",
                "interpolation": "linear", "integration_window_s": [0.0, 3.0],
            }
            with self.assertRaisesRegex(pi.PowerIntegrityError, "current_polarity"):
                pi.analyze_dynamic(network, scenario)

    def test_dynamic_floating_component_is_not_implicitly_referenced(self):
        network = {"nodes": ["A", "B", "S"],
                   "resistors": [{"name": "RAB", "a": "A", "b": "B",
                                  "resistance_ohm": 1.0}],
                   "capacitors": [], "mos": []}
        with tempfile.TemporaryDirectory() as directory:
            waveform = Path(directory) / "wave.csv"
            waveform.write_text("time_s,I_VDD\n0,0\n1,0\n", encoding="utf-8")
            scenario = {
                "waveform_csv": str(waveform), "waveform_provenance": "actual simulator CSV",
                "fixed_sources": {"S": {"kind": "constant", "value_V": 0.0}},
                "current_waveforms": [{"name": "supply", "source_name": "I_VDD",
                                        "source_node": "S", "role": "supply",
                                        "column": "I_VDD", "current_polarity": "positive_out_of_source",
                                        "pair": {"from_node": "S", "to_node": "A"}}],
                "initial_state": {"kind": "explicit", "node_voltages_V": {"A": 0.0, "B": 0.0}},
                "numerical_method": "trapezoidal", "time_grid": "waveform_samples",
                "interpolation": "linear", "integration_window_s": [0.0, 1.0],
            }
            with self.assertRaisesRegex(pi.PowerIntegrityError, "unreferenced/singular"):
                pi.analyze_dynamic(network, scenario)


if __name__ == "__main__":
    unittest.main()
