"""
experiment.py
-------------
Builds an Ax `Client` (Ax's current top-level API, as of ax-platform 1.3)
for one mixture campaign (Mix A or Mix B) and re-hydrates it from your
historical CSV data every time the app runs.

Why rebuild from scratch every time instead of saving Ax's own internal
experiment file? Two reasons we discussed: (1) Ax's internal serialization
format is an implementation detail that can change between versions you
might install months apart, while your CSV never changes shape; and (2)
with the data sizes you'll have (dozens of rows, not thousands), refitting
the GP from raw data takes seconds, so there's no real performance cost to
treating the CSV as the only durable state.

Key Ax concepts used here, explained inline as we hit them:
  - RangeParameterConfig / DerivedParameterConfig: how we encode the
    "5 components must sum to 1" mixture constraint.
  - configure_optimization's objective string: how multi-objective
    direction (maximize vs. minimize) is expressed.
  - outcome_constraints as objective thresholds: the reference point that
    hypervolume-based acquisition (qNEHVI) needs to know "what counts as
    worth optimizing at all."
  - attach_trial + complete_trial: how historical (already-tested) data
    gets fed back into a fresh Client so it "remembers" past batches.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from ax.api.client import Client
from ax.api.configs import DerivedParameterConfig, RangeParameterConfig

from backend.schema import COMPONENT_COLUMNS, aggregate_replicates

# Epoxide equivalent weights (g/eq) -- midpoints of supplier-datasheet
# ranges (EPIKOTE 1163: 380-410, EPON 828: 185-192, HELOXY 107: 155-165).
# Used only by the stoichiometry constraint below; not the same thing as
# the uncured densities in mass_calculator.py.
DEFAULT_EPOXIDE_EQUIVALENT_WEIGHTS: dict[str, float] = {
    "epikote_1163": 395.0,
    "epon_828": 188.5,
    "heloxy_107": 160.0,
    "glymo": 236.34,
}

# Anhydride hardener equivalent weights (g/eq). NSA and DDSA each have
# exactly one anhydride ring per molecule, so equivalent weight ==
# formula weight (from the SPI Supplies product pages / SDS).
DEFAULT_HARDENER_EQUIVALENT_WEIGHTS: dict[str, float] = {
    "nsa": 227.0,
    "ddsa": 266.4,
}


@dataclass
class CampaignConfig:
    """Everything that defines *how* we're optimizing one mix. This is the
    piece that's allowed to change over time (new bounds, a different
    threshold, etc.) without touching the raw CSV -- it belongs in
    project_state.py's JSON, not hardcoded here.
    """

    mix_id: str  # "A" or "B"

    # The component that this mix never uses at all (EPON 828 for Mix A,
    # EPIKOTE 1163 for Mix B). It's excluded from the search space
    # entirely, rather than being a free variable clamped to 0 -- this
    # keeps the search space's real dimensionality (4 free + 1 derived)
    # honest, so the GP isn't wasting model capacity on a "variable" that
    # never varies.
    excluded_component: str

    # Bounds for the four components Ax is actually free to choose.
    # dict of component_name -> (low, high)
    free_component_bounds: dict[str, tuple[float, float]]

    # The fifth component, whose value is always
    # "1 - sum(the four free components)" -- this is what makes the
    # mixture constraint (sum to unity) automatic rather than something
    # the acquisition optimizer has to be told about separately.
    derived_component: str

    # +1 to maximize density, -1 to minimize density. Stiffness is always
    # maximized in our formulation (see the objective_string method for
    # why maximize/maximize and minimize/maximize both trace a useful
    # frontier).
    density_direction: int = 1
    stiffness_direction: int = 1

    # Reference point for hypervolume calculations (qNEHVI needs this to
    # know what region of (density, stiffness) space is worth mapping at
    # all, rather than wasting early batches out at useless extremes).
    # These should be a bit outside your realistically-expected range on
    # the "bad" side -- i.e. worse than anything you'd actually want.
    density_threshold: float = 0.0
    stiffness_threshold: float = 0.0

    # Allowed range for the stoichiometric ratio (hardener equivalents /
    # epoxide equivalents), where 1.0 = perfectly balanced, <1 = under-
    # hardened, >1 = over-hardened. PLACEHOLDER default -- narrow this
    # once you know your chemistry's real cure-quality tolerance.
    stoichiometry_ratio_bounds: tuple[float, float] = (0.8, 1.2)

    # Half-width of the acceptable band around S* during the targeting
    # phase (once S* is locked): stiffness must land within
    # [S* - stiffness_tolerance, S* + stiffness_tolerance]. Unused during
    # mapping -- only takes effect once build_client is given an s_star.
    # PLACEHOLDER default -- narrow this once you know your measurement
    # noise / replicate spread.
    stiffness_tolerance: float = 0.25

    def free_component_names(self) -> list[str]:
        return list(self.free_component_bounds.keys())

    def all_component_names(self) -> list[str]:
        """All 5 components this campaign actually uses (excludes the
        never-used 6th)."""
        return self.free_component_names() + [self.derived_component]

    def objective_string(self) -> str:
        """Ax expresses multi-objective goals as a comma-separated string,
        with a leading '-' meaning "minimize this metric". E.g.
        "density, stiffness" = maximize both; "-density, -stiffness" =
        minimize both.

        Why maximize/minimize on BOTH objectives (per their configured
        direction) rather than fixing a stiffness target directly: this
        traces out each mix's whole achievable frontier (best density at
        every stiffness level) rather than homing in on one point, which
        is what lets us *choose* the shared target stiffness S* after
        seeing both frontiers overlaid, instead of guessing it blind. That
        reasoning holds regardless of which way stiffness is pointed --
        density_direction and stiffness_direction are independent, so e.g.
        "maximize density, minimize stiffness" traces the same kind of
        useful frontier as "maximize density, maximize stiffness" did
        before.
        """
        d_sign = "" if self.density_direction == 1 else "-"
        s_sign = "" if self.stiffness_direction == 1 else "-"
        return f"{d_sign}density, {s_sign}stiffness"

    def outcome_constraints(self) -> list[str]:
        """Encodes the objective thresholds (hypervolume reference point)
        as outcome constraints, in the direction matching each objective.
        For a maximized metric the threshold is a floor ("density >= X");
        for a minimized metric it's a ceiling ("density <= X").
        """
        d_op = ">=" if self.density_direction == 1 else "<="
        s_op = ">=" if self.stiffness_direction == 1 else "<="
        return [
            f"density {d_op} {self.density_threshold}",
            f"stiffness {s_op} {self.stiffness_threshold}",
        ]

    def targeting_objective_string(self) -> str:
        """Single-objective goal used once S* is locked: stiffness is no
        longer pushed toward an extreme (whichever direction it was
        configured for during mapping) -- overshooting S* is exactly as
        unhelpful as undershooting it -- so only density is optimized
        (still in this mix's preferred direction), with stiffness held
        near S* via an outcome constraint instead (see
        targeting_outcome_constraints)."""
        d_sign = "" if self.density_direction == 1 else "-"
        return f"{d_sign}density"

    def targeting_outcome_constraints(self, s_star: float) -> list[str]:
        """Keeps stiffness within stiffness_tolerance of S*. Ax's
        outcome_constraints has no single 'band' syntax, but two one-sided
        constraints on the same metric compose into exactly that (verified
        directly against a real ax.api.client.Client: both parse and
        configure together without Ax complaining about the repeated
        metric name)."""
        low = s_star - self.stiffness_tolerance
        high = s_star + self.stiffness_tolerance
        return [f"stiffness >= {low}", f"stiffness <= {high}"]


def default_campaign_config(mix_id: str) -> CampaignConfig:
    """Reasonable starting bounds for the two campaigns, matching what you
    described: Mix A centered on EPIKOTE 1163 (no EPON 828), Mix B centered
    on EPON 828 (no EPIKOTE 1163). Adjust the bounds/thresholds to match
    your actual chemistry knowledge before running real batches -- these
    are placeholders to get the machinery running.
    """
    if mix_id == "A":
        return CampaignConfig(
            mix_id="A",
            excluded_component="epon_828",
            free_component_bounds={
                "heloxy_107": (0.0, 0.4),
                "glymo": (0.0, 0.10),
                "nsa": (0.0, 0.6),
                "ddsa": (0.0, 0.6),
                "bdma": (0.0, 0.05),
            },
            derived_component="epikote_1163",
            density_direction=1,  # maximize density
            stiffness_direction=-1,  # minimize stiffness
            density_threshold=1.05,
            stiffness_threshold=4.5,
        )
    elif mix_id == "B":
        return CampaignConfig(
            mix_id="B",
            excluded_component="epikote_1163",
            free_component_bounds={
                "heloxy_107": (0.0, 0.4),
                "glymo": (0.0, 0.10),
                "nsa": (0.0, 0.6),
                "ddsa": (0.0, 0.6),
                "bdma": (0.0, 0.05),
            },
            derived_component="epon_828",
            density_direction=-1,  # minimize density
            stiffness_direction=-1,  # minimize stiffness
            density_threshold=1.30,
            stiffness_threshold=4.5,
        )
    else:
        raise ValueError(f"mix_id must be 'A' or 'B', got {mix_id!r}")


def compute_stoichiometry_ratio(
    composition: dict[str, float],
    epoxide_ews: dict[str, float],
    hardener_ews: dict[str, float],
) -> float:
    """Direct numeric ratio = hardener equivalents / epoxide equivalents
    for a full composition (including the derived component's value, not
    just the free ones) -- same equivalents accounting as
    _stoichiometry_constraints, but as a plain number instead of a
    constraint string. Used for the Data Entry background check (catching
    an out-of-band entry before it's ever saved, rather than only
    discovering it later as a hard failure when build_client next tries
    to replay it into Ax) as well as anywhere else the actual ratio value
    is useful, not just a pass/fail on it.
    """
    epoxide_eq = sum(
        value / epoxide_ews[name] for name, value in composition.items() if name in epoxide_ews
    )
    hardener_eq = sum(
        value / hardener_ews[name] for name, value in composition.items() if name in hardener_ews
    )
    if epoxide_eq <= 0:
        raise ValueError("Composition has no epoxide-functional components (nothing to react against).")
    return hardener_eq / epoxide_eq


def _stoichiometry_constraints(
    config: CampaignConfig,
    epoxide_ews: dict[str, float],
    hardener_ews: dict[str, float],
) -> list[str]:
    """Builds the two linear inequality strings that keep the search
    within [r_min, r_max] of stoichiometric balance, where
    ratio = (hardener equivalents) / (epoxide equivalents) and 1.0 means
    perfectly balanced.

    Any free (or derived) component whose name appears in epoxide_ews
    contributes epoxide equivalents (EPIKOTE 1163, EPON 828, HELOXY 107,
    GLYMO); any free component in hardener_ews contributes anhydride
    equivalents (NSA, DDSA). A component in neither dict (BDMA) is
    excluded from both sides. This is driven entirely by dict membership
    rather than hardcoded names, so adding another epoxide-functional or
    hardener-functional component later doesn't require touching this
    function.

    Ax's parameter_constraints only understand parameters actually being
    searched over -- the free components -- not the derived fifth one.
    So rather than referencing config.derived_component by name, we
    substitute its defining expression (1 - sum(free components))
    directly into the epoxide-equivalents expression as a string, and let
    Ax's sympy-based parser expand and simplify it. (Verified this parses
    correctly, including the constant term, via ax.core.parameter_constraint
    .ParameterConstraint directly.)

    r_min <= hardener_eq / epoxide_eq <= r_max
      => r_min*epoxide_eq - hardener_eq <= 0
      => hardener_eq - r_max*epoxide_eq <= 0
    """
    free_names = config.free_component_names()
    r_min, r_max = config.stoichiometry_ratio_bounds

    c_derived = 1.0 / epoxide_ews[config.derived_component]
    derived_expr = "(1 - " + " - ".join(free_names) + ")"
    epoxide_terms = [f"({c_derived})*{derived_expr}"]
    hardener_terms = []
    for name in free_names:
        if name in epoxide_ews:
            epoxide_terms.append(f"({1.0 / epoxide_ews[name]})*{name}")
        if name in hardener_ews:
            hardener_terms.append(f"({1.0 / hardener_ews[name]})*{name}")

    epoxide_eq_expr = " + ".join(epoxide_terms)
    hardener_eq_expr = " + ".join(hardener_terms)

    return [
        f"{r_min}*({epoxide_eq_expr}) - ({hardener_eq_expr}) <= 0",
        f"({hardener_eq_expr}) - {r_max}*({epoxide_eq_expr}) <= 0",
    ]


def build_client(
    config: CampaignConfig,
    measurements_df: pd.DataFrame,
    epoxide_equivalent_weights: dict[str, float] | None = None,
    hardener_equivalent_weights: dict[str, float] | None = None,
    s_star: float | None = None,
) -> Client:
    """Constructs a fresh Ax Client for this campaign, with the search
    space defined by `config`, and replays all historical data for this
    mix from `measurements_df` as completed trials.

    epoxide_equivalent_weights / hardener_equivalent_weights default to
    the module-level placeholders but are normally passed in from
    ProjectState, since those values are user-editable (Setup tab).

    s_star: pass None (the default) during mapping -- full density+
    stiffness Pareto exploration, same as always. Pass the locked S*
    value once it's chosen, and this automatically switches to targeting
    mode (single density objective, stiffness held near S* via an outcome
    constraint) -- callers don't need a separate "mode" flag, since
    ProjectState.s_star being set or not IS the mode.
    """
    epoxide_ews = epoxide_equivalent_weights or DEFAULT_EPOXIDE_EQUIVALENT_WEIGHTS
    hardener_ews = hardener_equivalent_weights or DEFAULT_HARDENER_EQUIVALENT_WEIGHTS

    client = Client()

    # --- Search space ---
    # Each free component gets a RangeParameterConfig (a plain bounded
    # float). The one dependent component is a DerivedParameterConfig: Ax
    # computes its value from an algebraic expression over the other
    # parameters at candidate-generation time, which is what enforces
    # "all 5 components sum to 1" without needing a separate inequality
    # constraint that the acquisition optimizer would otherwise have to
    # satisfy approximately.
    free_params = [
        RangeParameterConfig(name=name, bounds=bounds, parameter_type="float")
        for name, bounds in config.free_component_bounds.items()
    ]
    free_names = config.free_component_names()
    derived_expression = "1 - " + " - ".join(free_names)
    derived_param = DerivedParameterConfig(
        name=config.derived_component,
        expression_str=derived_expression,
        parameter_type="float",
    )
    # Two families of constraint, both expressed only in terms of the four
    # free parameters (never the derived one -- see note above and in
    # _stoichiometry_constraints):
    #   1. The free components can't sum past 1 (otherwise the derived
    #      component, computed as 1 - sum(free), goes negative).
    #   2. The stoichiometric ratio stays within config's configured
    #      [min, max] band around 1.0 (perfect balance).
    sum_constraint = f"{' + '.join(free_names)} <= 1.0"
    stoich_constraints = _stoichiometry_constraints(config, epoxide_ews, hardener_ews)
    client.configure_experiment(
        parameters=free_params + [derived_param],
        parameter_constraints=[sum_constraint, *stoich_constraints],
    )

    # --- Objective + thresholds ---
    if s_star is None:
        objective = config.objective_string()
        outcome_constraints = config.outcome_constraints()
    else:
        objective = config.targeting_objective_string()
        outcome_constraints = config.targeting_outcome_constraints(s_star)

    client.configure_optimization(
        objective=objective,
        outcome_constraints=outcome_constraints,
    )

    # Ax normally picks its generation strategy (Sobol exploration -> a
    # BoTorch model) lazily, the first time you call get_next_trials. But
    # get_pareto_frontier needs a generation strategy to already exist
    # (it uses the fitted model), so if we only ever called
    # get_pareto_front before ever requesting a batch, it would fail. We
    # configure it explicitly here so both entry points work regardless of
    # which one gets called first.
    client.configure_generation_strategy(method="fast")

    # --- Replay historical data ---
    # Ax's Client wants historical points fed in as "attach a trial with
    # these parameters, then complete it with this data" -- there's no
    # bulk-import call in the current API, so we loop. This is fine at lab
    # scale (tens of rows), and only runs once per app launch.
    aggregated = aggregate_replicates(measurements_df, config.mix_id)
    for _, row in aggregated.iterrows():
        parameters = {name: float(row[name]) for name in config.all_component_names()}
        trial_index = client.attach_trial(parameters=parameters)
        client.complete_trial(
            trial_index=trial_index,
            raw_data={
                "density": (float(row["density_mean"]), float(row["density_sem"])),
                "stiffness": (float(row["modulus_mean"]), float(row["modulus_sem"])),
            },
        )

    return client


def get_next_batch(client: Client, batch_size: int) -> list[dict[str, float]]:
    """Ask Ax for the next set of compositions to test. Returns a plain
    list of parameter dicts (one per suggested mixture) in a stable order,
    so the GUI can display "Sample 1, Sample 2, ..." without depending on
    Ax's internal trial-index numbering.
    """
    trials = client.get_next_trials(max_trials=batch_size)
    # trials is {trial_index: parameters}; sort by trial_index for a
    # stable, reproducible display order.
    return [trials[idx] for idx in sorted(trials.keys())]


def get_pareto_front(client: Client) -> list[tuple[dict[str, float], dict[str, float]]]:
    """Returns the Pareto front computed directly from your raw observed
    data (use_model_predictions=False), not the GP's smoothed posterior
    mean.

    This used to use use_model_predictions=True, on the theory that the
    fitted GP denoises the front relative to raw replicate scatter. In
    practice that caused a real bug: the plotted front line was drawn at
    each arm's *predicted* (density, stiffness), while the translucent
    all-points scatter (see pareto_tab.py) is drawn from the same arm's
    raw measured mean -- two different coordinates for the same
    composition. When the GP's prediction drifted even a little from the
    raw mean (normal with only 2-3 replicates per composition), a raw
    scatter point could end up sitting in a position that actually
    dominates a "front" point, which is exactly the kind of thing you'd
    notice and shouldn't have to second-guess when picking S* off this
    plot. Verified directly on real data: Mix B's model-predicted front
    included a point (stiffness=2.25, density=1.07) that raw data shows is
    dominated by an actual observed point (stiffness=2.22, density=1.06).

    This mirrors the same raw-data-over-model-smoothing choice already
    made in stability.py's hypervolume calculation, for the same reason:
    a number you're going to make a real decision from shouldn't wobble
    just because the model refit differently.
    """
    front = client.get_pareto_frontier(use_model_predictions=False)
    results = []
    for parameters, means_and_sems, _trial_index, _arm_name in front:
        metrics = {name: value[0] for name, value in means_and_sems.items()}
        results.append((dict(parameters), metrics))
    return results


def get_best_point(client: Client) -> tuple[dict[str, float], dict[str, float]]:
    """Targeting-mode analog of get_pareto_front: returns the model's
    current single best point as a (composition, {"density": ...,
    "stiffness": ...}) pair. Only valid on a client built with s_star set
    (single-objective config) -- calling get_pareto_front on that same
    client, or this on a mapping-mode client, raises Ax's UnsupportedError,
    since which of these two calls works is tied to which
    optimization_config shape is active (verified directly: a
    single-objective Client's get_pareto_frontier() raises
    "Single-objective optimization does not return a Pareto frontier").
    """
    parameters, means_and_sems, _trial_index, _arm_name = client.get_best_parameterization()
    metrics = {name: value[0] for name, value in means_and_sems.items()}
    return dict(parameters), metrics
