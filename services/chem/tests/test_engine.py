"""The engine inside the naming process: jars, environment, default tier, soft time limit.

These tests import Orthonym (RDKit) but never start a JVM and never touch the network.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import orthonym
import orthonym.jars
import pytest

from decimer_chem.engine import (
    ALLOWED_ORTHONYM_ENV,
    JAVA_OPTION_ENV,
    OrthonymEngine,
    apply_jar_policy,
    check_environment,
    check_jars,
    disable_runtime_caches,
    refuse_java_subprocess,
)
from decimer_chem.results import INVALID_SMILES_REASON, TIMEOUT_REASON
from decimer_chem.settings import Settings

SPECS = orthonym.jars.JARS


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any attempt to download anything fails the test that made it."""
    attempts: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> None:
        attempts.append("download")
        raise AssertionError("a download was attempted")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(orthonym.jars, "_download", refuse)
    return attempts


def write_jars(directory: Path, content: bytes = b"x") -> None:
    for spec in SPECS.values():
        (directory / spec.filename).write_bytes(content)


# --- environment ----------------------------------------------------------------------------------


@pytest.fixture
def clean_orthonym_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in [n for n in os.environ if n.startswith("ORTHONYM_")]:
        monkeypatch.delenv(name)
    for name in JAVA_OPTION_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_allowed_orthonym_settings_are_exactly_these_five() -> None:
    # JAR_DIR / NO_DOWNLOAD / CACHE_DIR are read by orthonym.jars, JVM_XMX by orthonym.jvm_bridge
    # (pinned commit 110ed81); SKIP_JAR_PREFETCH is read only by the install-time build hook, so
    # it cannot change a name at run time.
    assert {
        "ORTHONYM_JAR_DIR",
        "ORTHONYM_NO_DOWNLOAD",
        "ORTHONYM_JVM_XMX",
        "ORTHONYM_CACHE_DIR",
        "ORTHONYM_SKIP_JAR_PREFETCH",
    } == set(ALLOWED_ORTHONYM_ENV)


@pytest.mark.parametrize(
    "name",
    [
        "ORTHONYM_ALLOW_REDUCED",  # name without the jars: no round trip at all
        "ORTHONYM_OPSIN_JAR",  # a jar "as given, without the checksum check"
        "ORTHONYM_CENTRES_JAR",
        "ORTHONYM_DISABLE_JPYPE",  # a `java` process per call
        "ORTHONYM_DISABLE_OPSIN_VALIDITY_GATE",
        "ORTHONYM_SELF_CONSISTENCY_GATE",
        "ORTHONYM_ENABLE_TRIVIALITY_CONTROLLER",
        "ORTHONYM_STRICT",
        "ORTHONYM_GC_TUNE",
        "ORTHONYM_LEXICON_CACHE",  # an operator cannot switch the pickle cache back on
        "ORTHONYM_SOMETHING_ADDED_IN_A_LATER_RELEASE",
    ],
)
def test_every_orthonym_setting_but_the_allowed_ones_is_refused(
    clean_orthonym_env: pytest.MonkeyPatch, name: str
) -> None:
    clean_orthonym_env.setenv(name, "1")
    with pytest.raises(RuntimeError) as info:
        check_environment()
    assert name in str(info.value)


def test_the_refusal_names_the_variables_but_not_their_values(
    clean_orthonym_env: pytest.MonkeyPatch,
) -> None:
    clean_orthonym_env.setenv("ORTHONYM_ALLOW_REDUCED", "very-secret-value")
    clean_orthonym_env.setenv("ORTHONYM_STRICT", "1")
    with pytest.raises(RuntimeError) as info:
        check_environment()
    assert "ORTHONYM_ALLOW_REDUCED" in str(info.value)
    assert "ORTHONYM_STRICT" in str(info.value)
    assert "very-secret-value" not in str(info.value)


def test_an_empty_value_is_still_a_refused_setting(clean_orthonym_env: pytest.MonkeyPatch) -> None:
    clean_orthonym_env.setenv("ORTHONYM_ALLOW_REDUCED", "")
    with pytest.raises(RuntimeError, match="ORTHONYM_ALLOW_REDUCED"):
        check_environment()


@pytest.mark.parametrize("name", ["JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"])
def test_the_variables_that_inject_jvm_options_are_refused(
    clean_orthonym_env: pytest.MonkeyPatch, name: str
) -> None:
    # They would change the JVM's flags behind the service's back: memory, agents, error files.
    clean_orthonym_env.setenv(name, "-Xmx1g")
    with pytest.raises(RuntimeError) as info:
        check_environment()
    assert name in str(info.value)
    assert "-Xmx1g" not in str(info.value)


