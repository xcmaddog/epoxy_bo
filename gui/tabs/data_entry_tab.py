"""
data_entry_tab.py
------------------
Form for entering one replicate's worth of raw measurements at a time.
Deliberately raw-numbers-in (mass in air, mass submerged, fluid density,
modulus) rather than pre-computed density -- density gets computed and
stored automatically via backend/schema.py's Archimedes calculation, so
there's one formula, defined once, instead of everyone doing the division
by hand and possibly inconsistently.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.experiment import (
    DEFAULT_EPOXIDE_EQUIVALENT_WEIGHTS,
    DEFAULT_HARDENER_EQUIVALENT_WEIGHTS,
    compute_stoichiometry_ratio,
)
from backend.schema import NewSample, append_sample


class DataEntryTab(QWidget):
    def __init__(self, app_state):
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout()
        form = QFormLayout()

        self.mix_combo = QComboBox()
        self.mix_combo.addItems(["A", "B"])
        self.mix_combo.currentTextChanged.connect(self._rebuild_component_fields)
        form.addRow("Mix", self.mix_combo)

        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 9999)
        form.addRow("Batch number", self.batch_spin)

        self.sample_id_edit = QLineEdit()
        self.sample_id_edit.setPlaceholderText("e.g. A-3-2")
        form.addRow("Sample ID", self.sample_id_edit)

        # Component fraction fields get rebuilt whenever the mix selection
        # changes, since Mix A and Mix B use a different subset of the 5
        # active components.
        composition_note = QLabel(
            "Composition values below are mass fractions (fraction of "
            "total mass, not volume) and must sum to 1 across the mix's "
            "active components."
        )
        composition_note.setWordWrap(True)
        form.addRow(composition_note)

        self._component_boxes: dict[str, QDoubleSpinBox] = {}
        self._component_form = QFormLayout()
        form.addRow(self._component_form)

        self.mass_air_box = QDoubleSpinBox()
        self.mass_air_box.setRange(0.0, 1000.0)
        self.mass_air_box.setDecimals(4)
        form.addRow("Mass in air (g)", self.mass_air_box)

        self.mass_submerged_box = QDoubleSpinBox()
        self.mass_submerged_box.setRange(0.0, 1000.0)
        self.mass_submerged_box.setDecimals(4)
        form.addRow("Mass submerged (g)", self.mass_submerged_box)

        self.fluid_density_box = QDoubleSpinBox()
        self.fluid_density_box.setRange(0.0, 5.0)
        self.fluid_density_box.setDecimals(4)
        self.fluid_density_box.setValue(1.0)
        form.addRow("Fluid density (g/cm³)", self.fluid_density_box)

        self.modulus_box = QDoubleSpinBox()
        self.modulus_box.setRange(0.0, 1_000_000.0)
        self.modulus_box.setDecimals(2)
        form.addRow("Stiffness / modulus (GPa)", self.modulus_box)

        self.notes_edit = QTextEdit()
        self.notes_edit.setFixedHeight(60)
        form.addRow("Notes", self.notes_edit)

        layout.addLayout(form)

        submit_button = QPushButton("Add sample")
        submit_button.clicked.connect(self._on_submit)
        layout.addWidget(submit_button)

        self.setLayout(layout)
        self._rebuild_component_fields(self.mix_combo.currentText())

    def _rebuild_component_fields(self, mix_id: str):
        # Clear old rows
        while self._component_form.rowCount():
            self._component_form.removeRow(0)
        self._component_boxes.clear()

        config = (
            self.app_state.state.campaign_a
            if mix_id == "A"
            else self.app_state.state.campaign_b
        )
        # The four free components get direct input; the derived one is
        # computed and shown read-only so data entry can't accidentally
        # violate the sum-to-one constraint.
        for name in config.free_component_names():
            box = QDoubleSpinBox()
            box.setRange(0.0, 1.0)
            box.setDecimals(5)
            box.setSingleStep(0.001)
            box.valueChanged.connect(self._update_derived_display)
            self._component_form.addRow(name, box)
            self._component_boxes[name] = box

        self._derived_label = QLineEdit()
        self._derived_label.setReadOnly(True)
        self._component_form.addRow(
            f"{config.derived_component} (derived, mass fraction)", self._derived_label
        )
        self._update_derived_display()

    def _update_derived_display(self):
        mix_id = self.mix_combo.currentText()
        config = (
            self.app_state.state.campaign_a
            if mix_id == "A"
            else self.app_state.state.campaign_b
        )
        total_free = sum(box.value() for box in self._component_boxes.values())
        derived_value = 1.0 - total_free
        self._derived_label.setText(f"{derived_value:.5f}")
        if derived_value < 0:
            self._derived_label.setStyleSheet("color: red;")
        else:
            self._derived_label.setStyleSheet("")

    def _on_submit(self):
        mix_id = self.mix_combo.currentText()
        config = (
            self.app_state.state.campaign_a
            if mix_id == "A"
            else self.app_state.state.campaign_b
        )
        components = {name: box.value() for name, box in self._component_boxes.items()}
        derived_value = 1.0 - sum(components.values())
        if derived_value < 0:
            QMessageBox.warning(
                self,
                "Invalid composition",
                f"Free components sum to more than 1.0 "
                f"({config.derived_component} would be negative: {derived_value:.4f}).",
            )
            return
        components[config.derived_component] = derived_value

        ratio = compute_stoichiometry_ratio(
            components, DEFAULT_EPOXIDE_EQUIVALENT_WEIGHTS, DEFAULT_HARDENER_EQUIVALENT_WEIGHTS
        )
        r_min, r_max = config.stoichiometry_ratio_bounds
        if not (r_min <= ratio <= r_max):
            QMessageBox.warning(
                self,
                "Stoichiometry out of range",
                f"This composition's stoichiometric ratio is {ratio:.3f}, outside "
                f"the configured [{r_min}, {r_max}] band (Setup tab). Entering it "
                f"anyway would save fine here, but Ax would later refuse to "
                f"replay it when building suggestions or the Pareto front for "
                f"this mix -- so it's blocked now instead.",
            )
            return

        if not self.sample_id_edit.text().strip():
            QMessageBox.warning(self, "Missing sample ID", "Please enter a sample ID.")
            return

        sample = NewSample(
            mix_id=mix_id,
            batch_num=self.batch_spin.value(),
            sample_id=self.sample_id_edit.text().strip(),
            components=components,
            mass_air_g=self.mass_air_box.value(),
            mass_submerged_g=self.mass_submerged_box.value(),
            fluid_density_g_cm3=self.fluid_density_box.value(),
            modulus=self.modulus_box.value(),
            notes=self.notes_edit.toPlainText().strip(),
        )
        try:
            self.app_state.measurements_df = append_sample(
                self.app_state.measurements_df, sample
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid measurement", str(e))
            return

        self.app_state.save_measurements()
        QMessageBox.information(self, "Saved", f"Sample {sample.sample_id} added.")
        self.sample_id_edit.clear()
