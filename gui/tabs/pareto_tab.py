"""
pareto_tab.py
-------------
The main visual: Mix A's and Mix B's Pareto fronts overlaid on one
density-vs-stiffness plot (the vertical gap between them at a given
stiffness is the density span you'd get from blending), plus a
hypervolume-over-batches chart per mix as the stability signal, plus the
deliberate, confirmable "lock in S*" action.
"""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from backend.experiment import build_client, get_best_point, get_pareto_front
from backend.schema import aggregate_replicates
from backend.stability import compute_hypervolume_trajectory, stability_summary

# Aspect ratio (height / width) for just the Pareto front subplot, independent
# of the hypervolume subplot below it and of the figure's own size. 1.0 would
# be a perfect square; this is a plain constant specifically so it's a
# one-line tweak to your exact preferred ratio rather than something buried
# in plotting logic.
PARETO_FRONT_ASPECT_RATIO = 0.85


class ParetoTab(QWidget):
    def __init__(self, app_state, on_state_changed):
        super().__init__()
        self.app_state = app_state
        self.on_state_changed = on_state_changed

        layout = QVBoxLayout()

        refresh_button = QPushButton("Refresh fronts (refits both models)")
        refresh_button.clicked.connect(self.refresh)
        layout.addWidget(refresh_button)

        self.figure = Figure(figsize=(7, 7))
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        self.stability_label_a = QLabel("")
        self.stability_label_b = QLabel("")
        layout.addWidget(self.stability_label_a)
        layout.addWidget(self.stability_label_b)

        lock_in_row = QHBoxLayout()
        lock_in_row.addWidget(QLabel("S* (target stiffness, GPa):"))
        self.s_star_box = QDoubleSpinBox()
        self.s_star_box.setRange(0.0, 1_000_000.0)
        self.s_star_box.setDecimals(2)
        lock_in_row.addWidget(self.s_star_box)
        lock_button = QPushButton("Lock in S*")
        lock_button.clicked.connect(self._on_lock_in)
        lock_in_row.addWidget(lock_button)
        layout.addLayout(lock_in_row)

        self.setLayout(layout)
        self.refresh()

    def _on_lock_in(self):
        value = self.s_star_box.value()
        reply = QMessageBox.question(
            self,
            "Confirm S* selection",
            f"Lock in S* = {value:g} as the shared target stiffness?\n\n"
            "This records a permanent entry in the decision log. You can "
            "still change it later, but the original choice stays in the "
            "log for reference.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.app_state.state.choose_s_star(value)
            self.app_state.save()
            self.on_state_changed()
            QMessageBox.information(self, "S* locked in", f"S* set to {value:g}.")

    def refresh(self):
        df = self.app_state.measurements_df
        config_a = self.app_state.state.campaign_a
        config_b = self.app_state.state.campaign_b

        self.figure.clear()
        ax_front = self.figure.add_subplot(2, 1, 1)
        ax_hv = self.figure.add_subplot(2, 1, 2)

        s_star = self.app_state.state.s_star

        for mix_id, config, color in (("A", config_a, "tab:blue"), ("B", config_b, "tab:orange")):
            mix_df = df[df["mix_id"] == mix_id]
            if mix_df.empty:
                continue

            # All observed points (one per composition, mean of its
            # replicates -- same points Ax's front/GP are built from),
            # drawn first and translucent so the highlighted subset (front,
            # or best point once targeting) stands out on top.
            agg = aggregate_replicates(df, mix_id)
            if not agg.empty:
                ax_front.scatter(
                    agg["modulus_mean"],
                    agg["density_mean"],
                    color=color,
                    alpha=0.25,
                    s=25,
                    zorder=1,
                )

            try:
                client = build_client(config, df, s_star=s_star)
            except Exception as e:
                self._append_status(f"Mix {mix_id} client build failed: {e}")
                continue

            if s_star is None:
                # Mapping mode: full Pareto front, as before.
                try:
                    front = get_pareto_front(client)
                except Exception as e:
                    self._append_status(f"Mix {mix_id} front failed: {e}")
                    continue
                if front:
                    stiffness_vals = [m["stiffness"] for _, m in front]
                    density_vals = [m["density"] for _, m in front]
                    # Sort by stiffness so the front line draws left-to-right
                    # instead of zig-zagging in arbitrary order.
                    order = sorted(range(len(front)), key=lambda i: stiffness_vals[i])
                    stiffness_vals = [stiffness_vals[i] for i in order]
                    density_vals = [density_vals[i] for i in order]
                    ax_front.plot(
                        stiffness_vals,
                        density_vals,
                        "o-",
                        color=color,
                        label=f"Mix {mix_id}",
                        zorder=2,
                    )
            else:
                # Targeting mode: get_pareto_front would raise here (single-
                # objective config), so show the model's single current best
                # point instead, highlighted so it's easy to pick out from
                # the translucent scatter of all observed points.
                try:
                    _comp, metrics = get_best_point(client)
                except Exception as e:
                    self._append_status(f"Mix {mix_id} best point failed: {e}")
                    continue
                ax_front.scatter(
                    [metrics["stiffness"]],
                    [metrics["density"]],
                    color=color,
                    s=150,
                    marker="*",
                    edgecolor="black",
                    linewidth=0.8,
                    zorder=3,
                    label=f"Mix {mix_id} (current best)",
                )

            # Stability chart
            trajectory = compute_hypervolume_trajectory(df, config)
            if trajectory:
                batches = [p.batch_num for p in trajectory]
                hvs = [p.hypervolume for p in trajectory]
                ax_hv.plot(batches, hvs, "o-", color=color, label=f"Mix {mix_id}")
                summary = stability_summary(trajectory)
                if mix_id == "A":
                    self.stability_label_a.setText(f"Mix A: {summary}")
                else:
                    self.stability_label_b.setText(f"Mix B: {summary}")

        if self.app_state.state.s_star is not None:
            ax_front.axvline(
                self.app_state.state.s_star, color="green", linestyle="--", label="S*"
            )
            self.s_star_box.setValue(self.app_state.state.s_star)

        ax_front.set_xlabel("Stiffness (GPa)")
        ax_front.set_ylabel("Density (g/cm³)")
        ax_front.set_title("Pareto fronts: density vs. stiffness")
        ax_front.set_box_aspect(PARETO_FRONT_ASPECT_RATIO)
        ax_front.legend()

        ax_hv.set_xlabel("Batch number")
        ax_hv.set_ylabel("Hypervolume (cumulative)")
        ax_hv.set_title("Stability: hypervolume vs. batch")
        ax_hv.legend()

        self.figure.tight_layout()
        self.canvas.draw()

    def _append_status(self, message: str):
        # Minimal inline error surface -- keeps the tab usable even if one
        # mix's model fit fails (e.g. not enough data yet) while the other
        # still renders.
        print(message)