def test_the_java_option_variables_are_exactly_these() -> None:
    assert set(JAVA_OPTION_ENV) == {"JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"}


def test_the_lexicon_pickle_cache_is_switched_off_by_the_service_itself(
    clean_orthonym_env: pytest.MonkeyPatch,
) -> None:
    # Orthonym would otherwise write a pickle under ORTHONYM_CACHE_DIR / XDG_CACHE_HOME / ~/.cache
    # at run time and `pickle.loads` it at the next start. Set before Orthonym is imported.
    check_environment()  # the operator's environment is checked first and may not mention it
    disable_runtime_caches()
    assert os.environ["ORTHONYM_LEXICON_CACHE"] == "off"


def test_start_switches_the_caches_off_before_anything_of_orthonym_is_imported(
    clean_orthonym_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []
    import decimer_chem.engine as engine_module

    real_policy = engine_module.apply_jar_policy
    clean_orthonym_env.setattr(
        engine_module, "disable_runtime_caches", lambda: order.append("caches off")
    )
    clean_orthonym_env.setattr(
        engine_module,
        "apply_jar_policy",
        lambda settings: (order.append("jar policy"), real_policy(settings))[1],
    )
    with pytest.raises(RuntimeError, match="jar is not at"):  # stops at the empty jar directory
        OrthonymEngine(Settings(jars_dir=tmp_path)).start()
    assert order == ["caches off", "jar policy"]


@pytest.mark.parametrize("name", sorted(ALLOWED_ORTHONYM_ENV))
def test_the_allowed_settings_pass(clean_orthonym_env: pytest.MonkeyPatch, name: str) -> None:
    clean_orthonym_env.setenv(name, "x")
    check_environment()


def test_a_clean_environment_passes(clean_orthonym_env: pytest.MonkeyPatch) -> None:
    check_environment()


def test_the_jar_policy_points_orthonym_at_our_directory_and_forbids_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ORTHONYM_JAR_DIR", raising=False)
    monkeypatch.delenv("ORTHONYM_NO_DOWNLOAD", raising=False)
    assert apply_jar_policy(Settings(jars_dir=tmp_path)) == tmp_path
    assert os.environ["ORTHONYM_JAR_DIR"] == str(tmp_path)
    assert os.environ["ORTHONYM_NO_DOWNLOAD"] == "1"


def test_the_download_opt_in_lifts_the_download_ban(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ORTHONYM_NO_DOWNLOAD", "1")
    apply_jar_policy(Settings(jars_dir=tmp_path, allow_jar_download=True))
    assert "ORTHONYM_NO_DOWNLOAD" not in os.environ


def test_without_chem_jars_dir_orthonyms_own_directory_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ORTHONYM_JAR_DIR", str(tmp_path))
    assert apply_jar_policy(Settings()) == tmp_path


# --- jars -----------------------------------------------------------------------------------------


def test_missing_jars_are_a_runtime_error_naming_the_path_and_nothing_is_downloaded(
    tmp_path: Path, no_network: list[str]
) -> None:
    with pytest.raises(RuntimeError) as info:
        check_jars(tmp_path, allow_download=False)
    message = str(info.value)
    assert str(tmp_path / SPECS["opsin"].filename) in message
    assert "CHEM_ALLOW_JAR_DOWNLOAD" in message
    assert no_network == []


def pin_to_x(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make both pinned checksums those of a file containing b"x", so tests need no real jars."""
    digest = hashlib.sha256(b"x").hexdigest()
    patched = {k: replace(v, sha256=digest) for k, v in orthonym.jars.JARS.items()}
    monkeypatch.setattr(orthonym.jars, "JARS", patched)
    return digest


def test_one_missing_jar_is_enough_to_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_to_x(monkeypatch)
    (tmp_path / SPECS["opsin"].filename).write_bytes(b"x")  # present and correct
    with pytest.raises(RuntimeError, match=SPECS["centres"].filename):
        check_jars(tmp_path, allow_download=False)


def test_a_jar_with_the_wrong_checksum_is_refused_with_both_hashes(tmp_path: Path) -> None:
    write_jars(tmp_path)
    with pytest.raises(RuntimeError) as info:
        check_jars(tmp_path, allow_download=False)
    message = str(info.value)
    assert hashlib.sha256(b"x").hexdigest() in message  # what is on disk
    assert SPECS["opsin"].sha256 in message  # what is pinned
    assert str(tmp_path) in message


def test_a_jar_the_service_cannot_read_gets_a_permissions_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_to_x(monkeypatch)
    write_jars(tmp_path)
    unreadable = tmp_path / SPECS["opsin"].filename
    unreadable.chmod(
        0o000
    )  # Orthonym writes its jars with mode 0600: another user cannot read them
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("running as a user that can read anything")
        with pytest.raises(RuntimeError) as info:
            check_jars(tmp_path, allow_download=False)
    finally:
        unreadable.chmod(0o600)
    message = str(info.value)
    assert str(unreadable) in message
    assert "0600" in message
    assert "chmod" in message


def test_the_download_opt_in_lets_missing_jars_through_to_orthonym(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert check_jars(tmp_path, allow_download=True) == {}
    assert any("download" in r.getMessage() for r in caplog.records)


def test_the_download_opt_in_still_refuses_a_jar_that_is_there_but_wrong(tmp_path: Path) -> None:
    write_jars(tmp_path)
    with pytest.raises(RuntimeError, match="SHA-256"):
        check_jars(tmp_path, allow_download=True)


def test_good_jars_are_reported_with_their_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_jars(tmp_path)
    digest = pin_to_x(monkeypatch)
    assert check_jars(tmp_path, allow_download=False) == {"opsin": digest, "centres": digest}


def test_start_fails_on_missing_jars_before_any_engine_is_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in [n for n in os.environ if n.startswith("ORTHONYM_")]:
        monkeypatch.delenv(name)

    def refuse() -> None:
        raise AssertionError("Orthonym was built although the jars are missing")

    monkeypatch.setattr(OrthonymEngine, "_build_namer", lambda self: refuse())
    with pytest.raises(RuntimeError, match=str(tmp_path)):
        OrthonymEngine(Settings(jars_dir=tmp_path)).start()


# --- the valid tier, and no other ---------------------------------------------------------


def test_the_engine_is_built_for_the_valid_tier_and_no_wider_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class Recorder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.append((args, kwargs))

    monkeypatch.setattr(orthonym, "Orthonym", Recorder)
    OrthonymEngine(Settings())._build_namer()
    # `orthonym --emit-tier valid` is general_fallback=True and nothing else (cli._emit_tier_flags).
    # complete adds allow_aromatic_general, best-effort adds general_fallback_unverified and
    # full-coverage adds full_coverage; none of those may be passed.
    assert seen == [((), {"general_fallback": True})]


# --- the soft time limit --------------------------------------------------------------------------


class FakeOrthonym:
    def __init__(self, behaviour: str = "fast") -> None:
        self.behaviour = behaviour
        self.seen: list[str] = []

    def name_tiered(self, smiles: str) -> dict[str, object]:
        self.seen.append(smiles)
        if self.behaviour == "spin":
            give_up = time.monotonic() + 5  # a limit that never fires fails the test, not hangs it
            while time.monotonic() < give_up:
                time.sleep(0.005)
            raise AssertionError("the soft time limit never fired")
        if self.behaviour == "invalid":
            raise ValueError(f"Invalid SMILES: {smiles}")
        if self.behaviour == "bug":
            raise KeyError("boom")
        return {"name": "ethanol", "tier": "pin_verified", "verified": "opsin", "limit_code": None}


def test_a_name_comes_through_the_policy() -> None:
    result = OrthonymEngine(Settings(), namer=FakeOrthonym()).name("CCO", 5.0)
    assert (result.name, result.tier, result.verified) == ("ethanol", "pin_verified", "opsin")


def test_a_wider_tier_name_from_the_namer_is_withheld() -> None:
    class Wide(FakeOrthonym):
        def name_tiered(self, smiles: str) -> dict[str, object]:
            return {"name": "x", "tier": "best_effort", "verified": "opsin"}

    result = OrthonymEngine(Settings(), namer=Wide()).name("CCO", 5.0)
    assert (result.name, result.reason) == (None, "tier_not_accepted")


def test_a_name_without_a_round_trip_from_the_namer_is_withheld() -> None:
    class Carve(FakeOrthonym):
        def name_tiered(self, smiles: str) -> dict[str, object]:
            return {"name": "scyllo-inositol", "tier": "pin_unverified", "verified": "unverified"}

    result = OrthonymEngine(Settings(), namer=Carve()).name("CCO", 5.0)
    assert (result.name, result.reason) == (None, "not_round_tripped")


def test_orthonym_running_past_the_soft_limit_is_a_timeout_result() -> None:
    engine = OrthonymEngine(Settings(), namer=FakeOrthonym("spin"))
    start = time.monotonic()
    result = engine.name("CCO", 0.3)
    assert time.monotonic() - start < 3
    assert (result.name, result.tier, result.verified, result.reason) == (
        None,
        None,
        None,
        TIMEOUT_REASON,
    )
    assert engine.recycle_requested is False  # nothing was left behind


def test_the_engine_is_usable_after_a_timeout() -> None:
    fake = FakeOrthonym("spin")
    engine = OrthonymEngine(Settings(), namer=fake)
    assert engine.name("CCO", 0.2).reason == TIMEOUT_REASON
    fake.behaviour = "fast"
    assert engine.name("CCN", 5.0).name == "ethanol"


def test_a_timeout_that_left_state_behind_asks_for_a_new_process_and_logs_only_the_keys(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import orthonym.wallclock

    monkeypatch.setattr(
        orthonym.wallclock,
        "clear_leaked_naming_state",
        lambda: {"session_depth": 1, "visited": "CC(=O)Oc1ccccc1C(=O)O"},
    )
    engine = OrthonymEngine(Settings(), namer=FakeOrthonym("spin"))
    with caplog.at_level(logging.WARNING, logger="decimer_chem"):
        assert engine.name("CCO", 0.2).reason == TIMEOUT_REASON
    assert engine.recycle_requested is True
    record = next(r for r in caplog.records if "time limit" in r.getMessage())
    assert record.state_not_restored == ["session_depth", "visited"]  # type: ignore[attr-defined]
    assert "CC(=O)O" not in " ".join(str(r.__dict__) for r in caplog.records)


def test_an_unreadable_smiles_is_declined_before_orthonym_is_called() -> None:
    fake = FakeOrthonym()
    result = OrthonymEngine(Settings(), namer=fake).name("C1CC", 5.0)  # an open ring
    assert (result.name, result.tier, result.verified, result.reason) == (
        None,
        None,
        None,
        INVALID_SMILES_REASON,
    )
    assert fake.seen == []


@pytest.mark.parametrize("smiles", ["c1ccccc", "C(", "[Zz]", "C1CC1(C)(C)(C)C", "C=C=C=C=X"])
def test_what_rdkit_cannot_read_is_invalid_smiles(smiles: str) -> None:
    result = OrthonymEngine(Settings(), namer=FakeOrthonym()).name(smiles, 5.0)
    assert result.reason == INVALID_SMILES_REASON


def test_a_value_error_from_orthonym_itself_propagates_for_the_caller_to_classify() -> None:
    with pytest.raises(ValueError, match="Invalid SMILES"):
        OrthonymEngine(Settings(), namer=FakeOrthonym("invalid")).name("CCO", 5.0)


def test_an_unexpected_exception_propagates() -> None:
    with pytest.raises(KeyError):
        OrthonymEngine(Settings(), namer=FakeOrthonym("bug")).name("CCO", 5.0)


def test_the_soft_limit_refuses_to_be_silently_off_the_main_thread() -> None:
    # Orthonym's limit is a SIGALRM, which only works on the main thread; elsewhere it would do
    # nothing, and a pathological molecule would then run without any limit.
    errors: list[BaseException] = []

    def call() -> None:
        try:
            OrthonymEngine(Settings(), namer=FakeOrthonym()).name("CCO", 1.0)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    thread.join()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "main thread" in str(errors[0])


# --- no `java` process of its own ----------------------------------------------------------------


@pytest.fixture
def java_guard() -> Iterator[None]:
    restore = refuse_java_subprocess()
    try:
        yield
    finally:
        restore()


@pytest.mark.usefixtures("java_guard")
@pytest.mark.parametrize(
    "argv",
    [["java", "-jar", "x.jar"], ["/opt/jre/bin/java", "-version"], "java -version"],
    ids=["bare", "absolute", "string"],
)
def test_a_java_subprocess_is_refused_in_the_naming_process(argv: object) -> None:
    # Orthonym falls back to `java -jar ...` per call when its in-process JVM is unavailable. A
    # process started that way is not covered by the parent-death signal, so it is refused.
    # FileNotFoundError is what a missing `java` raises, and what Orthonym's fallbacks catch.
    with pytest.raises(FileNotFoundError, match="java"):
        subprocess.run(argv, check=False, shell=isinstance(argv, str))  # type: ignore[call-overload]


@pytest.mark.usefixtures("java_guard")
def test_other_subprocesses_still_run() -> None:
    out = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, check=True)
    assert out.stdout.strip() == b"1"


def test_the_guard_can_be_lifted() -> None:
    restore = refuse_java_subprocess()
    restore()
    out = subprocess.run([sys.executable, "-c", "print(2)"], capture_output=True, check=True)
    assert out.stdout.strip() == b"2"
