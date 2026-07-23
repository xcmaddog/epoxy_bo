"""
blend_validation.py
--------------------
Owns the blend-validation CSV: physical A/B blends tested at a few ratios,
to check whether stiffness actually stays flat across the blend line (the
assumption flagged as uncertain from the start, since Mix A and Mix B use
different hardeners).

Deliberately a separate file/schema from schema.py rather than extra
columns bolted onto measurements.csv: a blend row isn't "Mix A" or "Mix B"
data (no mix_id), it's not fed to either Ax experiment, and it has a
blend_fraction that plain composition rows don't. Keeping it separate
means neither schema has to grow columns that are meaningless for the
other kind of row.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backend.schema import (
    COMPONENT_COLUMNS,
    RAW_MEASUREMENT_COLUMNS,
    DERIVED_COLUMNS,
    compute_volume_and_density,
)

IDENTIFIER_COLUMNS = ["batch_num", "sample_id"]
# 1.0 = pure Mix A reference composition, 0.0 = pure Mix B reference
# composition, linear in between. Chosen so it reads the same
# left-to-right direction as "Mix A" being named first everywhere else.
BLEND_COLUMNS = ["blend_fraction"]
META_COLUMNS = ["notes"]

ALL_COLUMNS = (
    IDENTIFIER_COLUMNS
    + BLEND_COLUMNS
    + COMPONENT_COLUMNS
    + RAW_MEASUREMENT_COLUMNS
    + DERIVED_COLUMNS
    + META_COLUMNS
)


def blended_composition(
    blend_fraction: float, ref_a: dict[str, float], ref_b: dict[str, float]
) -> dict[str, float]:
    """Component-wise linear interpolation between the two locked reference
    compositions. blend_fraction=1.0 -> pure ref_a, 0.0 -> pure ref_b.

    Interpolates across every component either reference uses (not just
    the ones common to both), since a real physical blend-by-mass of two
    full formulations averages all of both mixes' raw components -- Mix
    A's derived resin (EPIKOTE 1163) doesn't vanish just because it's 0 in
    Mix B, it just gets diluted by the blend ratio like everything else.
    """
    names = set(ref_a) | set(ref_b)
    return {
        name: blend_fraction * ref_a.get(name, 0.0) + (1 - blend_fraction) * ref_b.get(name, 0.0)
        for name in names
    }


def empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype="object") for col in ALL_COLUMNS})


def load_blend_measurements(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        return empty_dataframe()
    df = pd.read_csv(csv_path)
    missing = set(ALL_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"Blend validation CSV at {csv_path} is missing expected columns: {missing}"
        )
    return df


def save_blend_measurements(df: pd.DataFrame, csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, columns=ALL_COLUMNS)


@dataclass
class NewBlendSample:
    """One physical blend cylinder's worth of input data. Composition is
    NOT supplied directly -- it's computed from blend_fraction plus
    whichever reference compositions are currently locked in
    ProjectState, via blended_composition(), so the CSV always reflects
    exactly what was (in principle) weighed out."""

    batch_num: int
    sample_id: str
    blend_fraction: float  # 0 = pure Mix B, 1 = pure Mix A
    mass_air_g: float
    mass_submerged_g: float
    fluid_density_g_cm3: float
    modulus: float
    notes: str = ""


def append_blend_sample(
    df: pd.DataFrame,
    sample: NewBlendSample,
    ref_a: dict[str, float],
    ref_b: dict[str, float],
) -> pd.DataFrame:
    """Appends one blend replicate's row. Returns a NEW DataFrame (does not
    mutate in place) -- caller saves it."""
    volume_cm3, density = compute_volume_and_density(
        sample.mass_air_g, sample.mass_submerged_g, sample.fluid_density_g_cm3
    )
    composition = blended_composition(sample.blend_fraction, ref_a, ref_b)

    row = {col: 0.0 for col in COMPONENT_COLUMNS}
    row.update(composition)
    row.update(
        {
            "batch_num": sample.batch_num,
            "sample_id": sample.sample_id,
            "blend_fraction": sample.blend_fraction,
            "mass_air_g": sample.mass_air_g,
            "mass_submerged_g": sample.mass_submerged_g,
            "fluid_density_g_cm3": sample.fluid_density_g_cm3,
            "modulus": sample.modulus,
            "volume_cm3": volume_cm3,
            "density": density,
            "notes": sample.notes,
        }
    )
    new_row_df = pd.DataFrame([row])
    return pd.concat([df, new_row_df], ignore_index=True)[ALL_COLUMNS]
