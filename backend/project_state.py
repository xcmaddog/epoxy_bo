"""
project_state.py
-----------------
Everything about "where we are in the process" that needs to survive the
GUI being closed and reopened, possibly weeks later, possibly by a
different lab mate. This is the piece that answers "have we already
decided S*?" without anyone needing to remember or dig through history.

Stored as a single JSON file alongside the measurements CSV. Kept
separate from the CSV deliberately: the CSV is permanent physical
measurements, this file is our current thinking about how to interpret
them, and it's fine (expected, even) for this file's contents to be
edited/revised as the project progresses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from backend.experiment import CampaignConfig, default_campaign_config
from backend.mass_calculator import DEFAULT_COMPONENT_DENSITIES_G_CM3


class Stage(str, Enum):
    MAPPING = "MAPPING"  # mapping Mix A and Mix B simultaneously
    S_STAR_CHOSEN = "S_STAR_CHOSEN"
    VALIDATING_BLENDS = "VALIDATING_BLENDS"
    DONE = "DONE"


@dataclass
class DecisionLogEntry:
    """One append-only entry in the decision history. We never edit or
    delete past entries -- if a decision is reconsidered later, that's a
    *new* entry referencing the old one, so the full reasoning trail stays
    intact for whoever looks at this in three months.
    """

    timestamp: str
    event: str
    details: dict


@dataclass
class ProjectState:
    stage: Stage = Stage.MAPPING
    campaign_a: CampaignConfig = field(default_factory=lambda: default_campaign_config("A"))
    campaign_b: CampaignConfig = field(default_factory=lambda: default_campaign_config("B"))
    s_star: float | None = None
    decision_log: list[DecisionLogEntry] = field(default_factory=list)

    # Cached suggestions: mix_id -> list of parameter dicts. Deliberately
    # NOT regenerated automatically on app open -- Ax's acquisition
    # optimization has some randomness (restarts/seeding), so re-running it
    # on every launch could silently show a different suggested batch than
    # what you were actually looking at before. Suggestions only change
    # when new data is attached and you explicitly ask for a fresh batch.
    cached_suggestions: dict[str, list[dict[str, float]]] = field(default_factory=dict)

    # Uncured raw-component densities (g/cm^3), used only by the Mass
    # Calculator tab to convert a suggested composition + mold volume
    # into grams to weigh out. See mass_calculator.py for the physics.
    component_densities: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_COMPONENT_DENSITIES_G_CM3)
    )

    # The two specific measured compositions being physically blended
    # during VALIDATING_BLENDS (one from Mix A's data, one from Mix B's),
    # chosen manually from the full measurement history for each mix. None
    # until set_blend_references() is called.
    blend_reference_a: dict[str, float] | None = None
    blend_reference_b: dict[str, float] | None = None

    def log_event(self, event: str, **details) -> None:
        entry = DecisionLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event=event,
            details=details,
        )
        self.decision_log.append(entry)

    def choose_s_star(self, value: float, note: str = "") -> None:
        """The deliberate, confirmable action for locking in the shared
        target stiffness. This is the one transition in the whole state
        machine that a human decides, not an algorithm -- everything else
        can be inferred, this can't.
        """
        self.s_star = value
        self.stage = Stage.S_STAR_CHOSEN
        self.log_event("s_star_chosen", value=value, note=note)

    def advance_to_validating_blends(
        self, ref_a: dict[str, float], ref_b: dict[str, float]
    ) -> None:
        """The second deliberate, confirmable human decision (after S*):
        which specific measured Mix A composition and which specific
        measured Mix B composition are actually going to get physically
        blended together. Logged with the full compositions so the
        decision log is self-contained -- no need to cross-reference back
        into measurements.csv to know what was chosen and why.
        """
        if self.s_star is None:
            raise ValueError("Cannot start blend validation before S* is chosen.")
        self.blend_reference_a = dict(ref_a)
        self.blend_reference_b = dict(ref_b)
        self.stage = Stage.VALIDATING_BLENDS
        self.log_event(
            "advanced_to_validating_blends", ref_a=dict(ref_a), ref_b=dict(ref_b)
        )

    def mark_done(self, note: str = "") -> None:
        self.stage = Stage.DONE
        self.log_event("marked_done", note=note)


def _campaign_to_dict(config: CampaignConfig) -> dict:
    return asdict(config)


def _campaign_from_dict(d: dict) -> CampaignConfig:
    # asdict() turns free_component_bounds' tuple values into lists when
    # round-tripped through JSON; convert them back to tuples so
    # CampaignConfig's type stays consistent for the rest of the code.
    d = dict(d)
    d["free_component_bounds"] = {
        k: tuple(v) for k, v in d["free_component_bounds"].items()
    }
    return CampaignConfig(**d)


def save_state(state: ProjectState, json_path: Path) -> None:
    payload = {
        "stage": state.stage.value,
        "campaign_a": _campaign_to_dict(state.campaign_a),
        "campaign_b": _campaign_to_dict(state.campaign_b),
        "s_star": state.s_star,
        "decision_log": [asdict(e) for e in state.decision_log],
        "cached_suggestions": state.cached_suggestions,
        "component_densities": state.component_densities,
        "blend_reference_a": state.blend_reference_a,
        "blend_reference_b": state.blend_reference_b,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)


def load_state(json_path: Path) -> ProjectState:
    """Loads project state, or returns a fresh default state if this is
    the first run (no special-casing needed by callers)."""
    if not json_path.exists():
        return ProjectState()

    with open(json_path) as f:
        payload = json.load(f)

    # Backward compat: older project_state.json files predate the merge of
    # MAPPING_A/MAPPING_B into a single MAPPING stage (we now map both mixes
    # simultaneously rather than one at a time).
    raw_stage = payload["stage"]
    if raw_stage in ("MAPPING_A", "MAPPING_B"):
        raw_stage = "MAPPING"

    return ProjectState(
        stage=Stage(raw_stage),
        campaign_a=_campaign_from_dict(payload["campaign_a"]),
        campaign_b=_campaign_from_dict(payload["campaign_b"]),
        s_star=payload["s_star"],
        decision_log=[DecisionLogEntry(**e) for e in payload["decision_log"]],
        cached_suggestions=payload["cached_suggestions"],
        # .get with a default, not payload[...]: older project_state.json
        # files (from before this field existed) won't have this key.
        component_densities=payload.get(
            "component_densities", dict(DEFAULT_COMPONENT_DENSITIES_G_CM3)
        ),
        blend_reference_a=payload.get("blend_reference_a"),
        blend_reference_b=payload.get("blend_reference_b"),
    )
