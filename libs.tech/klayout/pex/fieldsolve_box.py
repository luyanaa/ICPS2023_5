#!/usr/bin/env python3
"""2-D electrostatic FEM for a finite strip over an ideal BOX return plane.

This solves a deliberately bounded geometry, not a production PEX model:
finite-width top electrode, grounded bottom plane, homogeneous SiO2 BOX,
and natural (insulating) boundaries at the top outside the electrode and at
both lateral edges. Capacitance is obtained from electrostatic energy.
"""

import argparse
import math

import numpy as np
import scipy
import skfem
from skfem import Basis, MeshTri, asm, condense, solve
from skfem.element import ElementTriP1
from skfem.models.poisson import laplace

EPSILON_0_F_PER_M = 8.8541878128e-12


def solve_strip(width_um, box_um, epsilon_r, max_step_um, margin_box_thicknesses):
    if min(width_um, box_um, epsilon_r, max_step_um, margin_box_thicknesses) <= 0.0:
        raise ValueError(
            "width, BOX thickness, permittivity, mesh step, and margin must be positive"
        )

    margin_um = margin_box_thicknesses * box_um
    cells_in_margin = math.ceil(margin_um / max_step_um)
    cells_under_strip = math.ceil(width_um / max_step_um)
    left_um = -0.5 * width_um - margin_um
    right_um = 0.5 * width_um + margin_um
    left = np.linspace(left_um, -0.5 * width_um, cells_in_margin + 1)
    strip = np.linspace(-0.5 * width_um, 0.5 * width_um, cells_under_strip + 1)
    right = np.linspace(0.5 * width_um, right_um, cells_in_margin + 1)
    x_um = np.concatenate((left[:-1], strip, right[1:]))
    nz = math.ceil(box_um / max_step_um)
    z_um = np.linspace(0.0, box_um, nz + 1)
    mesh = MeshTri.init_tensor(x_um, z_um)
    basis = Basis(mesh, ElementTriP1())
    stiffness = epsilon_r * EPSILON_0_F_PER_M * asm(laplace, basis)

    bottom_facets = mesh.facets_satisfying(lambda p: np.isclose(p[1], 0.0))
    electrode_facets = mesh.facets_satisfying(
        lambda p: np.isclose(p[1], box_um)
        & (p[0] >= -0.5 * width_um - 1e-12)
        & (p[0] <= 0.5 * width_um + 1e-12)
    )
    ground = basis.get_dofs(facets=bottom_facets).all()
    driven = basis.get_dofs(facets=electrode_facets).all()
    if ground.size == 0 or driven.size == 0:
        raise RuntimeError("failed to select both electrode boundaries")

    prescribed = np.zeros(basis.N)
    prescribed[driven] = 1.0
    fixed = np.union1d(ground, driven)
    reduced, rhs, potential, free = condense(
        stiffness, np.zeros(basis.N), x=prescribed, D=fixed
    )
    potential[free] = solve(reduced, rhs)

    capacitance_f_per_m = float(potential @ (stiffness @ potential))
    capacitance_ff_per_um = capacitance_f_per_m * 1e9
    density_ff_per_um2 = capacitance_ff_per_um / width_um
    parallel_plate_ff_per_um2 = (
        EPSILON_0_F_PER_M * epsilon_r * 1e9 / box_um
    )
    return {
        "width_um": width_um,
        "capacitance_ff_per_um": capacitance_ff_per_um,
        "density_ff_per_um2": density_ff_per_um2,
        "parallel_plate_ff_per_um2": parallel_plate_ff_per_um2,
        "fringe_ratio": density_ff_per_um2 / parallel_plate_ff_per_um2,
        "nodes": basis.N,
        "triangles": mesh.t.shape[1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--widths-um", nargs="+", type=float, default=[1.0, 10.0, 100.0])
    parser.add_argument("--box-um", type=float, default=0.4)
    parser.add_argument("--epsilon-r", type=float, default=3.9)
    parser.add_argument("--max-step-um", type=float, default=0.02)
    parser.add_argument("--margin-box-thicknesses", type=float, default=12.0)
    args = parser.parse_args()

    print(
        "solver=scikit-fem %s numpy=%s scipy=%s"
        % (skfem.__version__, np.__version__, scipy.__version__)
    )
    print(
        "model=finite top strip / grounded bottom plane / homogeneous SiO2 BOX; "
        "top exterior and lateral boundaries are insulating"
    )
    print(
        "BOX=%.6g um epsilon_r=%.6g max_step=%.6g um margin=%.6g BOX thicknesses"
        % (args.box_um, args.epsilon_r, args.max_step_um, args.margin_box_thicknesses)
    )
    print(
        "width_um capacitance_fF_per_um density_fF_per_um2 "
        "parallel_plate_fF_per_um2 fringe_ratio nodes triangles"
    )
    for width_um in args.widths_um:
        result = solve_strip(
            width_um,
            args.box_um,
            args.epsilon_r,
            args.max_step_um,
            args.margin_box_thicknesses,
        )
        print(
            "{width_um:.6g} {capacitance_ff_per_um:.9g} "
            "{density_ff_per_um2:.9g} {parallel_plate_ff_per_um2:.9g} "
            "{fringe_ratio:.9g} {nodes} {triangles}".format(**result)
        )


if __name__ == "__main__":
    main()
