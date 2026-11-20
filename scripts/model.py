"""Build, test, describe and register this repository's model, as `model.toml` says.

    uv run python scripts/model.py build      # the FMU, in OpenModelica's pinned image
    uv run python scripts/model.py test       # the FMU against reference/, within its tolerance
    uv run python scripts/model.py test --write   # reference/outputs.csv from the FMU
    uv run python scripts/model.py entry      # the kind's `models` entry, from the FMU
    uv run python scripts/model.py register   # the FMU as the model's next version
    uv run python scripts/model.py register --dry-run   # what `register` sends, sending nothing

`build` needs Docker and git: it fetches each library `model.toml` names at its commit into
build/libraries/, then builds with no network. `test` loads the FMU's Linux x86-64 binary, so it
runs on Linux x86-64 (CI does). `entry` reads only the FMU's description and runs anywhere;
it adds the table files the model reads (`[table_files.<parameter>]`) and the outputs that are its
position (`[position]`) when `model.toml` names them.
`register` needs TWINHUB_TOKEN, the personal token of a model author of your org, and TWIN_ID, a
twin of the kind; TWINHUB_URL is the platform (https://dtp.twinhub.nl by default). The version
names the commit it is built from, the repository that commit is in (from GitHub Actions, else
from an https GitHub `origin`), and carries docs/model.md and docs/diagram.svg when they exist.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "model.toml").read_text(encoding="utf-8"))
FMU = ROOT / CONFIG["fmu"]
BUILT_FROM = ROOT / "build" / "built-from"
INPUTS_CSV = ROOT / "reference" / "inputs.csv"
OUTPUTS_CSV = ROOT / "reference" / "outputs.csv"
GUIDE = ROOT / "docs" / "model.md"
DIAGRAM = ROOT / "docs" / "diagram.svg"
TABLE_FORMATS = ("tables", "road_route")


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


def commit() -> str:
    """The commit the working tree is, or "" when a file differs from it or is not in it
    (build.mos loads the whole package folder, an untracked file in it too)."""
    if git("status", "--porcelain", cwd=ROOT):
        return ""
    return git("rev-parse", "HEAD", cwd=ROOT)


def build() -> None:
    FMU.unlink(missing_ok=True)
    BUILT_FROM.unlink(missing_ok=True)
    source = commit()  # before the build: a commit made while it runs did not build it
    library_path = ":".join(libraries())
    uid = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "0:0"
    subprocess.run(  # noqa: S603 -- the image is model.toml's, pinned by digest
        [  # noqa: S607
            *("docker", "run", "--rm", "--network", "none", "--platform", "linux/amd64"),
            # Labelled, so a caller whose time runs out removes the container, not leaves it.
            *("--label", "modelica-model.build=1"),
            *("--user", uid, "-e", "HOME=/tmp", "-v", f"{ROOT}:/work", "-w", "/work"),
            *("-e", f"OPENMODELICALIBRARY={library_path}"),
            CONFIG["openmodelica_image"],
            *("omc", "build.mos"),
        ],
        check=True,
    )
    if not FMU.is_file():
        raise SystemExit(f"build.mos wrote no {FMU.name}: read omc's messages above")
    BUILT_FROM.parent.mkdir(exist_ok=True)
    BUILT_FROM.write_text(source + "\n", encoding="utf-8")
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
    if not given or given[0]["time"] != 0:
        raise SystemExit("reference/inputs.csv starts at time 0, as every run in the sandbox does")
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


def table_files(described: Any) -> dict[str, dict[str, str]]:
    """`[table_files.<parameter>]`: each a String parameter of the FMU that takes a table file's
    path, its `format` and an optional `description`."""
    props = described.parameters.get("properties", {})
    strings = sorted(n for n, p in props.items() if p.get("type") == "string")
    files = {}
    for name, spec in CONFIG.get("table_files", {}).items():
        where = f"model.toml's [table_files.{name}]"
        if name not in strings:
            raise SystemExit(
                f"{where} names no String parameter of {FMU.name}; its String parameters are "
                f"{strings or 'none'} (a top-level `parameter String` with a literal default)"
            )
        if unknown := sorted(set(spec) - {"format", "description"}):
            raise SystemExit(f"{where} has unknown keys {unknown}: it takes format, description")
        if spec.get("format") not in TABLE_FORMATS:
            raise SystemExit(f"{where} needs format = one of {list(TABLE_FORMATS)}")
        files[name] = {k: str(v) for k, v in spec.items()}
    return files


def position(described: Any) -> tuple[str, str] | None:
    """`[position]`: the outputs that are the model's latitude and longitude, in degrees."""
    spec = CONFIG.get("position")
    if spec is None:
        return None
    if sorted(spec) != ["lat", "lon"]:
        raise SystemExit("model.toml's [position] names two outputs, lat and lon, and nothing else")
    if unknown := [o for o in (spec["lat"], spec["lon"]) if o not in described.outputs]:
        raise SystemExit(
            f"model.toml's [position] names {unknown}, not outputs of {FMU.name}; its outputs "
            f"are {sorted(described.outputs)}"
        )
    return spec["lat"], spec["lon"]


