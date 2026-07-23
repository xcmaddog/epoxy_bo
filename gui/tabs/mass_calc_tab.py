"""
mass_calc_tab.py
-----------------
Takes a cached suggestion (mass-fraction composition) plus a desired mold
volume and outputs how many grams of each uncured raw component to weigh
out. See backend/mass_calculator.py for the underlying physics and the
volume-additivity assumption it relies on.

Deliberately reads suggestions straight from ProjectState.cached_suggestions
rather than re-deriving them, so this tab always matches whatever's shown
on the Suggestions tab -- no separate "which suggestion did I mean" typing.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.mass_calculator import masses_for_volume


class MassCalculatorTab(QWidget):
    def __init__(self, app_state):
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout()

        intro = QLabel(
            "Pick a suggested composition and the volume of the mold(s) "
            "you're filling; this converts the mass-fraction recipe into "
            "grams of each uncured raw component to weigh out."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Suggestion picker ---
        picker_row = QHBoxLayout()
        self.mix_combo = QComboBox()
        self.mix_combo.addItems(["A", "B"])
        self.mix_combo.currentTextChanged.connect(self._rebuild_suggestion_list)
        picker_row.addWidget(QLabel("Mix:"))
        picker_row.addWidget(self.mix_combo)

        self.suggestion_combo = QComboBox()
        picker_row.addWidget(QLabel("Suggestion:"))
        picker_row.addWidget(self.suggestion_combo)
        layout.addLayout(picker_row)

        # --- Volume input ---
        volume_row = QHBoxLayout()
        self.volume_box = QDoubleSpinBox()
        self.volume_box.setRange(0.001, 10_000.0)
        self.volume_box.setDecimals(3)
        self.volume_box.setValue(10.0)
        volume_row.addWidget(QLabel("Mold volume to fill (cm³ / mL):"))
        volume_row.addWidget(self.volume_box)
        layout.addLayout(volume_row)

        calculate_button = QPushButton("Calculate masses")
        calculate_button.clicked.connect(self._on_calculate)
        layout.addWidget(calculate_button)

        # --- Uncured component densities (editable, persisted) ---
        density_group = QGroupBox(
            "Uncured component densities (g/cm³) -- verify against datasheets"
        )
        density_form = QFormLayout()
        self._density_boxes: dict[str, QDoubleSpinBox] = {}
        for name, value in app_state.state.component_densities.items():
            box = QDoubleSpinBox()
            box.setRange(0.01, 5.0)
            box.setDecimals(4)
            box.setValue(value)
            density_form.addRow(name, box)
            self._density_boxes[name] = box
        density_group.setLayout(density_form)
        layout.addWidget(density_group)

        save_densities_button = QPushButton("Save densities")
        save_densities_button.clicked.connect(self._on_save_densities)
        layout.addWidget(save_densities_button)

        # --- Results ---
        self.result_label = QLabel("")
        layout.addWidget(self.result_label)

        self.result_table = QTableWidget()
        layout.addWidget(self.result_table)

        self.setLayout(layout)
        self._rebuild_suggestion_list(self.mix_combo.currentText())

    def _rebuild_suggestion_list(self, mix_id: str):
        self.suggestion_combo.clear()
        suggestions = self.app_state.state.cached_suggestions.get(mix_id, [])
        for i in range(len(suggestions)):
            self.suggestion_combo.addItem(f"Suggestion {i + 1}")

    def _on_save_densities(self):
        self.app_state.state.component_densities = {
            name: box.value() for name, box in self._density_boxes.items()
        }
        self.app_state.save()
        QMessageBox.information(self, "Saved", "Component densities saved.")

    def _on_calculate(self):
        mix_id = self.mix_combo.currentText()
        suggestions = self.app_state.state.cached_suggestions.get(mix_id, [])
        index = self.suggestion_combo.currentIndex()
        if index < 0 or not suggestions:
            QMessageBox.warning(
                self,
                "No suggestion selected",
                f"No cached suggestions for Mix {mix_id} -- generate a batch "
                "on the Suggestions tab first.",
            )
            return

        mass_fractions = suggestions[index]
        component_densities = {
            name: box.value() for name, box in self._density_boxes.items()
        }
        volume_cm3 = self.volume_box.value()

        try:
            result = masses_for_volume(mass_fractions, component_densities, volume_cm3)
        except (KeyError, ValueError) as e:
            QMessageBox.critical(self, "Calculation failed", str(e))
            return

        self.result_label.setText(
            f"Mixture density: {result.mixture_density_g_cm3:.4f} g/cm³    |    "
            f"Total mass: {result.total_mass_g:.4f} g"
        )

        rows = list(result.component_masses_g.items())
        self.result_table.setColumnCount(2)
        self.result_table.setHorizontalHeaderLabels(["Component", "Mass to weigh (g)"])
        self.result_table.setRowCount(len(rows))
        for row, (name, mass_g) in enumerate(rows):
            self.result_table.setItem(row, 0, QTableWidgetItem(name))
            self.result_table.setItem(row, 1, QTableWidgetItem(f"{mass_g:.4f}"))
        self.result_table.resizeColumnsToContents()
