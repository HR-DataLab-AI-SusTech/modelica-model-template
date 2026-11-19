# A Modelica model for the twin platform

Start a model here. You write it in [Modelica](https://modelica.org), and this repository's CI
builds it into an FMU with [OpenModelica](https://openmodelica.org). An FMU is the
Functional Mock-up Interface's package of a model, its description and its solver in one file.
CI then holds the FMU to a reference result. On a tag, it registers the FMU as your model's next
version on the [twin platform](https://dtp.twinhub.nl), whose sandbox runs it when someone
requests a run on the twin's page. The guide
[Your model on the platform](https://dtp.twinhub.nl/learn/your-model-on-the-platform) walks
through it from the start.

The example is a tank whose water follows its inflow: `der(water) = (inflow - water) / tau_s`.

| File | What it is |
| --- | --- |
| `Model/package.mo` | the Modelica package; `Model.Tank` is the model |
| `build.mos` | OpenModelica's script that exports it: FMI 2.0 co-simulation, CVODE inside |
| `model.toml` | the FMU's name, the model's id, OpenModelica's pinned image, how each input meets a run's grid, what it replays and is scored against, the reference test's tolerance |
| `reference/inputs.csv`, `reference/outputs.csv` | inputs fed to the FMU one row per step, and the outputs they must give |
| `scripts/model.py` | `build`, `test`, `entry` and `register`, all reading `model.toml` |
| `.github/workflows/ci.yml` | the build and the test on every push; the registration on a tag `v*` |

## Start

1. **Use this template** (the green button on GitHub) to make your own repository, and clone it.
2. Install [uv](https://docs.astral.sh/uv/) and Docker, then run `uv sync`. It installs `twinhub`,
   the platform's SDK, from the platform's package index, and FMPy, which reads and runs FMUs.

## Change the model

Edit `Model/package.mo` in OMEdit (OpenModelica's editor) or as text. Each `input` and `output` is
what the platform feeds and reads, and each `parameter` is a run's parameter with its default. A
variable's `unit` must be the unit the kind declares for it. When you rename the model or the
package, change `build.mos` (the class and `fileNamePrefix`) and `fmu` in `model.toml` with it.

```sh
uv run python scripts/model.py build   # Tank.fmu, built for Linux x86-64 in OpenModelica's image
uv run python scripts/model.py entry   # the kind's models entry, read from the FMU
```

The build runs OpenModelica's Linux image. On an Apple-silicon Mac, Docker emulates it, which is
slower. `test` loads the FMU's Linux x86-64 binary, so it runs in CI, or on a Linux x86-64
machine:

```sh
uv run python scripts/model.py test           # the FMU against reference/, within the tolerance
uv run python scripts/model.py test --write   # reference/outputs.csv from the FMU, after a change
```

Rewrite the reference only when you mean the model's answer to change, and check the new numbers
before you commit them. For the tank, the answer is known: after the inflow steps from 20 to
60 °C at 300 s, `water = 60 - 40·exp(-(t - 300)/600)`. Put inputs into `reference/inputs.csv` that
exercise your model. Each row is held over the step to the next, as the sandbox holds a run's
inputs over each step.

## Put it on the platform

1. **The kind.** The model belongs to a kind, the platform's description of a sort of twin. Put
   the printed entry in the kind's `models`. `entry` takes what the FMU cannot say from
   `model.toml`: each input's `policy` and `max_gap_s`, the measured channel it `replays`, and
   the channel each output is scored against (`compares_to`). The guide shows a whole kind.
2. **Become a model author.** A platform admin makes you a model author of your org. Until then,
   a registration is refused.
3. **Tell CI where to register.** Under *Settings › Secrets and variables › Actions*, add the
   secret `TWINHUB_TOKEN`, your personal token from the platform's *Account › API tokens*, made with the **write** scope, and
   the variable `TWIN_ID`, a twin of the kind. Add `TWINHUB_URL` only for a platform other than
   `https://dtp.twinhub.nl`.
4. **Tag.** `git tag v1 && git push --tags`. CI builds, tests and registers the FMU, naming the
   commit it was built from and the OpenModelica release that built it. The platform refuses an
   FMU it cannot run, or one that differs from the kind's entry, and the job's log names the
   reason (the guide lists them).

A new version serves the runs requested after it. Request one on the twin's page (Models › Runs ›
Request a run), and compare its output with what was measured.

## Licence

Apache-2.0. A repository made from this template is yours to licence.