def entry() -> dict[str, Any]:
    """The kind's entry; anything FMPy prints while it reads the FMU goes to stderr, so stdout
    holds the entry alone (FMPy prints a warning on Linux on Arm)."""
    sys.stdout.flush()
    stdout = os.dup(1)
    os.dup2(2, 1)
    try:
        described = model().describe()
        # Only what model.toml declares, so a model with neither runs on an SDK that has neither.
        declared: dict[str, Any] = {}
        if files := table_files(described):
            declared["table_files"] = files
        if (where := position(described)) is not None:
            declared["position"] = where
        entry: dict[str, Any] = described.manifest_entry(
            CONFIG["model_id"],
            replays=CONFIG.get("replays", {}),
            compares_to=CONFIG.get("compares_to", {}),
            **declared,
        )
    finally:
        sys.stdout.flush()
        os.dup2(stdout, 1)
        os.close(stdout)
    return entry


def source_repository() -> str | None:
    """The https URL of the repository the commit is in: GitHub Actions' server and repository,
    else `origin` when it is an https GitHub URL (one with credentials in it is not used)."""
    server, repo = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY")
    if server and repo:
        return f"{server}/{repo}"
    try:
        origin = git("remote", "get-url", "origin", cwd=ROOT).removesuffix(".git")
    except subprocess.CalledProcessError:
        return None
    return origin if re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", origin) else None


def register(dry_run: bool) -> None:
    token, twin = os.environ.get("TWINHUB_TOKEN"), os.environ.get("TWIN_ID")
    if not dry_run and (not token or not twin):
        raise SystemExit(
            "register needs TWINHUB_TOKEN (a model author's personal token) and TWIN_ID; in "
            "GitHub, the repository's secret TWINHUB_TOKEN and its variable TWIN_ID"
        )
    built = BUILT_FROM.read_text(encoding="utf-8").strip() if BUILT_FROM.is_file() else ""
    sha = commit()
    stale = (
        f"{FMU.name} was not built from this commit with a clean tree: commit your changes, "
        "then build again before you register, so the version names what built it"
    )
    if not built or built != sha:
        if not dry_run:
            raise SystemExit(stale)
        print(f"register would refuse: {stale}")
    repository = source_repository()
    guide = GUIDE if GUIDE.is_file() else None
    diagram = DIAGRAM if DIAGRAM.is_file() else None
    print("would send:" if dry_run else "sending:")
    for field, value in {
        "model": f"{CONFIG['model_id']} of twin {twin or '$TWIN_ID'}",
        "fmu": FMU.name,
        "source_commit": sha or "(none: the tree differs from HEAD)",
        "openmodelica_version": openmodelica_version(),
        "source_repository": repository or "(none: not in GitHub Actions, no https GitHub origin)",
        "guide": guide.relative_to(ROOT) if guide else "(none: no docs/model.md)",
        "diagram": diagram.relative_to(ROOT) if diagram else "(none: no docs/diagram.svg)",
    }.items():
        print(f"  {field}: {value}")
    if dry_run:
        return
    from twinhub import Client

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
            source_repository=repository,
            guide=guide,
            diagram=diagram,
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
            register(dry_run=False)
        case ["register", "--dry-run"]:
            register(dry_run=True)
        case _:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
