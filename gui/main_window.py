"""
main_window.py
---------------
Top-level window. Owns the AppState (measurements DataFrame + ProjectState)
and the always-visible header bar showing current stage + S*, so that
information is never buried in a tab you have to remember to check.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from backend.blend_validation import load_blend_measurements, save_blend_measurements
from backend.project_state import ProjectState, load_state, save_state
from backend.schema import load_measurements, save_measurements
from gui.tabs.blend_validation_tab import BlendValidationTab
from gui.tabs.data_entry_tab import DataEntryTab
from gui.tabs.decision_log_tab import DecisionLogTab
from gui.tabs.mass_calc_tab import MassCalculatorTab
from gui.tabs.pareto_tab import ParetoTab
from gui.tabs.setup_tab import SetupTab
from gui.tabs.suggestions_tab import SuggestionsTab

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PATH = DATA_DIR / "measurements.csv"
STATE_PATH = DATA_DIR / "project_state.json"
BLEND_CSV_PATH = DATA_DIR / "blend_validation.csv"


class AppState:
    """Thin holder for the durable state, plus save helpers. Passed by
    reference into every tab so they all read/write the same in-memory
    objects; `save()` / `save_measurements()` / `save_blend_measurements()`
    are the only places disk I/O happens, keeping persistence centralized.
    """

    def __init__(self):
        self.measurements_df = load_measurements(CSV_PATH)
        self.blend_measurements_df = load_blend_measurements(BLEND_CSV_PATH)
        self.state: ProjectState = load_state(STATE_PATH)

    def save(self):
        save_state(self.state, STATE_PATH)

    def save_measurements(self):
        save_measurements(self.measurements_df, CSV_PATH)

    def save_blend_measurements(self):
        save_blend_measurements(self.blend_measurements_df, BLEND_CSV_PATH)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Epoxy Bayesian Optimization")
        self.resize(1000, 800)

        self.app_state = AppState()

        central = QWidget()
        outer_layout = QVBoxLayout()

        self.header_label = QLabel()
        self.header_label.setStyleSheet(
            "font-weight: bold; font-size: 14px; padding: 8px; "
            "background-color: #eef; border: 1px solid #99a;"
        )
        outer_layout.addWidget(self.header_label)

        self.tabs = QTabWidget()
        self.setup_tab = SetupTab(self.app_state)
        self.data_entry_tab = DataEntryTab(self.app_state)
        self.suggestions_tab = SuggestionsTab(self.app_state)
        self.pareto_tab = ParetoTab(self.app_state, on_state_changed=self._refresh_header)
        self.decision_log_tab = DecisionLogTab(self.app_state)
        self.mass_calc_tab = MassCalculatorTab(self.app_state)
        self.blend_validation_tab = BlendValidationTab(
            self.app_state, on_state_changed=self._refresh_header
        )

        self.tabs.addTab(self.setup_tab, "Setup")
        self.tabs.addTab(self.data_entry_tab, "Data Entry")
        self.tabs.addTab(self.suggestions_tab, "Suggestions")
        self.tabs.addTab(self.pareto_tab, "Pareto && Stability")
        self.tabs.addTab(self.decision_log_tab, "Decision Log")
        self.tabs.addTab(self.mass_calc_tab, "Mass Calculator")
        self.tabs.addTab(self.blend_validation_tab, "Blend Validation")

        # Refresh the decision log and header whenever the user switches
        # tabs, so actions taken in one tab (e.g. locking S* in the Pareto
        # tab) are reflected everywhere without needing an explicit
        # refresh click.
        self.tabs.currentChanged.connect(self._on_tab_changed)

        outer_layout.addWidget(self.tabs)
        central.setLayout(outer_layout)
        self.setCentralWidget(central)

        self._refresh_header()

    def _on_tab_changed(self, index: int):
        self._refresh_header()
        widget = self.tabs.widget(index)
        if widget is self.decision_log_tab:
            self.decision_log_tab.refresh()
        elif widget is self.blend_validation_tab:
            self.blend_validation_tab.refresh()

    def _refresh_header(self):
        stage = self.app_state.state.stage.value
        s_star = self.app_state.state.s_star
        s_star_text = f"{s_star:g}" if s_star is not None else "not yet chosen"
        self.header_label.setText(f"Stage: {stage}    |    S*: {s_star_text}")
