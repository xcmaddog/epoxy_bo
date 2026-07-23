"""
schema.py
---------
This module owns the raw measurement CSV: its column layout, reading/writing
it safely, and turning raw replicate rows into the per-composition
(mean, sem) summaries that Ax wants to see.

Design intent (per our discussion): the CSV is the permanent source of truth.
It should never need to change shape just because we change the Bayesian
optimization model, the objective, or the kernel. Everything model-related
lives in experiment.py / project_state.py instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# All six resin/hardener components. For a given mix_id, one of
# epon_828 / epikote_1163 will always be 0.0 -- we still keep the column so
# that Mix A and Mix B rows have an identical schema and nobody has to
# remember "which five columns apply to which mix."
COMPONENT_COLUMNS = [
    "epon_828",
    "epikote_1163",
    "heloxy_107",
    "glymo",
    "nsa",
    "ddsa",
    "bdma",
]

# Raw physical measurements. Note we store the raw Archimedes numbers
# (mass in air, mass submerged, fluid density) and the raw modulus, rather
# than only the derived density -- so a weird-looking density value later
# can be traced back to which raw number was likely off.
RAW_MEASUREMENT_COLUMNS = [
    "mass_air_g",
    "mass_submerged_g",
    "fluid_density_g_cm3",
    "modulus",
]

# Derived-but-stored-explicitly columns. These are computed from the raw
# columns at entry time and written to the CSV so nothing downstream has to
# recompute them (and so a spreadsheet-literate lab mate can sanity check
# by eye without knowing the Archimedes formula).
DERIVED_COLUMNS = [
    "volume_cm3",
    "density",
]

IDENTIFIER_COLUMNS = ["mix_id", "batch_num", "sample_id"]
META_COLUMNS = ["notes"]

ALL_COLUMNS = (
    IDENTIFIER_COLUMNS
    + COMPONENT_COLUMNS
    + RAW_MEASUREMENT_COLUMNS
    + DERIVED_COLUMNS
    + META_COLUMNS
)


def compute_volume_and_density(
    mass_air_g: float, mass_submerged_g: float, fluid_density_g_cm3: float
) -> tuple[float, float]:
    """Archimedes' principle: the buoyant force (mass_air - mass_submerged,
    in the units here since we're working in grams-force at 1 g/mL water-ish
    fluids) equals the weight of fluid displaced. So:

        volume = (mass_air - mass_submerged) / fluid_density
        density = mass_air / volume

    Returns (volume_cm3, density_g_cm3).
    """
    displaced_mass = mass_air_g - mass_submerged_g
    if displaced_mass <= 0:
        raise ValueError(
            "mass_air_g must be greater than mass_submerged_g "
            f"(got {mass_air_g} and {mass_submerged_g})"
        )
    volume_cm3 = displaced_mass / fluid_density_g_cm3
    density_g_cm3 = mass_air_g / volume_cm3
    return volume_cm3, density_g_cm3


def empty_dataframe() -> pd.DataFrame:
    """An empty, correctly-typed DataFrame -- used both to create a brand
    new CSV and as the fallback when a CSV doesn't exist yet."""
    return pd.DataFrame({col: pd.Series(dtype="object") for col in ALL_COLUMNS})


def load_measurements(csv_path: Path) -> pd.DataFrame:
    """Load the raw measurement CSV. Returns an empty (but correctly
    columned) DataFrame if the file doesn't exist yet, so callers don't need
    a special first-run code path."""
    if not csv_path.exists():
        return empty_dataframe()
    df = pd.read_csv(csv_path)
    # Guard against a hand-edited CSV missing a column -- fail loudly and
    # early rather than mysteriously later when Ax complains about missing
    # data.
    missing = set(ALL_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"Measurement CSV at {csv_path} is missing expected columns: {missing}"
        )
    return df


def save_measurements(df: pd.DataFrame, csv_path: Path) -> None:
    """Write the measurement DataFrame back out, columns in a fixed,
    predictable order so the CSV is easy to open and read directly."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, columns=ALL_COLUMNS)


@dataclass
class NewSample:
    """One physical cylinder's worth of input data, as you'd type it in
    after a round of testing. This is the entry point the GUI's data-entry
    tab will construct and hand to `append_sample`."""

    mix_id: str  # "A" or "B"
    batch_num: int
    sample_id: str
    components: dict[str, float]  # keys = subset of COMPONENT_COLUMNS
    mass_air_g: float
    mass_submerged_g: float
    fluid_density_g_cm3: float
    modulus: float
    notes: str = ""


def append_sample(df: pd.DataFrame, sample: NewSample) -> pd.DataFrame:
    """Append one replicate's row to the measurements DataFrame, computing
    volume/density from the raw Archimedes numbers. Returns a NEW DataFrame
    (does not mutate in place) -- caller is responsible for saving it."""
    volume_cm3, density = compute_volume_and_density(
        sample.mass_air_g, sample.mass_submerged_g, sample.fluid_density_g_cm3
    )

    row = {col: 0.0 for col in COMPONENT_COLUMNS}
    row.update(sample.components)
    row.update(
        {
            "mix_id": sample.mix_id,
            "batch_num": sample.batch_num,
            "sample_id": sample.sample_id,
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


def aggregate_replicates(df: pd.DataFrame, mix_id: str) -> pd.DataFrame:
    """Collapse replicate-level rows into one row per (batch_num, unique
    composition), with mean + standard error of the mean (SEM) for density
    and stiffness. This is what gets fed to Ax: Ax's Client.complete_trial
    accepts (mean, sem) tuples directly, which lets the GP correctly treat
    a batch of 3 noisy replicates as more trustworthy than a single
    one-off reading would be.

    Grouping key is the composition itself (rounded to guard against
    floating point noise) rather than just batch_num, in case two
    compositions ever get tested in the same batch by mistake or on
    purpose.
    """
    mix_df = df[df["mix_id"] == mix_id].copy()
    if mix_df.empty:
        return mix_df

    # Round composition columns for grouping so that e.g. 0.10000001 and
    # 0.1 (which can happen from float round-tripping through CSV) are
    # treated as the same recipe.
    group_cols = [c + "_rounded" for c in COMPONENT_COLUMNS]
    for c in COMPONENT_COLUMNS:
        mix_df[c + "_rounded"] = mix_df[c].round(6)

    def sem(x: pd.Series) -> float:
        n = len(x)
        if n <= 1:
            # A single replicate has no empirical variance to estimate a
            # SEM from. Returning a small positive number rather than 0
            # avoids handing Ax a claimed-exact (zero-noise) observation,
            # which can make the GP overconfident about that single point.
            return float(x.std(ddof=0)) if n == 1 else 0.0
        return float(x.std(ddof=1) / math.sqrt(n))

    grouped = (
        mix_df.groupby(["batch_num"] + group_cols)
        .agg(
            density_mean=("density", "mean"),
            density_sem=("density", sem),
            modulus_mean=("modulus", "mean"),
            modulus_sem=("modulus", sem),
            n_replicates=("density", "count"),
        )
        .reset_index()
    )
    # Strip the "_rounded" suffix back off and restore the true
    # (unrounded) component values by merging back the first matching row.
    grouped = grouped.rename(columns={c + "_rounded": c for c in COMPONENT_COLUMNS})
    return grouped
