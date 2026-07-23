"""
blend_validation_tab.py
------------------------
The VALIDATING_BLENDS phase: once S* is locked (and, per the workflow,
after a round of targeting-mode optimization has pulled Mix A and Mix B
compositions closer to S*), you manually pick one measured Mix A point and
one measured Mix B point to physically blend, record blend-ratio test
data, and check whether stiffness actually stays flat across the blend
line -- the assumption flagged as uncertain from the start, since A and B
use different hardeners.
"""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.blend_validation import NewBlendSample, append_blend_sample
from backend.schema import COMPONENT_COLUMNS, aggregate_replicates


class BlendValidationTab(QWidget):
    def __init__(self, app_state, on_state_changed):
        super().__init__()
        self.app_state = app_state
        self.on_state_changed = on_state_changed
        self._candidates_a: list[dict[str, float]] = []
        self._candidates_b: list[dict[str, float]] = []

        layout = QVBoxLayout()

        intro = QLabel(
            "Pick one measured Mix A composition and one measured Mix B "
            "composition to physically blend, then record test results at "
            "a few blend ratios to check whether stiffness stays flat "
            "across the blend line."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Reference picker ---
        ref_group = QGroupBox("Blend reference compositions")
        ref_form = QFormLayout()

        self.ref_a_combo = QComboBox()
        ref_form.addRow("Mix A reference:", self.ref_a_combo)
        self.ref_b_combo = QComboBox()
        ref_form.addRow("Mix B reference:", self.ref_b_combo)

        self.ref_status_label = QLabel("")
        self.ref_status_label.setWordWrap(True)
        ref_form.addRow(self.ref_status_label)

        confirm_ref_button = QPushButton("Confirm references && start blend validation")
        confirm_ref_button.clicked.connect(self._on_confirm_references)
        ref_form.addRow(confirm_ref_button)

        ref_group.setLayout(ref_form)
        layout.addWidget(ref_group)

        # --- Blend sample data entry ---
        entry_group = QGroupBox("Record a blend test sample")
        entry_form = QFormLayout()

        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 9999)
        entry_form.addRow("Batch number", self.batch_spin)

        self.sample_id_edit = QLineEdit()
        self.sample_id_edit.setPlaceholderText("e.g. BLEND-1-1")
        entry_form.addRow("Sample ID", self.sample_id_edit)

        self.blend_fraction_box = QDoubleSpinBox()
        self.blend_fraction_box.setRange(0.0, 1.0)
        self.blend_fraction_box.setSingleStep(0.05)
        self.blend_fraction_box.setValue(0.5)
        entry_form.addRow(
            "Blend fraction (0 = pure Mix B, 1 = pure Mix A)", self.blend_fraction_box
        )

        self.mass_air_box = QDoubleSpinBox()
        self.mass_air_box.setRange(0.0, 1000.0)
        self.mass_air_box.setDecimals(4)
        entry_form.addRow("Mass in air (g)", self.mass_air_box)

        self.mass_submerged_box = QDoubleSpinBox()
        self.mass_submerged_box.setRange(0.0, 1000.0)
        self.mass_submerged_box.setDecimals(4)
        entry_form.addRow("Mass submerged (g)", self.mass_submerged_box)

        self.fluid_density_box = QDoubleSpinBox()
        self.fluid_density_box.setRange(0.0, 5.0)
        self.fluid_density_box.setDecimals(4)
        self.fluid_density_box.setValue(1.0)
        entry_form.addRow("Fluid density (g/cm³)", self.fluid_density_box)

        self.modulus_box = QDoubleSpinBox()
        self.modulus_box.setRange(0.0, 1_000_000.0)
        self.modulus_box.setDecimals(2)
        entry_form.addRow("Stiffness / modulus (GPa)", self.modulus_box)

        self.notes_edit = QTextEdit()
        self.notes_edit.setFixedHeight(50)
        entry_form.addRow("Notes", self.notes_edit)

        add_button = QPushButton("Add blend sample")
        add_button.clicked.connect(self._on_add_sample)
        entry_form.addRow(add_button)

        entry_group.setLayout(entry_form)
        layout.addWidget(entry_group)

        # --- Plot ---
        self.figure = Figure(figsize=(7, 4))
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        # --- Mark done ---
        done_row = QHBoxLayout()
        done_button = QPushButton("Mark project DONE")
        done_button.clicked.connect(self._on_mark_done)
        done_row.addWidget(done_button)
        layout.addLayout(done_row)

        self.setLayout(layout)
        self.refresh()

    def refresh(self):
        self._rebuild_reference_combos()
        self._update_reference_status()
        self._redraw_plot()

    def _rebuild_reference_combos(self):
        df = self.app_state.measurements_df
        self.ref_a_combo.clear()
        self.ref_b_combo.clear()

        agg_a = aggregate_replicates(df, "A")
        self._candidates_a = []
        for _, row in agg_a.iterrows():
            comp = {name: float(row[name]) for name in COMPONENT_COLUMNS}
            self._candidates_a.append(comp)
            self.ref_a_combo.addItem(
                f"Batch {int(row['batch_num'])}: density={row['density_mean']:.4f}, "
                f"stiffness={row['modulus_mean']:.4f}"
            )

        agg_b = aggregate_replicates(df, "B")
        self._candidates_b = []
        for _, row in agg_b.iterrows():
            comp = {name: float(row[name]) for name in COMPONENT_COLUMNS}
            self._candidates_b.append(comp)
            self.ref_b_combo.addItem(
                f"Batch {int(row['batch_num'])}: density={row['density_mean']:.4f}, "
                f"stiffness={row['modulus_mean']:.4f}"
            )

    def _update_reference_status(self):
        ref_a = self.app_state.state.blend_reference_a
        ref_b = self.app_state.state.blend_reference_b
        if ref_a is not None and ref_b is not None:
            self.ref_status_label.setText(
                "References locked in for this project. Re-confirming below will "
                "overwrite them (this re-logs the decision; the original choice "
                "stays in the Decision Log for reference)."
            )
        else:
            self.ref_status_label.setText(
                "No references locked in yet -- pick one Mix A and one Mix B "
                "composition above, then confirm."
            )

    def _on_confirm_references(self):
        if self.app_state.state.s_star is None:
            QMessageBox.warning(
                self,
                "S* not chosen yet",
                "Lock in S* on the Pareto tab before choosing blend references.",
            )
            return
        if not self._candidates_a or not self._candidates_b:
            QMessageBox.warning(
                self,
                "Not enough data",
                "Need at least one measured composition for both Mix A and Mix B.",
            )
            return

        ref_a = self._candidates_a[self.ref_a_combo.currentIndex()]
        ref_b = self._candidates_b[self.ref_b_combo.currentIndex()]

        reply = QMessageBox.question(
            self,
            "Confirm blend references",
            "Lock in these two compositions as the blend references?\n\n"
            f"Mix A: {self.ref_a_combo.currentText()}\n"
            f"Mix B: {self.ref_b_combo.currentText()}\n\n"
            "This records a permanent entry in the decision log.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.app_state.state.advance_to_validating_blends(ref_a, ref_b)
            self.app_state.save()
            self.on_state_changed()
            self.refresh()
            QMessageBox.information(self, "References locked in", "Blend validation started.")

    def _on_add_sample(self):
        ref_a = self.app_state.state.blend_reference_a
        ref_b = self.app_state.state.blend_reference_b
        if ref_a is None or ref_b is None:
            QMessageBox.warning(
                self,
                "No references locked in",
                "Confirm the two blend reference compositions above first.",
            )
            return
        if not self.sample_id_edit.text().strip():
            QMessageBox.warning(self, "Missing sample ID", "Please enter a sample ID.")
            return

        sample = NewBlendSample(
            batch_num=self.batch_spin.value(),
            sample_id=self.sample_id_edit.text().strip(),
            blend_fraction=self.blend_fraction_box.value(),
            mass_air_g=self.mass_air_box.value(),
            mass_submerged_g=self.mass_submerged_box.value(),
            fluid_density_g_cm3=self.fluid_density_box.value(),
            modulus=self.modulus_box.value(),
            notes=self.notes_edit.toPlainText().strip(),
        )
        try:
            self.app_state.blend_measurements_df = append_blend_sample(
                self.app_state.blend_measurements_df, sample, ref_a, ref_b
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid measurement", str(e))
            return

        self.app_state.save_blend_measurements()
        self.sample_id_edit.clear()
        self._redraw_plot()
        QMessageBox.information(self, "Saved", f"Blend sample {sample.sample_id} added.")

    def _on_mark_done(self):
        if self.app_state.blend_measurements_df.empty:
            QMessageBox.warning(
                self,
                "No blend data yet",
                "Record at least one blend sample before marking the project done.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Confirm mark done",
            "Mark the whole project as DONE? This records a permanent decision "
            "log entry; you can keep using the app afterward if needed.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.app_state.state.mark_done()
            self.app_state.save()
            self.on_state_changed()
            QMessageBox.information(self, "Marked done", "Project stage set to DONE.")

    def _redraw_plot(self):
        self.figure.clear()
        df = self.app_state.blend_measurements_df

        ax_stiffness = self.figure.add_subplot(1, 2, 1)
        ax_density = self.figure.add_subplot(1, 2, 2)

        if not df.empty:
            ax_stiffness.scatter(df["blend_fraction"], df["modulus"], color="tab:purple")
            ax_density.scatter(df["blend_fraction"], df["density"], color="tab:purple")

        s_star = self.app_state.state.s_star
        if s_star is not None:
            ax_stiffness.axhline(s_star, color="green", linestyle="--", label="S*")
            ax_stiffness.legend()

        ax_stiffness.set_xlabel("Blend fraction (0=B, 1=A)")
        ax_stiffness.set_ylabel("Stiffness (GPa)")
        ax_stiffness.set_title("Stiffness across the blend line")

        ax_density.set_xlabel("Blend fraction (0=B, 1=A)")
        ax_density.set_ylabel("Density (g/cm³)")
        ax_density.set_title("Density across the blend line")

        self.figure.tight_layout()
        self.canvas.draw()
