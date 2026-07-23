# Epoxy Bayesian Optimization GUI (prototype)

## Running it

```
pip install -r requirements.txt
python main.py
```

First launch creates `data/measurements.csv` and `data/project_state.json`
with sensible-but-placeholder defaults. **Edit the bounds and thresholds in
the Setup tab before running real batches** — the defaults in
`backend/experiment.py::default_campaign_config` are just enough to make
the app runnable, not calibrated to your actual chemistry.

## Project layout

```
backend/
  schema.py          Raw CSV I/O, Archimedes density calc, replicate aggregation
  experiment.py       Ax Client construction: search space, objectives, thresholds
  stability.py        Hypervolume-based front-stability metric (independent of Ax's model)
  project_state.py    Workflow stage machine, decision log, S*, cached suggestions (JSON)
gui/
  main_window.py       App shell + persistent header (stage + S*)
  tabs/
    setup_tab.py        Edit campaign bounds/thresholds
    data_entry_tab.py   Enter raw replicate measurements
    suggestions_tab.py  Request/display next batch from Ax
    pareto_tab.py        Overlaid fronts + stability chart + S* lock-in
    decision_log_tab.py  Read-only decision history
data/
  measurements.csv     Permanent raw data -- the actual source of truth
  project_state.json   Current modeling choices -- safe to hand-edit or reset
```

## Design decisions worth knowing about before you extend this

- **The CSV never changes shape when you change the model.** Ax's
  `Client` is rebuilt from `measurements.csv` + `project_state.json` every
  time it's needed (fast at this data scale), rather than persisting Ax's
  own internal experiment format, which is more likely to shift between
  Ax versions than your own schema is.
- **Mixture constraint (sum to 1)** is enforced via Ax's
  `DerivedParameterConfig`, not a separate inequality constraint — see the
  comments in `experiment.py::build_client`.
- **Suggestions are cached, not regenerated on open.** Ax's acquisition
  optimization has some randomness across restarts; re-running it every
  time the app launches could silently change the displayed batch. New
  suggestions only appear when you click "Generate next batch."
- **Stability (hypervolume) is computed from raw data, not the fitted
  GP**, so the "has this plateaued" signal doesn't wobble just because the
  model refit differently — see the top of `stability.py` for the full
  reasoning.
- **Locking in S\* is a deliberate, confirmable GUI action** (with an
  "are you sure" dialog) and is logged as a permanent, append-only entry
  in the decision log — it's a chemistry judgment call, not something the
  app infers automatically.

## Known gaps / next steps (prototype, not finished product)

- No validation-blend workflow yet (testing A/B mixed in ratios once S* is
  chosen) — `Stage.VALIDATING_BLENDS` exists in the state machine but
  there's no tab built for it yet.
- No CSV import/export dialog in the GUI yet — `measurements.csv` is a
  plain file you can already copy/edit/version by hand, but a "load a
  different CSV" file picker isn't wired up.
- Objective thresholds (hypervolume reference points) are set once in the
  Setup tab; there's no guidance in-app for choosing them well, since
  that's chemistry judgment, not something the app can infer.
- Real replicate variance from your actual test rig will differ from the
  placeholder noise handling here — worth revisiting `aggregate_replicates`'s
  SEM calculation once you have real data with more than 1-2 batches.
