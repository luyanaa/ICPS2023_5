#!/usr/bin/env python3
"""Focused status, evidence-binding, voltage-envelope and ERC regressions."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_postlayout_erc as erc
import postlayout_reliability as reliability


class ReliabilityStatusTest(unittest.TestCase):
    def test_unconfigured_request_is_explicitly_blocked_with_immutable_policy(self):
        report = reliability.run_reliability()
        self.assertEqual(report["screening_status"], "BLOCKED")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")
        self.assertFalse(report["manufacturing_acceptance"]["accepted"])
        self.assertEqual(set(report["topics"]), set(reliability.POLICY_TOPICS))
        self.assertTrue(all(item["status"] == "BLOCKED" for item in report["topics"].values()))
        self.assertEqual(report["screens"]["static_ir"]["status"], "BLOCKED")
        self.assertEqual(report["screens"]["dynamic_ir"]["status"], "BLOCKED")
        self.assertEqual(report["screens"]["voltage_thermal"]["status"], "BLOCKED")


    def test_cli_without_config_writes_blocked_report_and_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "reliability.json"
            with redirect_stdout(io.StringIO()):
                code = reliability.main(["--report", str(report_path)])
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(report["screening_status"], "BLOCKED")
        self.assertEqual(report["manufacturing_acceptance"]["status"], "BLOCKED")

    def test_failure_precedes_missing_evidence_and_supported_screens(self):
        self.assertEqual(reliability._overall_status(["BLOCKED", "FAIL"]), "FAIL")
        self.assertEqual(reliability._overall_status(["BLOCKED", "ASSESSED_NUMERICALLY"]), "BLOCKED")
        self.assertEqual(reliability._overall_status(["PASS", "ASSESSED_NUMERICALLY"]), "SCREENED")
        self.assertEqual(reliability._overall_status(["PASS", "ERROR"]), "BLOCKED")
        self.assertEqual(reliability._overall_status([]), "BLOCKED")

    def test_request_relative_artifacts_are_resolved_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "physical").mkdir()
            request = {"artifacts": {}}
            for role in reliability.ARTIFACT_KEYS:
                path = root / "physical" / (role + ".spice")
                path.write_text("evidence for " + role, encoding="utf-8")
                request["artifacts"][role] = str(path.relative_to(root))
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            loaded, base, _ = reliability._request(request_path, None)
            records, paths, errors = reliability._artifacts(loaded, base, None)
            self.assertEqual(errors, [])
            for role in reliability.ARTIFACT_KEYS:
                expected_path = (root / request["artifacts"][role]).resolve()
                self.assertEqual(paths[role], expected_path)
                self.assertEqual(records[role]["sha256"], reliability._sha256(expected_path))

    def test_generated_artifact_path_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "different.gds"
            generated = root / "generated.gds"
            configured.write_text("configured", encoding="utf-8")
            generated.write_text("generated", encoding="utf-8")
            records, paths, errors = reliability._artifacts(
                {"artifacts": {"gds": str(configured)}}, root,
                {"gds": {"path": str(generated)}})
            self.assertTrue(any("does not match generated physical evidence" in item for item in errors))
            self.assertEqual(paths["gds"], generated.resolve())
            self.assertTrue(records["gds"]["exists"])

    def test_generated_top_binding_rejects_configured_top_override(self):
        report = reliability.run_reliability(
            {"top": "OTHER"}, physical_artifacts={"top": "GENERATED"})
        self.assertEqual(report["top_binding"]["status"], "FAIL")
        self.assertEqual(report["top_binding"]["resolved"], "GENERATED")
        self.assertEqual(report["artifact_binding"]["status"], "FAIL")

    def test_documented_voltage_boundary_is_not_a_qualified_pass(self):
        rating = reliability._policy()["topics"]["voltage_limits"]
        metric_names = rating["documented_absolute_maximum"]["metrics"]

        def sample(value, covered=True):
            metrics = {
                name: {"history": {"time_s": [0.0, 1.0], "voltage_v": [0.0, value]},
                       "full_window_coverage": covered}
                for name in metric_names
            }
            return {"scenarios": [{"name": "edge", "coverage": {
                "full_window_coverage": covered, "requested_window_s": [0.0, 1.0]},
                "devices": [{"name": "M1", "metrics": metrics}]}]}

        boundary = reliability._documented_voltage_screen(sample(7.0), rating)
        self.assertEqual(boundary["status"], "ASSESSED_NUMERICALLY")
        self.assertTrue(all(item["status"] == "WITHIN_DOCUMENTED_ENVELOPE"
                            for item in boundary["observations"]))
        violation = reliability._documented_voltage_screen(sample(-7.001), rating)
        self.assertEqual(violation["status"], "FAIL")
        self.assertEqual(len(violation["violations"]), len(metric_names))
        incomplete = reliability._documented_voltage_screen(sample(6.0, covered=False), rating)
        self.assertEqual(incomplete["status"], "BLOCKED")

    def test_documented_voltage_screen_clips_and_interpolates_requested_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            network = root / "network.spice"
            network.write_text(".SUBCKT TOP D G S E\nM1 D G S E nch\n.ENDS TOP\n")
            waveform = root / "wave.csv"
            waveform.write_text("time,d,g,s,e\n0,0,8,0,0\n1,0,6,0,0\n2,0,6,0,0\n3,0,8,0,0\n")
            result = reliability.voltage_thermal.analyze({
                "network": {"path": str(network), "top": "TOP"},
                "scenarios": [{
                    "name": "clipped", "csv": str(waveform), "time_column": "time",
                    "window_s": [1.5, 2.5],
                    "column_map": {"D": "d", "G": "g", "S": "s", "E": "e"},
                }],
            })
            screen = reliability._documented_voltage_screen(
                result, reliability._policy()["topics"]["voltage_limits"])
        self.assertEqual(screen["status"], "ASSESSED_NUMERICALLY")
        vgs = next(row for row in screen["observations"] if row["metric"] == "VGS")
        self.assertEqual(vgs["minimum_v"], 6.0)
        self.assertEqual(vgs["peak_absolute_v"], 7.0)
        self.assertEqual(vgs["status"], "WITHIN_DOCUMENTED_ENVELOPE")



class MultiMosAssociationTest(unittest.TestCase):
    def test_lvs_device_terminals_match_by_instance_not_first_same_polarity(self):
        keys = ["NMOS@1.000000,1.000000", "NMOS@2.000000,2.000000"]
        comments = []
        lvs_mos = []
        pex_maps = []
        assembled_maps = []
        assembled_mos = []
        node_maps = []
        resistors = []
        association_rows = {}
        expected = []
        for index, (instance, key) in enumerate(zip(("$1", "$2"), keys), start=1):
            logical = {role: "N%d_%s" % (index, role.upper())
                       for role in ("drain", "gate", "source")}
            lvs_nodes = {role: "L%d_%s" % (index, role.upper())
                         for role in ("drain", "gate", "source")}
            pex_nodes = {role: "P%d_%s" % (index, role.upper())
                         for role in ("drain", "gate", "source")}
            for role in ("drain", "gate", "source"):
                comments.append("net %s %s" % (lvs_nodes[role], logical[role]))
                association_rows[logical[role].casefold()] = {
                    "row": {"pex_nodes": [pex_nodes[role]]}}
            x = float(index)
            comments.append("device instance %s r0 MOS %.6f,%.6f NMOS" % (instance, x, x))
            lvs_mos.append({"name": "M" + instance, "model": "NMOS",
                            "d": lvs_nodes["drain"], "g": lvs_nodes["gate"],
                            "s": lvs_nodes["source"], "e": "SUB"})
            pex_maps.append({"device": key, "drain": pex_nodes["drain"],
                             "gate": pex_nodes["gate"], "source": pex_nodes["source"],
                             "e": "SUB"})
            assembled_name = "M%04d" % index
            assembled_maps.append({"name": assembled_name, "layout_device": key})
            assembled_mos.append({"name": assembled_name, "d": "M%d_D" % index,
                                  "g": logical["gate"], "s": "M%d_S" % index,
                                  "e": "E_SUB"})
            node_maps.extend({"pex": pex_nodes[role], "assembled": logical[role]}
                             for role in ("drain", "gate", "source"))
            node_maps.append({"pex": "SUB", "assembled": "E_SUB"})
            for role in ("drain", "source"):
                terminal = "M%d_%s" % (index, role[0].upper())
                resistors.append({"name": "RCNT%d_%s" % (index, role),
                                  "a": logical[role], "b": terminal})
            expected.append({"device": key, "drain_net": logical["drain"],
                             "gate_net": logical["gate"], "source_net": logical["source"]})

        lvs = {
            "metadata": {"comments": comments, "selected_comments": comments},
            "mos": lvs_mos,
        }
        pex = {"mos_maps": pex_maps}
        assembled = {
            "mos_device_maps": assembled_maps,
            "mos": assembled_mos,
            "assembly_node_maps": node_maps,
            "resistors": resistors,
        }
        result = erc._mos_association_check(
            {"expected_mos_associations": expected},
            {"lvs": lvs, "pex": pex, "assembled": assembled},
            association_rows)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(len(result["devices"]), 2)
        lvs_mos[1]["d"] = lvs_mos[0]["d"]
        swapped = erc._mos_association_check(
            {"expected_mos_associations": expected},
            {"lvs": lvs, "pex": pex, "assembled": assembled},
            association_rows)
        self.assertEqual(swapped["status"], "FAIL", swapped)


class IntegratedReliabilityRegressionTest(unittest.TestCase):
    def read_network(self, root, text):
        path = root / "assembled.spice"
        path.write_text(text, encoding="utf-8")
        return path, reliability.reliability_netlist.read_network(path, top="TOP")

    def write_thermal_export(self, root, network, *, branch_name="R1",
                              resistance=1.0, current=(0.0, 0.0),
                              device_power=(0.0, 0.0)):
        path = root / "thermal.json"
        path.write_text(json.dumps({
            "network": {"sha256": network["sha256"]},
            "thermal_input": {
                "times_s": [0.0, 1.0],
                "branches": [{"name": branch_name, "resistance_ohm": resistance,
                              "current_a": list(current)}],
                "device_power_w": None if device_power is None else list(device_power),
            },
        }), encoding="utf-8")
        return path

    def voltage_config(self, csv_path, thermal_path, *, include_e=True,
                       voltage=0.5, tempco=0.0, rth=10.0,
                       ownership=True, vge_limit=False):
        columns = {"D": "D", "G": "G", "S": "S"}
        if include_e:
            columns["E"] = "E"
        config = {
            "scenarios": [{
                "name": "terminal-waveforms", "csv": str(csv_path),
                "time_column": "time_s", "window_s": [0.0, 1.0],
                "column_map": columns,
            }],
            "thermal_input_file": str(thermal_path),
            "thermal": {
                "window_s": [0.0, 1.0], "Rth_k_per_w": rth,
                "Cth_j_per_k": 1.0, "ambient_k": 300.0,
                "resistance_tempco_per_k": tempco, "load_scale": [1.0],
                "initial_delta_t_k": 0.0, "max_step_s": 0.5,
            },
        }
        if ownership:
            config["thermal"]["power_ownership"] = {
                "external_mos_devices": ["M1"], "internal_selfheat_devices": [],
                "excluded_devices": [], "shmod_by_device": {"M1": "disabled"},
                "external_power_source": {
                    "quantity": "dissipative_mos_power", "source": "measured M1 power",
                    "devices": ["M1"],
                },
            }
        if vge_limit:
            config["limits"] = {
                "VGE": {"max_v": 1.0, "source": "explicit regression limit",
                        "qualified": True},
            }
        rows = """time_s,D,G,S,E
