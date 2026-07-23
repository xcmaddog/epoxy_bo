"""
setup_tab.py
------------
Lets you view/edit the search-space bounds and objective thresholds for
each campaign. This writes into ProjectState (via the app's save callback)
rather than backend/experiment.py's hardcoded defaults -- so changing a
bound here doesn't require touching code.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from backend.experiment import CampaignConfig


class CampaignConfigForm(QGroupBox):
    """One editable form for a single campaign's bounds/thresholds."""

    def __init__(self, title: str, config: CampaignConfig):
        super().__init__(title)
        self.config = config
        self._bound_spinboxes: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox]] = {}

        layout = QFormLayout()

        for name, (low, high) in config.free_component_bounds.items():
            row = QHBoxLayout()
            low_box = QDoubleSpinBox()
            low_box.setRange(0.0, 1.0)
            low_box.setSingleStep(0.01)
            low_box.setValue(low)
            high_box = QDoubleSpinBox()
            high_box.setRange(0.0, 1.0)
            high_box.setSingleStep(0.01)
            high_box.setValue(high)
            row.addWidget(QLabel("low"))
            row.addWidget(low_box)
            row.addWidget(QLabel("high"))
            row.addWidget(high_box)
            layout.addRow(f"{name} bounds (mass fraction)", row)
            self._bound_spinboxes[name] = (low_box, high_box)

        self.density_threshold_box = QDoubleSpinBox()
        self.density_threshold_box.setRange(-100.0, 100.0)
        self.density_threshold_box.setDecimals(3)
        self.density_threshold_box.setValue(config.density_threshold)
        layout.addRow("Density threshold (ref. point, g/cm³)", self.density_threshold_box)

        self.stiffness_threshold_box = QDoubleSpinBox()
        self.stiffness_threshold_box.setRange(-1_000_000.0, 1_000_000.0)
        self.stiffness_threshold_box.setValue(config.stiffness_threshold)
        layout.addRow("Stiffness threshold (ref. point, GPa)", self.stiffness_threshold_box)

        # Stoichiometric ratio = hardener equivalents / epoxide equivalents;
        # 1.0 = perfectly balanced. Same low/high pattern as the component
        # bounds above, just for this derived quantity instead of a raw
        # fraction.
        stoich_row = QHBoxLayout()
        stoich_low, stoich_high = config.stoichiometry_ratio_bounds
        self.stoich_low_box = QDoubleSpinBox()
        self.stoich_low_box.setRange(0.0, 5.0)
        self.stoich_low_box.setSingleStep(0.05)
        self.stoich_low_box.setValue(stoich_low)
        self.stoich_high_box = QDoubleSpinBox()
        self.stoich_high_box.setRange(0.0, 5.0)
        self.stoich_high_box.setSingleStep(0.05)
        self.stoich_high_box.setValue(stoich_high)
        stoich_row.addWidget(QLabel("min"))
        stoich_row.addWidget(self.stoich_low_box)
        stoich_row.addWidget(QLabel("max"))
        stoich_row.addWidget(self.stoich_high_box)
        layout.addRow("Stoichiometric ratio bounds (1.0 = balanced)", stoich_row)

        # Only takes effect once S* is locked (see build_client's s_star
        # param / targeting_outcome_constraints) -- harmless to set now.
        self.stiffness_tolerance_box = QDoubleSpinBox()
        self.stiffness_tolerance_box.setRange(0.0, 100.0)
        self.stiffness_tolerance_box.setSingleStep(0.05)
        self.stiffness_tolerance_box.setValue(config.stiffness_tolerance)
        layout.addRow(
            "Stiffness tolerance around S* (targeting phase, GPa)",
            self.stiffness_tolerance_box,
        )

        direction_note = QLabel(
            f"Density direction: {'maximize' if config.density_direction == 1 else 'minimize'}, "
            f"Stiffness direction: {'maximize' if config.stiffness_direction == 1 else 'minimize'} "
            f"(fixed by mix design, not editable here)"
        )
        direction_note.setWordWrap(True)
        layout.addRow(direction_note)

        self.setLayout(layout)

    def updated_config(self) -> CampaignConfig:
        """Reads the form's current values back into a CampaignConfig,
        keeping everything else (excluded component, derived component,
        directions) unchanged."""
        new_bounds = {
            name: (low_box.value(), high_box.value())
            for name, (low_box, high_box) in self._bound_spinboxes.items()
        }
        self.config.free_component_bounds = new_bounds
        self.config.density_threshold = self.density_threshold_box.value()
        self.config.stiffness_threshold = self.stiffness_threshold_box.value()
        self.config.stoichiometry_ratio_bounds = (
            self.stoich_low_box.value(),
            self.stoich_high_box.value(),
        )
        self.config.stiffness_tolerance = self.stiffness_tolerance_box.value()
        return self.config


class SetupTab(QWidget):
    def __init__(self, app_state):
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout()
        intro = QLabel(
            "Edit search-space bounds and objective thresholds below, then "
            "click Save. Changes apply the next time you build a client "
            "(e.g. next time you request a batch or view the Pareto front)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.form_a = CampaignConfigForm("Mix A (EPIKOTE-based)", app_state.state.campaign_a)
        self.form_b = CampaignConfigForm("Mix B (EPON-based)", app_state.state.campaign_b)
        layout.addWidget(self.form_a)
        layout.addWidget(self.form_b)

        save_button = QPushButton("Save campaign settings")
        save_button.clicked.connect(self._on_save)
        layout.addWidget(save_button)

        self.setLayout(layout)

    def _on_save(self):
        self.app_state.state.campaign_a = self.form_a.updated_config()
        self.app_state.state.campaign_b = self.form_b.updated_config()
        self.app_state.state.log_event("campaign_settings_updated")
        self.app_state.save()
