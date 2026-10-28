"""Checks the properties of deploy/compose.yaml that a typo could quietly break.

    python3 -I deploy/check-compose.py              # compose.yaml with dummy secrets (CI, before a commit)
    python3 -I deploy/check-compose.py --env deploy/.env      # the same checks on YOUR settings (before an `up`)

It copies compose.yaml into a temporary directory with dummy secrets (deploy/.env is never read unless you name it),
runs `docker compose config --format json` there (nothing is built or started, no container is touched) and checks
the result, twice: with an .env that holds only the secrets (so every default in compose.yaml is what is checked) and
with .env.example plus the secrets (so the documented settings are too). With `--env FILE` it checks that file, once.
Exit 0 and "ok" when every check holds, 1 and the list of problems otherwise. COMPOSE="/usr/libexec/docker/cli-plugins/
docker-compose" (or any other command) picks another Compose; Compose v2.3.3 and v2.39.2 print the same JSON for
these fields.

What it keeps true:
  * the worker and the fast worker need chem to have STARTED, never to be healthy: they run with chem down;
  * the only published ports are the edge's two, on 127.0.0.1; GlitchTip and its Postgres publish nothing;
  * the only network with a route out is `public`, and only the edge is on it;
  * GlitchTip is on two internal networks (its Postgres's and the edge's), its Postgres on one, and the api and the
    workers reach it only through the `glitchtip-gate`; both images are pinned by digest, the files the compose
    file mounts exist;
  * the ingest sidecar has one internal network (the api's), no volume of job data, a read-only root file
    system and no secret in its environment;
  * every service that gets deploy/.env as env_file blanks GlitchTip's three secrets;
  * two services that call each other share exactly ONE network (docker's DNS could answer with the address on
    the other one: the api must see the edge on `app`, where TRUSTED_PROXY_CIDR is);
  * the usage counter's Redis has a volume, and the api and the workers point at it;
  * the api's memory limit is the small one that goes with the sidecar (and the in-process fallback, an empty
    INGEST_SIDECAR_URL, comes with the big one), and GlitchTip with its Postgres stays within about 0.5 GB.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent
DUMMY = {
    "IP_HASH_SECRET": "0" * 64,
    "REDIS_PASSWORD": "1" * 48,
    "GLITCHTIP_SECRET_KEY": "2" * 64,
    "GLITCHTIP_DB_PASSWORD": "3" * 48,
    "GLITCHTIP_ADMIN_EMAIL": "operator@example.org",
    "GLITCHTIP_ADMIN_PASSWORD": "4" * 48,
    # set, so that a service that is not to report (the sweep, the sidecar) and does not blank it is seen
    "SENTRY_DSN": "http://" + "5" * 32 + "@glitchtip-gate:8000/1",
}
SECRETS_OF_GLITCHTIP = ("GLITCHTIP_SECRET_KEY", "GLITCHTIP_DB_PASSWORD", "GLITCHTIP_ADMIN_PASSWORD")
MIB = 1024 * 1024
# who calls whom by name (the ones that must share exactly one network)
CALLS = {
    "edge": ["api", "glitchtip"],
    "api": ["redis", "usage", "chem", "ingest", "glitchtip-gate"],
    "worker": ["redis", "usage", "seg", "cls", "ocsr", "chem", "glitchtip-gate"],
    "worker-fast": ["redis", "usage", "seg", "cls", "ocsr", "chem", "glitchtip-gate"],
    "sweep": ["redis"],
    "glitchtip-gate": ["glitchtip"],
    "glitchtip": ["glitchtip-db"],
}
IN_PROCESS_API_MEMORY = 4608 * MIB
GLITCHTIP_MEMORY_LIMIT = 640 * MIB


def load_config(
    compose: list[str], *, example: bool, env_file: Path | None = None
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory) / "deploy"
        folder.mkdir()
        shutil.copy(DEPLOY / "compose.yaml", folder / "compose.yaml")
        if env_file is not None:
            text = env_file.read_text()
        else:
            text = (DEPLOY / ".env.example").read_text() if example else ""
            for name, value in DUMMY.items():
                text = re.sub(rf"^{name}=.*$", "", text, flags=re.MULTILINE)
                text += f"\n{name}={value}\n"
        (folder / ".env").write_text(text)
        result = subprocess.run(  # noqa: S603  (the user's own docker compose)
            [
                *compose,
                "-p",
                "decimer-next",
                "-f",
                str(folder / "compose.yaml"),
                "--env-file",
                str(folder / ".env"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        sys.exit(f"check-compose: `docker compose config` failed:\n{result.stderr}")
    loaded: dict[str, Any] = json.loads(result.stdout)
    return loaded


def networks_of(service: dict[str, Any]) -> set[str]:
    declared = service.get("networks") or {}
    return set(declared if isinstance(declared, (dict, list)) else [])


def depends(service: dict[str, Any]) -> dict[str, str]:
    declared = service.get("depends_on") or {}
    if isinstance(declared, list):
        return {name: "service_started" for name in declared}
    return {name: item.get("condition", "service_started") for name, item in declared.items()}


def environment(service: dict[str, Any]) -> dict[str, Any]:
    declared = service.get("environment") or {}
    if isinstance(declared, list):
        return dict(item.split("=", 1) for item in declared)
    return dict(declared)


def memory(service: dict[str, Any]) -> int:
    return int(service["mem_limit"])


def check(config: dict[str, Any], *, defaults: bool = True) -> list[str]:
    services: dict[str, dict[str, Any]] = config["services"]
    networks: dict[str, dict[str, Any]] = config["networks"]
    problems: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    # --- the worker does not depend on chem ---------------------------------------------------------
    for name, service in services.items():
        for target, condition in depends(service).items():
            if target == "chem":
                need(
                    condition == "service_started",
                    f"{name} depends on chem with {condition}: it must be service_started, so that "
                    "the worker runs with chem down",
                )
    for name in ("worker", "worker-fast"):
        need("chem" in depends(services[name]), f"{name} no longer waits for chem to start")

    # --- ports, networks, egress --------------------------------------------------------------------
    published = {
        name: [(p["host_ip"], int(p["target"])) for p in service.get("ports", [])]
        for name, service in services.items()
        if service.get("ports")
    }
    need(
        set(published) == {"edge"}, f"only the edge may publish ports, these do: {sorted(published)}"
    )
    need(
        sorted(published.get("edge", [])) == [("127.0.0.1", 8080), ("127.0.0.1", 8081)],
        f"the edge publishes {published.get('edge')}: expected 8080 and 8081, on 127.0.0.1 only",
    )
    for name, definition in networks.items():
        if name != "public":
            need(bool(definition.get("internal")), f"network {name} has a route out (not internal)")
    on_public = [n for n, s in services.items() if "public" in networks_of(s)]
    need(on_public == ["edge"], f"only the edge may be on `public`, these are: {on_public}")

    # --- GlitchTip -----------------------------------------------------------------------------------
    glitchtip, database = services["glitchtip"], services["glitchtip-db"]
    gate = services["glitchtip-gate"]
    need(
        networks_of(glitchtip) == {"glitchtip-db", "glitchtip-edge"},
        f"glitchtip is on {sorted(networks_of(glitchtip))}: its Postgres, and the edge and the gate "
        "(never glitchtip-sdk: the api and the workers reach it only through the gate)",
    )
    need(networks_of(database) == {"glitchtip-db"}, "glitchtip-db must be on glitchtip-db alone")
    need(
        networks_of(gate) == {"glitchtip-sdk", "glitchtip-edge"},
        f"glitchtip-gate is on {sorted(networks_of(gate))}",
    )
    for name in ("api", "worker", "worker-fast"):
        on = networks_of(services[name])
        need("glitchtip-sdk" in on, f"{name} is not on glitchtip-sdk, so it cannot report")
        need(
            not on & {"glitchtip-edge", "glitchtip-db"},
            f"{name} must reach GlitchTip only through the gate (it is on {sorted(on)})",
        )
        dsn = environment(services[name]).get("SENTRY_DSN", "")
        need(
            dsn == "" or re.fullmatch(r"https?://[0-9a-f]+@glitchtip-gate:8000/\d+", dsn) is not None,
            f"{name}: SENTRY_DSN must name the gate (glitchtip-gate:8000), not GlitchTip",
        )
    for name in ("glitchtip", "glitchtip-db", "glitchtip-gate"):
        need("@sha256:" in services[name]["image"], f"{name}: the image is not pinned by digest")
    gate_volumes = [volume.get("target") for volume in gate.get("volumes", [])]
    need(gate_volumes == ["/etc/nginx/nginx.conf"], f"the gate mounts {gate_volumes}: its configuration")
    need(bool(gate.get("read_only")), "the gate must have a read-only root file system")
    glitchtip_env = environment(glitchtip)
    need(glitchtip_env.get("VALKEY_URL") == "", "glitchtip must not use Valkey/Redis (VALKEY_URL empty)")
    need(
        glitchtip_env.get("GLITCHTIP_EMBED_WORKER") == "true",
        "glitchtip runs its worker in the same process",
    )
    need(glitchtip_env.get("GLITCHTIP_RETENTION_DAYS") == "90", "event retention is not 90 days")
    need(
        glitchtip_env.get("ENABLE_USER_REGISTRATION") == "false",
        "glitchtip registration must be off",
    )
    need(
        glitchtip_env.get("CORS_ORIGIN_ALLOW_ALL") == "false",
        "glitchtip must not allow every origin (CORS_ORIGIN_ALLOW_ALL=false)",
    )
    need(
        '"enabled": true' in glitchtip_env.get("GLITCHTIP_PII_SCRUB_DEFAULT", ""),
        "glitchtip's server-side scrubbing is not on for every project (GLITCHTIP_PII_SCRUB_DEFAULT)",
    )
    # The account exists before the web server starts: GlitchTip is open to registration while it has none.
    command = " ".join(glitchtip.get("command", []))
    first = [command.find(step) for step in ("migrate", "createcachetable", "glitchtip-init.py", "start.sh")]
    need(
        all(position >= 0 for position in first) and first == sorted(first),
        "glitchtip must run its migrations, then glitchtip-init.py, and only then start the web server",
    )
    need("&&" in command and "||" not in command and ";" not in command, "glitchtip's start must stop if the init fails")
    for volume in glitchtip.get("volumes", []):
        need(volume.get("read_only") is True, "glitchtip mounts only read-only files")
    need(
        memory(glitchtip) + memory(database) <= GLITCHTIP_MEMORY_LIMIT,
        f"glitchtip and its Postgres together may take {GLITCHTIP_MEMORY_LIMIT // MIB} MiB (about 0.5 GB), "
        f"they have {(memory(glitchtip) + memory(database)) // MIB}",
    )
    need(
        "GLITCHTIP_WEB_PROJECT_ID" in environment(services["edge"]),
        "the edge is not told which GlitchTip project the browsers may report to",
    )
    # the files the compose file mounts from this directory are there
    for name, service in services.items():
        for volume in service.get("volumes", []):
            if volume.get("type") == "bind":
                file = Path(volume["source"]).name
                need(
                    (DEPLOY / file).exists() or (DEPLOY / "nginx" / file).exists(),
                    f"{name} mounts {file}, which is not in deploy/ or deploy/nginx/",
                )

    # --- the ingest sidecar --------------------------------------------------------------------------
    ingest = services["ingest"]
    need(networks_of(ingest) == {"ingest"}, f"ingest is on {sorted(networks_of(ingest))}")
    need(bool(ingest.get("read_only")), "ingest must have a read-only root file system")
    mounts = [volume.get("source") for volume in ingest.get("volumes", [])]
    need(mounts == ["ingest-scratch"], f"ingest mounts {mounts}: its scratch volume and nothing else")
    ingest_env = environment(ingest)
    for secret in ("REDIS_PASSWORD", "IP_HASH_SECRET", "SENTRY_DSN"):
        need(ingest_env.get(secret) == "", f"ingest must blank {secret} (env_file would hand it over)")
    api = services["api"]
    need("ingest" in networks_of(api), "the api is not on the ingest network")
    sidecar = environment(api).get("INGEST_SIDECAR_URL")
    if defaults:
        need(sidecar == "tcp://ingest:9000", "the api does not use the sidecar by default")
    # The sidecar and the small limit go together, and so do the in-process fallback (an empty URL) and
    # the big one: the api then runs the parsers' children itself (0.5 + 2 x 1.5 + 2 x 0.45 GB).
    if sidecar == "":
        need(
            memory(api) >= IN_PROCESS_API_MEMORY,
            f"INGEST_SIDECAR_URL is empty (the api parses in children of its own) but the api has only "
            f"{memory(api) // MIB} MiB: set API_MEM_LIMIT=4608m",
        )
    else:
        need(memory(api) <= 2048 * MIB, "the api has the memory of the in-process parsers, not the sidecar's")
    need(
        depends(api).get("ingest") == "service_healthy",
        "the api must wait for the sidecar to be healthy",
    )
    need("models" not in networks_of(api), "the api must not be on the models network")

    # --- secrets that are not the service's -----------------------------------------------------------
    # `docker compose config` has already merged each env_file into `environment`, so a service that gets
    # deploy/.env (api, worker, worker-fast, sweep, ingest) shows GlitchTip's secrets unless it blanks them.
    for name, service in services.items():
        env = environment(service)
        for secret in SECRETS_OF_GLITCHTIP:
            if name == "glitchtip" and secret == "GLITCHTIP_ADMIN_PASSWORD":
                continue  # the one service that makes the account
            need(
                env.get(secret) in (None, ""),
                f"{name} has {secret} (deploy/.env as env_file): blank it in `environment`",
            )
    sweep_dsn = environment(services["sweep"]).get("SENTRY_DSN")
    need(sweep_dsn in (None, ""), "the sweep does not report: it must not be handed the DSN")

    # --- who shares what ------------------------------------------------------------------------------
    for caller, callees in CALLS.items():
        for callee in callees:
            shared = networks_of(services[caller]) & networks_of(services[callee])
            need(
                len(shared) == 1,
                f"{caller} and {callee} share {sorted(shared) or 'no network'}: exactly one is needed "
                "(with two, docker's DNS may answer with the address on the other one)",
            )

    # --- the usage counter ----------------------------------------------------------------------------
    usage = services["usage"]
    need(
        [v.get("target") for v in usage.get("volumes", [])] == ["/data"],
        "the usage Redis needs its volume at /data",
    )
    need("appendonly yes" in " ".join(usage.get("entrypoint", [])), "the usage Redis must persist")
    for name in ("api", "worker", "worker-fast"):
        url = environment(services[name]).get("USAGE_REDIS_URL", "")
        need(bool(re.fullmatch(r"redis://:[^@]+@usage:6379/0", url)), f"{name}: USAGE_REDIS_URL is {url!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    env_file: Path | None = None
    if arguments[:1] == ["--env"] and len(arguments) == 2:
        env_file = Path(arguments[1])
        if not env_file.is_file():
            sys.exit(f"check-compose: {env_file} is not a file")
    elif arguments:
        sys.exit("usage: check-compose.py [--env FILE]")
    compose = shlex.split(os.environ.get("COMPOSE", "docker compose"))
    problems: list[str] = []
    for example in [False] if env_file else [False, True]:
        label = str(env_file) if env_file else (".env.example" if example else "secrets only")
        config = load_config(compose, example=example, env_file=env_file)
        problems += [f"{problem} [env: {label}]" for problem in check(config, defaults=env_file is None)]
    if problems:
        print("check-compose: problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check-compose: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
