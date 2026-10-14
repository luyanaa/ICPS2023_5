#!/usr/bin/env python3
"""Regression checks for the research-only electrostatic FEM coupons."""

import json
from pathlib import Path
import unittest

import numpy as np

from fieldsolve_box import solve_layered_cross_section, solve_strip


class HomogeneousBoxTests(unittest.TestCase):
    def test_finite_strip_approaches_parallel_plate_limit(self):
        result = solve_strip(
            width_um=100.0,
            box_um=0.4,
            epsilon_r=3.9,
            max_step_um=0.02,
            margin_box_thicknesses=12.0,
        )
        relative_error = (
            result["density_ff_per_um2"] / result["parallel_plate_ff_per_um2"] - 1.0
        )
        self.assertLess(abs(relative_error), 0.005, result)

    def test_mesh_refinement_converges(self):
        densities = [
            solve_strip(10.0, 0.4, 3.9, step, 12.0)["density_ff_per_um2"]
            for step in (0.04, 0.02, 0.01)
        ]
        coarse_change = abs(densities[1] - densities[0])
        fine_change = abs(densities[2] - densities[1])
        self.assertGreater(coarse_change, fine_change, densities)
        self.assertLess(fine_change / densities[2], 0.001, densities)

    def test_geometric_scaling_preserves_two_dimensional_capacitance(self):
        base = solve_strip(2.0, 0.2, 3.9, 0.02, 10.0)
        scaled = solve_strip(20.0, 2.0, 3.9, 0.2, 10.0)
        self.assertAlmostEqual(
            base["capacitance_ff_per_um"],
            scaled["capacitance_ff_per_um"],
            delta=base["capacitance_ff_per_um"] * 1e-9,
        )


class LayeredCrossSectionTests(unittest.TestCase):
    def setUp(self):
        # Synthetic relative permittivities exercise caller-supplied weighting;
        # they are not ICPS2023_5 process parameters.
        self.layers = (
            ("gate_oxide", 0.015, 3.0),
            ("teos", 0.300, 4.0),
        )
        self.electrodes = (
            ("DIFF", 0.0, -0.1, 0.8),
            ("TiN", 0.015, 0.0, 1.0),
            ("Al", 0.315, 0.1, 0.6),
        )

    def solve(self, layers=None, electrodes=None):
        return solve_layered_cross_section(
            self.layers if layers is None else layers,
            self.electrodes if electrodes is None else electrodes,
            max_step_um=0.1,
            margin_layer_thicknesses=4.0,
        )

    def test_large_stack_matches_independent_series_dielectric_reference(self):
        layers = (("oxide_a", 0.02, 3.0), ("oxide_b", 0.08, 4.0))
        width_um = 30.0
        result = solve_layered_cross_section(
            layers,
            (("bottom", 0.0, 0.0, width_um), ("top", 0.1, 0.0, width_um)),
            max_step_um=0.02,
            margin_layer_thicknesses=4.0,
        )
        matrix = result["capacitance_matrix_ff_per_um"]
        effective_electrical_thickness = sum(
            thickness / epsilon_r for _name, thickness, epsilon_r in layers
        )
        analytic_density = 8.8541878128e-12 * 1e9 / effective_electrical_thickness
        fem_density = -matrix[0, 1] / width_um
        self.assertLess(abs(fem_density / analytic_density - 1.0), 0.01, matrix)

    def test_synthetic_interconnect_reference_matrix(self):
        path = Path(__file__).with_name("interconnect_cap_reference.json")
        fixture = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("synthetic", fixture["status"].casefold())
        result = solve_layered_cross_section(
            fixture["layers"],
            fixture["electrodes"],
            max_step_um=fixture["max_step_um"],
            margin_layer_thicknesses=fixture["margin_layer_thicknesses"],
        )
        matrix = result["capacitance_matrix_ff_per_um"]
        self.assertEqual(result["electrodes"], tuple(item[0] for item in fixture["electrodes"]))
        self.assertTrue(
            np.allclose(
                matrix,
                np.asarray(fixture["matrix_ff_per_um"]),
                rtol=1e-8,
                atol=1e-10,
            ),
            matrix,
        )
        self.assertTrue(np.all(np.diag(matrix) > 0.0), matrix)
        self.assertTrue(np.all(matrix[np.triu_indices(len(matrix), 1)] < 0.0), matrix)
        self.assertTrue(
            np.allclose(
                matrix.sum(axis=1),
                0.0,
                rtol=0.0,
                atol=np.max(np.abs(matrix)) * 1e-8,
            ),
            matrix,
        )

    def test_matrix_is_symmetric_charge_conserving_and_passive(self):
        result = self.solve()
        matrix = result["capacitance_matrix_ff_per_um"]

        self.assertEqual(result["electrodes"], ("DIFF", "TiN", "Al"))
        self.assertEqual(matrix.shape, (3, 3))
        self.assertTrue(np.all(np.diag(matrix) > 0.0), matrix)
        self.assertTrue(np.all(matrix[np.triu_indices(3, 1)] < 0.0), matrix)
        self.assertTrue(np.allclose(matrix, matrix.T, rtol=1e-12, atol=1e-12), matrix)
        scale = np.max(np.abs(matrix))
        self.assertTrue(np.allclose(matrix.sum(axis=1), 0.0, rtol=0.0, atol=scale * 1e-8), matrix)

    def test_gate_oxide_permittivity_changes_diffusion_tin_mutual_capacitance(self):
        higher_permittivity = (("gate_oxide", 0.015, 6.0), ("teos", 0.300, 4.0))

        baseline = self.solve()["capacitance_matrix_ff_per_um"]
        changed = self.solve(layers=higher_permittivity)["capacitance_matrix_ff_per_um"]

        self.assertGreater(changed[0, 0], baseline[0, 0])
        self.assertLess(changed[0, 1], baseline[0, 1])


    def test_electrodes_must_follow_layer_boundaries_and_not_touch(self):
        wrong_boundary = (
            ("DIFF", 0.0, -0.1, 0.8),
            ("TiN", 0.016, 0.0, 1.0),
        )
        touching = (
            ("LEFT", 0.0, -0.5, 1.0),
            ("RIGHT", 0.0, 0.5, 1.0),
        )

        with self.assertRaisesRegex(ValueError, "layer boundary"):
            self.solve(electrodes=wrong_boundary)
        with self.assertRaisesRegex(ValueError, "must not touch"):
            self.solve(electrodes=touching)


if __name__ == "__main__":
    unittest.main()
