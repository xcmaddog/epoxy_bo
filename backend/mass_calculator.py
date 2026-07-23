"""
mass_calculator.py
-------------------
Turns a suggested composition (mass fractions from an Ax suggestion) plus
a desired mold volume into a bench-ready recipe: how many grams of each
*uncured* raw component to weigh out.

This is a different calculation than anything else in the app models --
Ax's Pareto fronts and hypervolume are about the CURED part's properties
(density, stiffness after curing). This calculator only needs the known
densities of the raw, uncured liquids/solids before mixing, since that's
what actually goes on the scale.

Physics: mass fractions don't directly tell you a mixture's density --
you need each component's own density too. Assuming volumes are additive
when the raw components are mixed (no volume change on mixing, a
reasonable first-order approximation for liquid epoxy resin/hardener/
diluent blends), the mixture's density follows the standard mass-weighted
harmonic mean:

    1 / rho_mix = sum(w_i / rho_i)   for each component i with mass
                                       fraction w_i and pure density rho_i

Once you know rho_mix, converting your mold's volume into a total mass --
and then each component's share of that mass -- is direct multiplication.
"""

from __future__ import annotations

from dataclasses import dataclass

# Uncured component densities (g/cm^3), from supplier datasheets/SDS.
# NSA, DDSA, and BDMA were given as specific gravity (relative to water),
# which is numerically the same as g/cm^3, so no conversion needed there.
# Still editable in the Mass Calculator tab (e.g. if a new lot's SDS gives
# a different figure); edits are saved to project_state.json.
DEFAULT_COMPONENT_DENSITIES_G_CM3: dict[str, float] = {
    "epikote_1163": 1.84,
    "epon_828": 1.16,
    "heloxy_107": 1.09,
    "glymo": 1.07,
    "nsa": 1.03,
    "ddsa": 1.005,
    "bdma": 0.899,
}


@dataclass
class MassCalculationResult:
    mixture_density_g_cm3: float
    total_mass_g: float
    component_masses_g: dict[str, float]  # only components with nonzero fraction


def mixture_density_g_cm3(
    mass_fractions: dict[str, float], component_densities: dict[str, float]
) -> float:
    """Mass-weighted harmonic mean of the pure component densities -- see
    module docstring for the volume-additivity assumption this relies on.
    """
    total = 0.0
    for name, fraction in mass_fractions.items():
        if fraction <= 0:
            continue
        if name not in component_densities:
            raise KeyError(f"No density known for component {name!r}")
        density = component_densities[name]
        if density <= 0:
            raise ValueError(f"Density for {name!r} must be positive, got {density}")
        total += fraction / density
    if total <= 0:
        raise ValueError("Mass fractions must include at least one positive value.")
    return 1.0 / total


def masses_for_volume(
    mass_fractions: dict[str, float],
    component_densities: dict[str, float],
    volume_cm3: float,
) -> MassCalculationResult:
    """Given a mold volume (cm^3) and a composition's mass fractions,
    returns the mixture density, total mass needed, and each component's
    mass to weigh out."""
    if volume_cm3 <= 0:
        raise ValueError(f"Volume must be positive, got {volume_cm3}")
    rho_mix = mixture_density_g_cm3(mass_fractions, component_densities)
    total_mass_g = volume_cm3 * rho_mix
    component_masses_g = {
        name: fraction * total_mass_g
        for name, fraction in mass_fractions.items()
        if fraction > 0
    }
    return MassCalculationResult(
        mixture_density_g_cm3=rho_mix,
        total_mass_g=total_mass_g,
        component_masses_g=component_masses_g,
    )
