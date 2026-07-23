"""
suggestions_tab.py
-------------------
Requests the next batch of mixtures to test from Ax, and displays them in
a table you can read off at the bench. Suggestions are cached in
ProjectState once generated (see project_state.py's reasoning on this) --
reopening the tab shows the same suggestions until you explicitly ask for
a new batch, so the recipe list on screen always matches what you'd
actually go mix.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.experiment import build_client, get_next_batch


class SuggestionsTab(QWidget):
    def __init__(self, app_state):
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout()

        controls = QHBoxLayout()
        self.mix_combo = QComboBox()
        self.mix_combo.addItems(["A", "B"])
        self.mix_combo.currentTextChanged.connect(self._refresh_table)
        controls.addWidget(QLabel("Mix:"))
        controls.addWidget(self.mix_combo)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 12)
        self.batch_size_spin.setValue(3)
        controls.addWidget(QLabel("Batch size:"))
        controls.addWidget(self.batch_size_spin)

        generate_button = QPushButton("Generate next batch (fits GP, may take a moment)")
        generate_button.clicked.connect(self._on_generate)
        controls.addWidget(generate_button)

        layout.addLayout(controls)

        layout.addWidget(
            QLabel("Compositions below are mass fractions (sum to 1 across active components).")
        )

        self.table = QTableWidget()
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.setLayout(layout)
        self._refresh_table(self.mix_combo.currentText())

    def _on_generate(self):
        mix_id = self.mix_combo.currentText()
        config = (
            self.app_state.state.campaign_a
            if mix_id == "A"
            else self.app_state.state.campaign_b
        )
        self.status_label.setText("Fitting model and optimizing acquisition function...")
        # Force a UI repaint before the (blocking) Ax call, so the status
        # message is actually visible rather than the window appearing to
        # freeze with no explanation.
        self.repaint()

        try:
            client = build_client(
                config, self.app_state.measurements_df, s_star=self.app_state.state.s_star
            )
            batch = get_next_batch(client, self.batch_size_spin.value())
        except Exception as e:
            QMessageBox.critical(self, "Optimization failed", str(e))
            self.status_label.setText("Failed to generate a batch -- see error dialog.")
            return

        mode = (
            "targeting mode (density optimized, stiffness held near S*)"
            if self.app_state.state.s_star is not None
            else "mapping mode (full Pareto exploration)"
        )
        self.app_state.state.cached_suggestions[mix_id] = batch
        self.app_state.save()
        self.status_label.setText(
            f"Generated {len(batch)} new suggestions for Mix {mix_id} -- {mode}."
        )
        self._refresh_table(mix_id)

    def _refresh_table(self, mix_id: str):
        suggestions = self.app_state.state.cached_suggestions.get(mix_id, [])
        if not suggestions:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        columns = list(suggestions[0].keys())
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setRowCount(len(suggestions))
        for row, params in enumerate(suggestions):
            for col, name in enumerate(columns):
                value = params[name]
                item = QTableWidgetItem(f"{value:.5f}")
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
