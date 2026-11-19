"""Build, test, describe and register this repository's model, as `model.toml` says.

    uv run python scripts/model.py build      # the FMU, in OpenModelica's pinned image
    uv run python scripts/model.py test       # the FMU against reference/, within its tolerance
    uv run python scripts/model.py test --write   # reference/outputs.csv from the FMU
    uv run python scripts/model.py entry      # the kind's `models` entry, from the FMU
    uv run python scripts/model.py register   # the FMU as the model's next version

`build` needs Docker and git: it fetches each library `model.toml` names at its commit into
build/libraries/, then builds with no network. `test` loads the FMU's Linux x86-64 binary, so it
runs on Linux x86-64 (CI does). `entry` reads only the FMU's description and runs anywhere.
`register` needs TWINHUB_TOKEN, the personal token of a model author of your org, and TWIN_ID, a
twin of the kind; TWINHUB_URL is the platform (https://dtp.twinhub.nl by default). The version
names the commit it is built from.
"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "model.toml").read_text(encoding="utf-8"))
FMU = ROOT / CONFIG["fmu"]
INPUTS_CSV = ROOT / "reference" / "inputs.csv"
OUTPUTS_CSV = ROOT / "reference" / "outputs.csv"


def openmodelica_version() -> str:
    """The release in the image's tag: `…:v1.27.1-minimal@sha256:…` is 1.27.1."""
    tag = CONFIG["openmodelica_image"].split("@")[0].rsplit(":", 1)[1]
    return tag.removeprefix("v").split("-")[0]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(  # noqa: S603 -- git, with model.toml's repositories and commits
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def libraries() -> list[str]:
    """Each library at its commit in build/libraries/<name>, fetched only when it is not there
    yet; their paths inside the build's container, for OPENMODELICALIBRARY."""
    paths = []
    for name, lib in CONFIG.get("libraries", {}).items():
        where = ROOT / "build" / "libraries" / name
        if not (where / ".git").is_dir():
            where.mkdir(parents=True, exist_ok=True)
            git("init", "--quiet", cwd=where)
            git("remote", "add", "origin", lib["git"], cwd=where)
        git("remote", "set-url", "origin", lib["git"], cwd=where)
        try:
            here = git("rev-parse", "HEAD", cwd=where)
        except subprocess.CalledProcessError:
            here = ""
        if here != lib["commit"]:
            print(f"fetching {name} at {lib['commit'][:12]} from {lib['git']}")
            git("fetch", "--quiet", "--depth", "1", "origin", lib["commit"], cwd=where)
            git("checkout", "--quiet", "--force", "--detach", "FETCH_HEAD", cwd=where)
        if git("rev-parse", "HEAD", cwd=where) != lib["commit"]:
            raise SystemExit(f"library {name} is not at {lib['commit']}")
        paths.append(f"/work/build/libraries/{name}")
    return paths


def build() -> None:
    FMU.unlink(missing_ok=True)
    library_path = ":".join(libraries())
    uid = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "0:0"
    subprocess.run(  # noqa: S603 -- the image is model.toml's, pinned by digest
        [  # noqa: S607
            *("docker", "run", "--rm", "--network", "none", "--platform", "linux/amd64"),
            *("--user", uid, "-e", "HOME=/tmp", "-v", f"{ROOT}:/work", "-w", "/work"),
            *("-e", f"OPENMODELICALIBRARY={library_path}"),
            CONFIG["openmodelica_image"],
            *("omc", "build.mos"),
        ],
        check=True,
    )
    if not FMU.is_file():
        raise SystemExit(f"build.mos wrote no {FMU.name}: read omc's messages above")
    print(f"built {FMU.name}, {FMU.stat().st_size} bytes")


def model() -> Any:
    from twinhub.sim import Input
    from twinhub.sim.fmu import FmuModel

    described = FmuModel(FMU).describe()
    inputs = {
        name: Input(unit=described.inputs[name].unit, **spec)
        for name, spec in CONFIG.get("inputs", {}).items()
    }
    return FmuModel(FMU, inputs=inputs)


def rows(path: Path) -> list[dict[str, float]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


def simulate() -> tuple[list[str], list[dict[str, float]]]:
    """Each row of reference/inputs.csv held over the step to the next, as the sandbox steps a run
    with its inputs held over each step; the outputs at the end of every step."""
    fmu = model()
    described = fmu.describe()
    defaults = {
        name: schema["default"]
        for name, schema in described.parameters["properties"].items()
        if "default" in schema
    }
    given = rows(INPUTS_CSV)
    names = list(described.inputs)
    outputs = list(described.outputs)
    out: list[dict[str, float]] = []
    fmu.setup(defaults, None, {n: given[0][n] for n in names})
    try:
        for here, there in zip(given, given[1:], strict=False):
            t, h = here["time"], there["time"] - here["time"]
            answered = fmu.do_step(t, h, {n: here[n] for n in names})
            out.append({"time": t + h} | {o: float(answered[o]) for o in outputs})
    finally:
        fmu.terminate()
    return outputs, out


def test(write: bool) -> None:
    outputs, got = simulate()
    if write:
        with OUTPUTS_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["time", *outputs], lineterminator="\n")
            w.writeheader()
            w.writerows({k: f"{v:.9g}" for k, v in r.items()} for r in got)
        print(f"wrote {OUTPUTS_CSV.relative_to(ROOT)}: {len(got)} rows")
        return
    want = rows(OUTPUTS_CSV)
    tolerance = float(CONFIG["reference"]["tolerance"])
    if [r["time"] for r in got] != [r["time"] for r in want]:
        raise SystemExit("reference/outputs.csv is not at the end of every step of inputs.csv")
    worst = {o: max(abs(g[o] - w[o]) for g, w in zip(got, want, strict=True)) for o in outputs}
    for o, err in worst.items():
        print(f"{o}: largest difference {err:.3g} (tolerance {tolerance:g})")
    if any(not math.isfinite(e) or e > tolerance for e in worst.values()):
        raise SystemExit("the FMU does not give reference/outputs.csv within the tolerance")


def entry() -> dict[str, Any]:
    entry: dict[str, Any] = (
        model()
        .describe()
        .manifest_entry(
            CONFIG["model_id"],
            replays=CONFIG.get("replays", {}),
            compares_to=CONFIG.get("compares_to", {}),
        )
    )
    return entry


def commit() -> str:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def register() -> None:
    from twinhub import Client

    token, twin = os.environ.get("TWINHUB_TOKEN"), os.environ.get("TWIN_ID")
    if not token or not twin:
        raise SystemExit(
            "register needs TWINHUB_TOKEN (a model author's personal token) and TWIN_ID; in "
            "GitHub, the repository's secret TWINHUB_TOKEN and its variable TWIN_ID"
        )
    sha = commit()
    repo = os.environ.get("GITHUB_REPOSITORY", ROOT.name)
    with Client(
        token=token,
        user_agent=f"modelica-model-template ({repo})",
        base_url=os.environ.get("TWINHUB_URL") or "https://dtp.twinhub.nl",
    ) as hub:
        version = hub.register_model_version(
            twin,
            CONFIG["model_id"],
            fmu=FMU,
            source_commit=sha,
            openmodelica_version=openmodelica_version(),
        )
    print(f"version {version.version} of {CONFIG['model_id']}, built from {sha[:12]}")


def main(argv: list[str]) -> None:
    match argv:
        case ["build"]:
            build()
        case ["test"]:
            test(write=False)
        case ["test", "--write"]:
            test(write=True)
        case ["entry"]:
            print(json.dumps(entry(), indent=2))
        case ["register"]:
            register()
        case _:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
