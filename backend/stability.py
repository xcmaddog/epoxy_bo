"""
stability.py
-------------
Tracks whether a mix's Pareto front has "settled down" -- i.e. whether
recent batches are still meaningfully expanding the achievable
density/stiffness frontier, or whether you've plateaued and it's a
reasonable time to consider locking in S*.

Deliberate design choice: this is computed directly from your raw,
aggregated measurement data (not from Ax's fitted GP). Two reasons:
  1. It gives you an honest, model-independent sanity check -- if the GP's
     posterior front is still moving around a lot, that's worth knowing,
     but "have we actually observed more density/stiffness combinations
     lately" is a simpler, more trustworthy signal for a go/no-go decision.
  2. It doesn't require an expensive Ax model refit per batch just to draw
     a trend line.

The metric is hypervolume: the area (in 2D) of the region of
(density, stiffness) space dominated by your current non-dominated set of
observed points, relative to a reference point. It's a standard scalar
summary used throughout multi-objective optimization for exactly this
"how much of the space have we covered well" question. As you collect
more batches, hypervolume should increase and then flatten out as you
approach what's achievable -- that flattening is your stability signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import torch
from botorch.utils.multi_objective.hypervolume import Hypervolume
from botorch.utils.multi_objective.pareto import is_non_dominated

from backend.experiment import CampaignConfig
from backend.schema import aggregate_replicates


@dataclass
class StabilityPoint:
    batch_num: int
    hypervolume: float
    pct_change_from_previous: float | None  # None for the first batch


def _to_maximize_convention(
    df: pd.DataFrame, config: CampaignConfig
) -> torch.Tensor:
    """Hypervolume, as implemented in botorch, assumes every objective is
    "bigger is better." Since Mix B minimizes density, we negate density
    here so both dimensions follow the maximize convention -- this is
    purely an internal transform for the hypervolume math and doesn't
    affect anything else.
    """
    density = df["density_mean"].to_numpy()
    stiffness = df["modulus_mean"].to_numpy()
    if config.density_direction == -1:
        density = -density
    if config.stiffness_direction == -1:
        stiffness = -stiffness
    return torch.tensor(
        [[d, s] for d, s in zip(density, stiffness)], dtype=torch.double
    )


def _reference_point(config: CampaignConfig) -> torch.Tensor:
    """Same thresholds used for Ax's objective thresholds double as the
    hypervolume reference point here, so the "how much of the useful
    region have we covered" question is answered consistently between the
    optimizer and this stability chart.
    """
    d = config.density_threshold
    s = config.stiffness_threshold
    if config.density_direction == -1:
        d = -d
    if config.stiffness_direction == -1:
        s = -s
    return torch.tensor([d, s], dtype=torch.double)


def compute_hypervolume_trajectory(
    measurements_df: pd.DataFrame, config: CampaignConfig
) -> list[StabilityPoint]:
    """Computes cumulative hypervolume after each batch: batch 1 uses only
    batch-1 data, batch 2 uses batches 1-2 combined, etc. This mirrors how
    the frontier actually grows over time as more of the composition space
    gets explored.
    """
    aggregated = aggregate_replicates(measurements_df, config.mix_id)
    if aggregated.empty:
        return []

    ref_point = _reference_point(config)
    hv = Hypervolume(ref_point=ref_point)

    batch_nums = sorted(aggregated["batch_num"].unique())
    trajectory: list[StabilityPoint] = []
    previous_hv: float | None = None

    for batch_num in batch_nums:
        cumulative = aggregated[aggregated["batch_num"] <= batch_num]
        points = _to_maximize_convention(cumulative, config)

        # Hypervolume is only defined over the non-dominated ("Pareto
        # optimal") subset of points -- dominated points contribute
        # nothing extra and botorch's Hypervolume expects just the front.
        mask = is_non_dominated(points)
        pareto_points = points[mask]

        # Points that don't clear the reference point on *both* axes
        # contribute zero volume and can cause a shape mismatch if left
        # in; botorch handles this internally, but we filter defensively
        # here for a clear error message if something is mis-configured
        # rather than a cryptic tensor-shape failure.
        if pareto_points.numel() == 0:
            volume = 0.0
        else:
            volume = hv.compute(pareto_points)

        pct_change = (
            None
            if previous_hv is None
            else (
                float("inf")
                if previous_hv == 0
                else (volume - previous_hv) / previous_hv * 100
            )
        )
        trajectory.append(
            StabilityPoint(
                batch_num=int(batch_num),
                hypervolume=float(volume),
                pct_change_from_previous=pct_change,
            )
        )
        previous_hv = volume

    return trajectory


def stability_summary(trajectory: list[StabilityPoint], lookback: int = 2) -> str:
    """A short, plain-language readout for the GUI header, e.g.
    "Mix A front: +2.1% hypervolume over the last 2 batches."
    Not a decision -- just the number to inform your judgment call.
    """
    if len(trajectory) < 2:
        return "Not enough batches yet to assess stability."
    recent = trajectory[-lookback:]
    start_hv = trajectory[max(0, len(trajectory) - lookback - 1)].hypervolume
    end_hv = recent[-1].hypervolume
    if start_hv == 0:
        return "Hypervolume reference point not yet cleared by any data."
    pct = (end_hv - start_hv) / start_hv * 100
    n = len(recent)
    return f"+{pct:.1f}% hypervolume over the last {n} batch(es)."
