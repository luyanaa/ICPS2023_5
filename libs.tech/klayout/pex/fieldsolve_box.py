"""Research-only 2-D electrostatic FEM coupons.

The default solve is a finite strip over an ideal homogeneous BOX return.
An opt-in layered mode accepts caller-specified material regions and electrode
strips; neither mode is a qualified process model.
"""

import argparse
import math

import numpy as np
import scipy
import skfem
from scipy.sparse import coo_matrix
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


def solve_layered_cross_section(layers, electrodes, max_step_um, margin_layer_thicknesses):
    """Return a Maxwell capacitance matrix for explicit finite dielectric layers.

    `layers` entries are (name, thickness_um, epsilon_r), ordered from bottom
    to top. `electrodes` entries are (name, z_um, center_x_um, width_um) and
    must lie on a layer boundary. The finite lateral domain and dielectric
    surface outside electrodes use natural (insulating) boundaries.
    """
    layers = [(str(name), float(thickness), float(epsilon_r))
              for name, thickness, epsilon_r in layers]
    electrodes = [(str(name), float(z), float(center), float(width))
                  for name, z, center, width in electrodes]
    if not layers or len(electrodes) < 2:
        raise ValueError("at least one dielectric layer and two electrodes are required")
    if not math.isfinite(max_step_um) or max_step_um <= 0.0:
        raise ValueError("mesh step must be finite and positive")
    if not math.isfinite(margin_layer_thicknesses) or margin_layer_thicknesses <= 0.0:
        raise ValueError("lateral margin must be finite and positive")

    layer_names = [name for name, _, _ in layers]
    if any(not name for name in layer_names) or len(set(layer_names)) != len(layer_names):
        raise ValueError("dielectric layer names must be non-empty and unique")
    if any(not math.isfinite(thickness) or thickness <= 0.0 or
           not math.isfinite(epsilon_r) or epsilon_r <= 0.0
           for _, thickness, epsilon_r in layers):
        raise ValueError("layer thicknesses and relative permittivities must be finite and positive")

    boundaries = [0.0]
    for _, thickness, _ in layers:
        boundaries.append(boundaries[-1] + thickness)

    electrode_names = [name for name, _, _, _ in electrodes]
    if any(not name for name in electrode_names) or len(set(electrode_names)) != len(electrode_names):
        raise ValueError("electrode names must be non-empty and unique")
    normalized_electrodes = []
    for name, z, center, width in electrodes:
        if not all(math.isfinite(value) for value in (z, center, width)) or width <= 0.0:
            raise ValueError("electrode coordinates must be finite and widths positive")
        boundary = next(
            (value for value in boundaries if math.isclose(z, value, rel_tol=0.0, abs_tol=1e-12)),
            None,
        )
        if boundary is None:
            raise ValueError("electrode %s must lie on a dielectric layer boundary" % name)
        normalized_electrodes.append((name, boundary, center, width))

    for index, (_, z, center, width) in enumerate(normalized_electrodes):
        left = center - 0.5 * width
        right = center + 0.5 * width
        for _, other_z, other_center, other_width in normalized_electrodes[:index]:
            other_left = other_center - 0.5 * other_width
            other_right = other_center + 0.5 * other_width
            if math.isclose(z, other_z, rel_tol=0.0, abs_tol=1e-12) and (
                left <= other_right and other_left <= right
            ):
                raise ValueError("electrodes on the same boundary must not touch or overlap")

    total_thickness = boundaries[-1]
    margin_um = margin_layer_thicknesses * total_thickness
    electrode_edges = sorted({
        edge
        for _, _, center, width in normalized_electrodes
        for edge in (center - 0.5 * width, center + 0.5 * width)
    })
    x_breaks = [electrode_edges[0] - margin_um, *electrode_edges, electrode_edges[-1] + margin_um]
    x_um = np.unique(np.concatenate([
        np.linspace(left, right, max(1, math.ceil((right - left) / max_step_um)) + 1)
        for left, right in zip(x_breaks[:-1], x_breaks[1:])
    ]))
    z_sections = []
    z_start = 0.0
    for _, thickness, _ in layers:
        cells = max(2, math.ceil(thickness / max_step_um))
        z_sections.append(np.linspace(z_start, z_start + thickness, cells + 1))
        z_start += thickness
    z_um = np.unique(np.concatenate(z_sections))

    mesh = MeshTri.init_tensor(x_um, z_um)
    basis = Basis(mesh, ElementTriP1())

    # Assemble the scalar 2-D Laplace stiffness with one permittivity per
    # triangle; the mesh includes every material boundary exactly.
    triangle_nodes = mesh.t.T
    x = mesh.p[0, mesh.t].T
    z = mesh.p[1, mesh.t].T
    twice_area = (
        (x[:, 1] - x[:, 0]) * (z[:, 2] - z[:, 0])
        - (x[:, 2] - x[:, 0]) * (z[:, 1] - z[:, 0])
    )
    area = 0.5 * np.abs(twice_area)
    b = np.column_stack((z[:, 1] - z[:, 2], z[:, 2] - z[:, 0], z[:, 0] - z[:, 1]))
    c = np.column_stack((x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0]))
    local_stiffness = (
        b[:, :, None] * b[:, None, :] + c[:, :, None] * c[:, None, :]
    ) / (4.0 * area[:, None, None])
    layer_index = np.searchsorted(boundaries[1:], z.mean(axis=1), side="right")
    epsilon_by_layer = np.asarray([epsilon_r for _, _, epsilon_r in layers])
    local_stiffness *= (
        EPSILON_0_F_PER_M * epsilon_by_layer[layer_index, None, None]
    )
    rows = np.broadcast_to(triangle_nodes[:, :, None], local_stiffness.shape).ravel()
    columns = np.broadcast_to(triangle_nodes[:, None, :], local_stiffness.shape).ravel()
    stiffness = coo_matrix(
        (local_stiffness.ravel(), (rows, columns)),
        shape=(basis.N, basis.N),
    ).tocsr()

    electrode_dofs = []
    for name, z_level, center, width in normalized_electrodes:
        left = center - 0.5 * width
        right = center + 0.5 * width
        facets = mesh.facets_satisfying(
            lambda p, level=z_level, x0=left, x1=right: (
                np.isclose(p[1], level, rtol=0.0, atol=1e-12)
                & (p[0] >= x0 - 1e-12)
                & (p[0] <= x1 + 1e-12)
            )
        )
        dofs = basis.get_dofs(facets=facets).all()
        if dofs.size == 0:
            raise RuntimeError("failed to select electrode boundary for %s" % name)
        electrode_dofs.append(dofs)
    fixed = np.unique(np.concatenate(electrode_dofs))
    if fixed.size != sum(dofs.size for dofs in electrode_dofs):
        raise ValueError("electrode boundaries share mesh nodes")

    solutions = []
    zero_rhs = np.zeros(basis.N)
    for driven_dofs in electrode_dofs:
        prescribed = np.zeros(basis.N)
        prescribed[driven_dofs] = 1.0
        reduced, rhs, potential, free = condense(
            stiffness, zero_rhs, x=prescribed, D=fixed
        )
        potential[free] = solve(reduced, rhs)
        solutions.append(potential)

    potential_matrix = np.column_stack(solutions)
    capacitance = potential_matrix.T @ (stiffness @ potential_matrix)
    capacitance = 0.5 * (capacitance + capacitance.T) * 1e9
    return {
        "layers": tuple(layers),
        "electrodes": tuple(name for name, _, _, _ in normalized_electrodes),
        "capacitance_matrix_ff_per_um": capacitance,
        "nodes": basis.N,
        "triangles": mesh.t.shape[1],
    }


