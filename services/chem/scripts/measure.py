"""Measure the running service the way the README reports it: startup, latency, memory, a soak.

Starts `python -m decimer_chem` itself on a free port (so it measures a cold start), drives it over
HTTP, reads memory from /proc, and prints plain text. Standard library only.

    JAVA_HOME=~/.local/opt/jre uv run python scripts/measure.py [--soak 600]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

DRUGS = [
    "CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O",  # ibuprofen
    "CC(=O)Nc1ccc(O)cc1",  # paracetamol
    "CN1CCC[C@H]1c1cccnc1",  # nicotine
    "OC(=O)c1ccccc1O",  # salicylic acid
    "C[C@H](N)Cc1ccccc1",  # amphetamine
    "CC1=C2[C@@]([C@]([C@H]([C@@H]3[C@]4([C@H](OC4)C[C@@H]([C@]3(C(=O)[C@@H]2OC(=O)C)C)O)"
    "OC(=O)C)OC(=O)c5ccccc5)(C[C@@H]1OC(=O)[C@@H]([C@H](c6ccccc6)NC(=O)c7ccccc7)O)O)(C)C",  # taxol
    "C[C@H]1CC[C@@]2([C@H]([C@@H]3[C@@H](O2)C[C@@H]4[C@@]3(CC[C@H]5[C@H]4CC=C6[C@@]5(CC[C@@H]"
    "(C6)O[C@H]7[C@@H]([C@H]([C@@H]([C@H](O7)CO)O)O)O[C@H]8[C@@H]([C@@H]([C@H]([C@@H](O8)C)O)O)O)"
    "C)C)C)OC1",  # a steroidal saponin
    "C1C2CC3CC1CC(C2)C3",  # adamantane
    "N[C@@H](C)C(=O)" + "N[C@@H](CC(C)C)C(=O)" * 18 + "O",  # a 19-mer peptide
    "OC(=O)CCCCCCCCC=C",  # an acid
    "c1ccc2c(c1)[nH]c1ccccc12",  # carbazole
]
VANCOMYCIN = (
    "C[C@H]1[C@H]([C@@](C[C@@H](O1)O[C@H]2[C@H]([C@@H]([C@H](O[C@@H]2OC3=C4C=C5C=C3OC6=C(C=C(C=C6)"
    "[C@H]([C@H](C(=O)N[C@H](C(=O)N[C@H]5C(=O)N[C@@H]7C8=CC(=C(C=C8)O)C9=C(C=C(C=C9O)O)[C@H](NC(="
    "O)[C@H]([C@@H](C1=CC(=C(O4)C=C1)Cl)O)NC7=O)C(=O)O)CC(=O)N)NC(=O)[C@@H](CC(C)C)NC)O)Cl)CO)O)O)"
    "(C)N)O"
)
DENDRIMER = (
    "C(CN(CCC(=O)NCCN(CCC(=O)OC)CCC(=O)OC)CCC(=O)NCCN(CCC(=O)OC)CCC(=O)OC)N(CCC(=O)NCCN(CCC(=O)OC)"
    "CCC(=O)OC)CCC(=O)NCCN(CCC(=O)OC)CCC(=O)OC"
)
PATHOLOGICAL = {
    "vancomycin (declined by Orthonym in ~9 s)": VANCOMYCIN,
    "PEG-50 (STRUCTURE_TOO_LARGE after ~10 s)": "OCC" + "OCC" * 50 + "O",
    "PAMAM dendrimer (~30 s)": DENDRIMER,
}


def post(port: int, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as answer:  # noqa: S310
            return answer.status, json.load(answer)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def rss_kb(pid: int, field: str = "VmRSS") -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    raise KeyError(field)


def children(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False)
    return [int(x) for x in out.stdout.split()]


def engine_pid(server: int) -> int:
    return max(children(server), key=rss_kb)  # the resource tracker is also a child; it is tiny


def mb(kb: int) -> str:
    return f"{kb / 1024:.0f} MB"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--soak", type=int, default=600, help="number of names in the soak")
    args = parser.parse_args()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    began = time.monotonic()
    server = subprocess.Popen(
        [sys.executable, "-m", "decimer_chem"],
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):  # noqa: S310
                    break
            except OSError:
                time.sleep(0.05)
        print(f"process start -> /healthz answers: {time.monotonic() - began:.2f} s")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=30):  # noqa: S310
            pass
        engine = engine_pid(server.pid)
        print(
            f"RSS after start: service {mb(rss_kb(server.pid))}, "
            f"naming process {mb(rss_kb(engine))}"
        )

        with (REPO / "golden" / "labels.csv").open(newline="") as handle:
            golden = [r["smiles"] for r in csv.DictReader(handle) if r["number"].isdigit()][:12]

        singles = []
        for smiles in golden:
            t = time.perf_counter()
            status, body = post(port, "/name", {"smiles": smiles})
            singles.append((time.perf_counter() - t) * 1000)
            assert status == 200, body
        print(
            "POST /name, the 12 Figure 2 compounds (ms, HTTP round trip): "
            f"min {min(singles):.0f}, median {statistics.median(singles):.0f}, "
            f"max {max(singles):.0f}"
        )
        t = time.perf_counter()
        status, body = post(port, "/names", {"smiles": golden + DRUGS[:8]})
        print(
            f"POST /names, 20 structures: {(time.perf_counter() - t) * 1000:.0f} ms "
            f"(status {status})"
        )

        for label, smiles in PATHOLOGICAL.items():
            t = time.perf_counter()
            status, body = post(port, "/name", {"smiles": smiles})
            print(
                f"pathological {label}: {time.perf_counter() - t:.1f} s -> {status} "
                f"name={body.get('name')!r} reason={body.get('reason')!r}"
            )
        engine = engine_pid(server.pid)  # it may have been replaced if a kill happened
        print(f"RSS before soak: naming process {mb(rss_kb(engine))}")

        pool = golden + DRUGS
        sent = 0
        per_name = []
        print(f"soak: {args.soak} names in batches of 20 (RSS of the naming process)")
        while sent < args.soak:
            batch = [pool[(sent + i) % len(pool)] for i in range(20)]
            t = time.perf_counter()
            status, body = post(port, "/names", {"smiles": batch})
            assert status == 200, body
            per_name.append((time.perf_counter() - t) * 1000 / len(batch))
            sent += len(batch)
            if sent % 100 == 0:
                print(
                    f"  after {sent:>5} names: {mb(rss_kb(engine))} "
                    f"(peak {mb(rss_kb(engine, 'VmHWM'))})"
                )
        print(
            f"soak per name (mean of batch means): {statistics.mean(per_name):.0f} ms, "
            f"service RSS {mb(rss_kb(server.pid))}"
        )
    finally:
        server.send_signal(signal.SIGTERM)
        try:
            output, _ = server.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            output = ""
    for line in output.splitlines():
        if '"naming process ready"' in line:
            record = json.loads(line)
            print(
                f"naming process ready {record['seconds']} s after it was spawned "
                f"(engine start inside it: {record['start_seconds']} s: jar hashes, "
                "Orthonym import, JVM start, OPSIN warm-up)"
            )


if __name__ == "__main__":
    main()