0,0,%s,0,0
1,0,%s,0,0
""" % (voltage, voltage)
        csv_path.write_text(rows, encoding="utf-8")
        return config

    def test_real_dynamic_baseline_and_series_case_route_by_scenario_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            network_path, network = self.read_network(
                root, """.SUBCKT TOP VDD X 0
R1 VDD X 1
C1 X 0 1u
.ENDS TOP
""")
            waveform = root / "simulator.csv"
            waveform.write_text(
                """time_s,VDD_V,I_VDD
0,1,0.1
1,1,0.1
2,1,0.1
""",
                encoding="utf-8")
            scenario = {
                "name": "transient-inverter", "waveform_csv": str(waveform),
                "time_column": "time_s", "waveform_provenance": "actual simulator current CSV",
                "fixed_sources": {"VDD": {"kind": "waveform", "column": "VDD_V"},
                                  "0": {"kind": "constant", "value_V": 0.0}},
                "current_waveforms": [{
                    "name": "supply", "source_name": "I_VDD", "source_node": "VDD",
                    "role": "supply", "column": "I_VDD",
                    "current_polarity": "positive_out_of_source",
                    "pair": {"from_node": "VDD", "to_node": "0",
                             "source_from_node": "VDD", "source_to_node": "0"},
                }],
                "initial_state": {"kind": "explicit", "node_voltages_V": {"X": 1.0}},
                "numerical_method": "trapezoidal", "time_grid": "waveform_samples",
                "interpolation": "linear", "integration_window_s": [0.0, 2.0],
                "access_sensitivity_cases": [{
                    "name": "extra-series-access",
                    "branches": [{
                        "name": "RACCESS", "series_with": "R1",
                        "a": "VDD", "b": "X", "resistance_ohm": 0.25,
                        "basis": "explicit omitted series access",
                        "uncertainty": "illustrative only",
                        "excludes_existing": ["measured_CNT", "fitted_RDSW"],
                    }],
                }],
            }
            request = {"scenarios": [scenario]}
            dynamic = reliability._run_ir_scenarios(network, request, "dynamic", root)
            self.assertEqual(dynamic["scenario_errors"], [])
            self.assertEqual(dynamic["scenarios"][0]["name"], "transient-inverter")
            baseline, _ = reliability._select_dynamic_thermal_export(
                {"scenario": "transient-inverter", "case": "baseline"}, dynamic, network)
            sensitivity, _ = reliability._select_dynamic_thermal_export(
                {"scenario": "transient-inverter", "case": "extra-series-access"},
                dynamic, network)
            self.assertEqual(baseline["branches"][0]["resistance_ohm"], 1.0)
            self.assertEqual(sensitivity["branches"][0]["resistance_ohm"], 1.25)

    def test_static_failure_survives_later_invalid_sensitivity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, network = self.read_network(
                root, """.SUBCKT TOP VDD VSS