def parse_layer(text):
    fields = text.split(":")
    if len(fields) != 3 or not fields[0]:
        raise argparse.ArgumentTypeError(
            "layer must be NAME:THICKNESS_UM:EPSILON_R"
        )
    try:
        return fields[0], float(fields[1]), float(fields[2])
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_electrode(text):
    fields = text.split(":")
    if len(fields) != 4 or not fields[0]:
        raise argparse.ArgumentTypeError(
            "electrode must be NAME:Z_UM:CENTER_X_UM:WIDTH_UM"
        )
    try:
        return fields[0], *(float(value) for value in fields[1:])
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--widths-um", nargs="+", type=float, default=[1.0, 10.0, 100.0])
    parser.add_argument("--box-um", type=float, default=0.4)
    parser.add_argument("--epsilon-r", type=float, default=3.9)
    parser.add_argument("--max-step-um", type=float, default=0.02)
    parser.add_argument("--margin-box-thicknesses", type=float, default=12.0)
    parser.add_argument(
        "--dielectric-layer",
        action="append",
        type=parse_layer,
        default=[],
        metavar="NAME:THICKNESS_UM:EPSILON_R",
        help="caller-supplied material layer; repeat in bottom-to-top order",
    )
    parser.add_argument(
        "--electrode",
        action="append",
        type=parse_electrode,
        default=[],
        metavar="NAME:Z_UM:CENTER_X_UM:WIDTH_UM",
        help="caller-supplied finite electrode at a dielectric boundary; repeat",
    )
    parser.add_argument("--margin-layer-thicknesses", type=float, default=12.0)
    args = parser.parse_args()

    print(
        "solver=scikit-fem %s numpy=%s scipy=%s"
        % (skfem.__version__, np.__version__, scipy.__version__)
    )
    if args.dielectric_layer or args.electrode:
        if not args.dielectric_layer or not args.electrode:
            parser.error("layered mode requires both --dielectric-layer and --electrode")
        try:
            result = solve_layered_cross_section(
                args.dielectric_layer,
                args.electrode,
                args.max_step_um,
                args.margin_layer_thicknesses,
            )
        except (RuntimeError, ValueError) as error:
            parser.error(str(error))
        print(
            "model=bounded caller-specified dielectric layers; "
            "lateral and unelectroded top boundaries are insulating"
        )
        print("dielectric_layers name thickness_um epsilon_r")
        for name, thickness, epsilon_r in result["layers"]:
            print("%s %.9g %.9g" % (name, thickness, epsilon_r))
        print("electrodes name z_um center_x_um width_um")
        for name, z_level, center, width in args.electrode:
            print("%s %.9g %.9g %.9g" % (name, z_level, center, width))
        print(
            "capacitance_matrix_fF_per_um rows/columns="
            + " ".join(result["electrodes"])
        )
        for name, row in zip(result["electrodes"], result["capacitance_matrix_ff_per_um"]):
            print(name + " " + " ".join("%.9g" % value for value in row))
        print("nodes=%d triangles=%d" % (result["nodes"], result["triangles"]))
        return

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
