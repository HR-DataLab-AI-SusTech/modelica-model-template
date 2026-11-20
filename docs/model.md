# The tank model

A tank of water fed by an inflow, whose temperature follows the inflow's with a time constant: the
example this template builds into an FMU (FMI 2.0 co-simulation) that the platform runs in its
sandbox. Make it your own by replacing the model, and this guide with it.

## Its blocks

| Block | Where | What it does |
| --- | --- | --- |
| The tank | `Model/package.mo`: model `Model.Tank` | the water's temperature, a first-order lag behind the inflow's |

## The key equation

`der(water) = (inflow - water) / tau_s`, from `water = water_start` at the start. After the inflow
steps to a new temperature, the water closes about 63 % of the gap in `tau_s` seconds: for the
reference test's step from 20 to 60 °C at 300 s, `water = 60 - 40·exp(-(t - 300)/600)`.

## Inputs, outputs and parameters

| Kind | Name | Unit or default | What it is |
| --- | --- | --- | --- |
| input | `inflow` | °C | the inflow's temperature, replayed from the measured `sensor:inflow` |
| output | `water` | °C | the water's temperature, scored against the measured `sensor:water` |
| parameter | `tau_s` | 600 s | the time constant, at least 0 |
| parameter | `water_start` | 20 °C | the water's temperature at the start |

## When you change it

- **Units are the kind's.** An input's or output's `unit` must be the unit the kind declares for
  its channel, or the platform refuses the version.
- **A parameter is top level and unprotected** to show in the run dialog, with `min`/`max` where
  they hold.
- **A new input** takes a `[inputs.<name>]` in `model.toml` (how a replayed series meets the
  run's grid) and, when it replays a measured channel, an entry under `[replays]`.
- **The reference moves with care.** `reference/outputs.csv` holds the inflow step to 0.001 °C; a
  change that moves it is rewritten with `scripts/model.py test --write`, and the new numbers are
  checked before they are committed.

## How a change reaches the platform

1. Edit `Model/`, then `uv run python scripts/model.py build` (OpenModelica in Docker).
2. Push. CI builds the FMU, holds it to `reference/` (`model.py test`) and writes the kind's
   entry, keeping both as the `fmu` artifact. A changed input, output or unit changes the entry:
   put it in the kind's `models` first.
3. Tag `vN` on that commit and push the tag: CI registers the FMU as the model's next version on
   the platform, with this guide and `docs/diagram.svg`, where a run picks it.