R1 VDD X 1
R2 X VSS 1
.ENDS TOP
""")
            scenario = {
                "name": "loaded", "fixed_sources_V": {"VDD": 1.0, "VSS": 0.0},
                "current_pairs": [{
                    "name": "load", "from_node": "X", "to_node": "VSS",
                    "current_a": 0.1, "source_from_node": "VDD",
                    "source_to_node": "VSS",
                }],
                "qualified_branch_limits": [{
                    "branch_name": "R1", "peak_current_limit_a": 0.05,
                    "qualified": True, "source": "explicit regression bound",
                }],
                "access_sensitivity_cases": [{
                    "name": "later-invalid-series",
                    "branches": [{
                        "name": "RACCESS", "series_with": "MISSING",
                        "a": "VDD", "b": "X", "resistance_ohm": 1.0,
                        "basis": "test", "uncertainty": "test",
                        "excludes_existing": ["measured_CNT", "fitted_RDSW"],
                    }],
                }],
            }
            request = {"scenarios": [scenario]}
            result = reliability._run_ir_scenarios(network, request, "static", root)
            screen = reliability._ir_screen(result, request["scenarios"], network)
        self.assertEqual(screen["status"], "FAIL")
        self.assertTrue(screen["scenario_errors"])
        self.assertEqual(result["scenarios"][0]["baseline"]["branches"][0]["stress_status"],
                         "EXCEEDS_EXPLICIT_QUALIFIED_LIMIT")

    def test_voltage_violation_survives_missing_thermal_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            network_path, network = self.read_network(
                root, """.SUBCKT TOP D G S E
