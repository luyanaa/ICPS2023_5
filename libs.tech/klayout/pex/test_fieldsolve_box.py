#!/usr/bin/env python3
"""Regression checks for the research-only electrostatic FEM coupons."""

import unittest

import numpy as np

from fieldsolve_box import solve_layered_cross_section


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
