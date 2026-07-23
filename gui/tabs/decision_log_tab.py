"""
decision_log_tab.py
--------------------
Read-only view of the append-only decision history. Mostly for
auditability weeks later ("wait, why did we pick that S* value again?")
rather than something you'd check daily.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class DecisionLogTab(QWidget):
    def __init__(self, app_state):
        super().__init__()
        self.app_state = app_state

        layout = QVBoxLayout()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        layout.addWidget(refresh_button)

        self.table = QTableWidget()
        layout.addWidget(self.table)

        self.setLayout(layout)
        self.refresh()

    def refresh(self):
        entries = self.app_state.state.decision_log
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Timestamp (UTC)", "Event", "Details"])
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.table.setItem(row, 0, QTableWidgetItem(entry.timestamp))
            self.table.setItem(row, 1, QTableWidgetItem(entry.event))
            self.table.setItem(row, 2, QTableWidgetItem(str(entry.details)))
        self.table.resizeColumnsToContents()