R1 D S 1
M1 D G S E nch
.ENDS TOP
""")
            missing_file = root / "not-generated.json"
            csv_path = root / "overvoltage.csv"
            config = self.voltage_config(csv_path, missing_file, voltage=8.0)
            result = reliability._run_voltage_thermal(
                config, root, network, network_path,
                reliability._policy()["topics"]["voltage_limits"], None)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["analysis"]["thermal"]["status"], "BLOCKED")
        self.assertEqual(result["documented_absolute_maximum"]["status"], "FAIL")
        self.assertIn("error", result["analysis"]["thermal"])

    def test_missing_e_history_blocks_until_explicitly_mapped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            network_path, network = self.read_network(
                root, """.SUBCKT TOP D G S E
R1 D S 1
M1 D G S E nch
.ENDS TOP
""")
            thermal_file = self.write_thermal_export(root, network)
            csv_path = root / "terminals.csv"
            missing_e = self.voltage_config(
                csv_path, thermal_file, include_e=False, vge_limit=True)
            result_missing = reliability._run_voltage_thermal(
                missing_e, root, network, network_path,
                reliability._policy()["topics"]["voltage_limits"], None)
            with_e = self.voltage_config(
                csv_path, thermal_file, include_e=True, vge_limit=True)
            result_with_e = reliability._run_voltage_thermal(
                with_e, root, network, network_path,
                reliability._policy()["topics"]["voltage_limits"], None)
        self.assertEqual(result_missing["status"], "BLOCKED")
        self.assertEqual(result_missing["terminal_analysis_status"], "UNASSESSED")
        self.assertEqual(result_with_e["status"], "ASSESSED_NUMERICALLY")
        self.assertEqual(result_with_e["terminal_analysis_status"], "ASSESSED_NUMERICALLY")

    def test_unstable_conductor_response_fails_despite_unknown_mos_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            network_path, network = self.read_network(
                root, """.SUBCKT TOP D G S E
R1 D S 1
M1 D G S E nch
.ENDS TOP
""")
            thermal_file = self.write_thermal_export(
                root, network, current=(1.0, 1.0), device_power=None)
            csv_path = root / "terminals.csv"
            config = self.voltage_config(
                csv_path, thermal_file, tempco=1.0, rth=10.0, ownership=False)
            result = reliability._run_voltage_thermal(
                config, root, network, network_path,
                reliability._policy()["topics"]["voltage_limits"], None)
        thermal = result["analysis"]["thermal"]
        self.assertEqual(thermal["status"], "UNASSESSED")
        self.assertTrue(thermal["ownership_reasons"])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["thermal_instabilities"][0]["conductor_feedback_stability"],
                         "UNSTABLE")

    def test_external_thermal_export_requires_identity_and_complete_resistor_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, network = self.read_network(root, """.SUBCKT TOP A B
R1 A B 1
.ENDS TOP
""")
            valid = self.write_thermal_export(root, network)
            source, evidence = reliability._load_external_thermal_export(str(valid), root, network)
            self.assertEqual(evidence["network_sha256"], network["sha256"])
            self.assertEqual(source["branches"][0]["name"], "R1")
            invalid_exports = (
                {"times_s": [0.0, 1.0], "branches": [], "device_power_w": None},
                {"times_s": [0.0, 1.0],
                 "branches": [{"name": "OTHER", "resistance_ohm": 1.0,
                               "current_a": [0.0, 0.0]}],
                 "device_power_w": None},
                {"times_s": [0.0, 1.0],
                 "branches": [{"name": "R1", "resistance_ohm": 2.0,
                               "current_a": [0.0, 0.0]}],
                 "device_power_w": None},
            )
            for index, thermal_input in enumerate(invalid_exports):
                path = root / ("invalid-%d.json" % index)
                path.write_text(json.dumps({
                    "network": {"sha256": network["sha256"]},
                    "thermal_input": thermal_input,
                }), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(reliability.ReliabilityError):
                    reliability._load_external_thermal_export(str(path), root, network)
            without_identity = root / "without-identity.json"
            without_identity.write_text(json.dumps({"thermal_input": source}), encoding="utf-8")
            with self.assertRaises(reliability.ReliabilityError):
                reliability._load_external_thermal_export(str(without_identity), root, network)


if __name__ == "__main__":
    unittest.main()
