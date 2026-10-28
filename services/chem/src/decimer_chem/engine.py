"""The engine that runs inside the naming process: Orthonym's `valid` tier, labelled, and CDK.

Nothing here is imported by the HTTP process. Orthonym (and RDKit, and the JVM it starts through
JPype) are imported lazily, so importing this module is cheap and the parent never pays for them.
CDK (`cdk.py`, the fallback for SMILES RDKit rejects) runs in the same JVM, which Orthonym starts.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .cdk import CDK_JAR_NAME, CDK_JAR_SHA256, CdkLayout
from .results import (
    INVALID_SMILES_REASON,
    TIMEOUT_REASON,
    CdkResult,
    NameResult,
    result_from_row,
)
from .settings import Settings

log = logging.getLogger("decimer_chem")

#: The only `ORTHONYM_*` variables the service tolerates. Orthonym has about thirty, and several
#: switch checking off or change what is named (`ORTHONYM_ALLOW_REDUCED` names without the jars,
#: `ORTHONYM_OPSIN_JAR` uses a jar without its checksum, `ORTHONYM_DISABLE_OPSIN_VALIDITY_GATE`,
#: `ORTHONYM_DISABLE_JPYPE` runs a `java` process per call, ...). Names checked against the source
#: at the pinned commit: JAR_DIR, NO_DOWNLOAD and CACHE_DIR are read by `orthonym.jars`, JVM_XMX by
#: `orthonym.jvm_bridge`; SKIP_JAR_PREFETCH is read only by the install-time build hook, so it
#: cannot change a name at run time.
ALLOWED_ORTHONYM_ENV = frozenset(
    {
        "ORTHONYM_JAR_DIR",
        "ORTHONYM_NO_DOWNLOAD",
        "ORTHONYM_JVM_XMX",
        "ORTHONYM_CACHE_DIR",
        "ORTHONYM_SKIP_JAR_PREFETCH",
    }
)

#: The constructor flags of Orthonym's `valid` tier, from `cli._emit_tier_flags("valid")` at the
#: pinned commit: `general_fallback=True` and nothing else. `complete` adds
#: `allow_aromatic_general`, `best-effort` adds `general_fallback_unverified` and `full-coverage`
#: adds `full_coverage`; none of those is passed.
VALID_TIER_FLAGS: dict[str, bool] = {"general_fallback": True}

#: Variables that inject JVM options (memory, agents, error files) behind the service's back.
JAVA_OPTION_ENV = ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")

_WARMUP_SMILES = "Cn1cnc2c1c(=O)n(C)c(=O)n2C"  # caffeine: the first name boots the JVM and OPSIN


def disable_runtime_caches() -> None:
    """Switch off Orthonym's lexicon cache, before Orthonym is imported.

    That cache is a pickle that Orthonym writes under `ORTHONYM_CACHE_DIR`, `XDG_CACHE_HOME` or
    `~/.cache/orthonym` at run time and loads with `pickle.loads` at the next start: a code-exec
    path through a writable directory. Without it the morpheme table is rebuilt at every start of
    the naming process (about half a second). Set after `check_environment`, so an operator cannot
    switch it back on (`ORTHONYM_LEXICON_CACHE` is not on the allow-list).
    """
    os.environ["ORTHONYM_LEXICON_CACHE"] = "off"


def check_environment(environ: Mapping[str, str] | None = None) -> None:
    """Refuse to start with an `ORTHONYM_*` variable that is not on the allow-list, or with any
    variable that injects JVM options."""
    environ = os.environ if environ is None else environ
    java = [name for name in JAVA_OPTION_ENV if name in environ]
    if java:
        raise RuntimeError(
            f"{', '.join(java)} must not be set: it would change the JVM's options behind the "
            "service's back (memory, agents, where crash reports go)"
        )
    refused = sorted(
        name
        for name in environ
        if name.startswith("ORTHONYM_") and name not in ALLOWED_ORTHONYM_ENV
    )
    if refused:
        raise RuntimeError(
            f"{', '.join(refused)} must not be set: Orthonym settings other than "
            f"{', '.join(sorted(ALLOWED_ORTHONYM_ENV))} can make a name weaker than the service "
            "promises (see ALLOWED_ORTHONYM_ENV in decimer_chem.engine)"
        )


def apply_jar_policy(settings: Settings) -> Path:
    """Point Orthonym at our jar directory and forbid its own download unless opted in.

    Orthonym downloads a missing jar by default ("first start"); the service must not, so the ban
    is switched on in this (naming) process's environment before Orthonym resolves any jar.
    """
    if settings.jars_dir is not None:
        os.environ["ORTHONYM_JAR_DIR"] = str(settings.jars_dir)
    if settings.allow_jar_download:
        os.environ.pop("ORTHONYM_NO_DOWNLOAD", None)
    else:
        os.environ["ORTHONYM_NO_DOWNLOAD"] = "1"
    from orthonym.jars import jar_dir

    return Path(jar_dir())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_jars(directory: Path, *, allow_download: bool) -> dict[str, str]:
    """Check that both pinned jars are in `directory` with Orthonym's pinned SHA-256.

    Returns kind -> SHA-256 of what is there. A missing jar is a RuntimeError naming the path,
    unless `allow_download` (development), in which case Orthonym fetches it on first use. A jar
    that is there with another checksum is always an error, and so is one the service cannot read.
    """
    import orthonym.jars

    found: dict[str, str] = {}
    for kind, spec in orthonym.jars.JARS.items():
        path = directory / spec.filename
        try:
            if not path.is_file():
                if allow_download:
                    log.warning(
                        "jar not found, letting Orthonym download it",
                        extra={"jar": str(path), "url": spec.url},
                    )
                    continue
                raise RuntimeError(
                    f"the {kind} {spec.version} jar is not at {path}. Put {spec.filename} in "
                    f"{directory} (CHEM_JARS_DIR; the image bakes it in), or set "
                    "CHEM_ALLOW_JAR_DOWNLOAD=1 to let Orthonym download it (development only; "
                    "the service never downloads at runtime otherwise)."
                )
            actual = _sha256(path)
        except PermissionError as exc:
            raise RuntimeError(
                f"cannot read {path}: {exc.strerror}. Orthonym writes its jars with mode 0600, so "
                f"the user that runs the service must own them: chmod 0644 {path}, or chown it to "
                "that user, when copying the jars into the image."
            ) from exc
        if actual != spec.sha256:
            raise RuntimeError(
                f"{path} has SHA-256 {actual}, but Orthonym pins the {kind} {spec.version} jar to "
                f"{spec.sha256}. Replace it with the official release file."
            )
        found[kind] = actual
    return found


def check_cdk_jar(directory: Path) -> str:
    """Check that the CDK 2.8 jar is in `directory` with the pinned SHA-256; return its SHA-256.

    Same rules as `check_jars`, with one difference: the service never downloads this jar, whatever
    `CHEM_ALLOW_JAR_DOWNLOAD` says (`scripts/fetch_jars.py` and `deploy/weights/fetch.sh` put it
    on disk). A missing jar, a jar the service cannot read and a jar with another hash are each a
    RuntimeError that names the path.
    """
    path = directory / CDK_JAR_NAME
    try:
        if not path.is_file():
            raise RuntimeError(
                f"the CDK jar is not at {path}. Put {CDK_JAR_NAME} in {directory} (CHEM_JARS_DIR; "
                "the image bakes it in; scripts/fetch_jars.py fetches it for development). The "
                "service never downloads it."
            )
        actual = _sha256(path)
    except PermissionError as exc:
        raise RuntimeError(
            f"cannot read {path}: {exc.strerror}. The user that runs the service must be able to "
            f"read it: chmod 0644 {path}, or chown it to that user."
        ) from exc
    if actual != CDK_JAR_SHA256:
        raise RuntimeError(
            f"{path} has SHA-256 {actual}, but the service pins the CDK jar to {CDK_JAR_SHA256}. "
            "Replace it with cdk-2.8.jar from the official release "
            "(https://github.com/cdk/cdk/releases/tag/cdk-2.8)."
        )
    return actual


def _runs_java(args: object) -> bool:
    if isinstance(args, str):
        try:
            first = shlex.split(args)[0]
        except (ValueError, IndexError):
            return False
    elif isinstance(args, (list, tuple)) and args:
        first = os.fspath(args[0])
        if isinstance(first, bytes):
            first = os.fsdecode(first)
    else:
        return False
    return os.path.basename(first) == "java"


def refuse_java_subprocess() -> Callable[[], None]:
    """Make `subprocess` refuse to start `java` in this process; return the function that undoes it.

    Orthonym falls back to `java -jar ...` once per call when its in-process JVM is unavailable. A
    process started that way is not covered by the parent-death signal (the kernel clears it for
    the children of a child), so it could outlive the service. The service runs one JVM, in this
    process, and anything else fails closed. The refusal is a `FileNotFoundError`, which is what a
    missing `java` raises and what Orthonym's fallbacks are written to catch. Idempotent.
    """
    original = subprocess.Popen.__init__
    if hasattr(original, "_chem_original"):
        return lambda: None

    def guarded(self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any) -> None:
        if _runs_java(args):
            raise FileNotFoundError(
                errno.ENOENT,
                "starting a `java` process is refused in the naming process: the service runs one "
                "in-process JVM (check JAVA_HOME), and a separate one would not die with it",
                "java",
            )
        original(self, args, *rest, **kwargs)

    guarded._chem_original = original  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = guarded  # type: ignore[method-assign]

    def restore() -> None:
        subprocess.Popen.__init__ = original  # type: ignore[method-assign]

    return restore


class OrthonymEngine:
    """Orthonym at its `valid` tier, one structure at a time, on the calling (main) thread."""

    def __init__(
        self, settings: Settings, namer: Any | None = None, cdk: CdkLayout | None = None
    ) -> None:
        self._settings = settings
        self._namer: Any = namer
        self._cdk = cdk
        #: Set when a timeout left Orthonym's process-wide state dirty: the supervisor then
        #: replaces this process after delivering the answer.
        self.recycle_requested = False

    @staticmethod
    def _build_namer() -> Any:
        import orthonym

        return orthonym.Orthonym(**VALID_TIER_FLAGS)

    def start(self) -> dict[str, object]:
        began = time.monotonic()
        check_environment()
        disable_runtime_caches()  # before the first import of Orthonym, which may read the cache
        refuse_java_subprocess()
        directory = apply_jar_policy(self._settings)
        hashes = check_jars(directory, allow_download=self._settings.allow_jar_download)
        hashes["cdk"] = check_cdk_jar(directory)  # before the JVM starts, like the other two
        from rdkit import rdBase

        # Orthonym probes many candidate names; RDKit writes a "Can't kekulize" line to stderr for
        # each probe that fails. They are not the service's errors and would fill the JSON log.
        rdBase.DisableLog("rdApp.*")
        self._namer = self._build_namer()
        # The first name starts the JVM (it is lazy) and builds OPSIN's automaton: do it now, so
        # no request pays for it.
        self._namer.name_tiered(_WARMUP_SMILES)
        import jpype

        if not jpype.isJVMStarted():
            raise RuntimeError(
                "Orthonym did not start an in-process JVM, so it would run a `java` process per "
                "call (or none at all). Check that a Java 11+ runtime is installed and JAVA_HOME "
                "points at it."
            )
        # CDK is opened in a class loader of its own once Orthonym's JVM is up (see cdk.py), and
        # warmed up here, so no request pays for loading its classes and layout templates.
        if self._cdk is None:
            self._cdk = CdkLayout(directory / CDK_JAR_NAME)
        cdk_info = self._cdk.start()
        warmed = self._cdk.molfile(_WARMUP_SMILES, self._settings.startup_timeout_s)
        if warmed.molfile is None:
            raise RuntimeError(f"CDK could not lay out caffeine at start (reason: {warmed.reason})")
        import orthonym

        return {
            "orthonym": orthonym.__version__,
            "tier": "valid",
            **cdk_info,
            "java": str(jpype.java.lang.System.getProperty("java.version")),
            "jars": hashes,
            "jar_dir": str(directory),
            "start_seconds": round(time.monotonic() - began, 2),
        }

    def name(self, smiles: str, timeout_s: float) -> NameResult:
        from orthonym.wallclock import (
            WallClockTimeout,
            can_limit,
            clear_leaked_naming_state,
            wall_clock_limit,
        )
        from rdkit import Chem

        if self._namer is None:
            raise RuntimeError("the engine was not started")
        if not can_limit():
            # Off the main thread Orthonym's limit would silently do nothing.
            raise RuntimeError(
                "the soft time limit works on the main thread of a POSIX process only"
            )
        try:
            with wall_clock_limit(timeout_s):
                if Chem.MolFromSmiles(smiles) is None:
                    # Orthonym would raise ValueError for this too, but a ValueError raised
                    # inside it could also mean something else; RDKit's answer is checked first.
                    return NameResult(None, None, None, INVALID_SMILES_REASON)
                row = self._namer.name_tiered(smiles)
        except WallClockTimeout:
            leaked = clear_leaked_naming_state()
            if leaked:
                self.recycle_requested = True
            log.warning(
                "orthonym hit the time limit",
                extra={"limit_s": round(timeout_s, 2), "state_not_restored": sorted(leaked)},
            )
            return NameResult(None, None, None, TIMEOUT_REASON)
        except Exception:
            clear_leaked_naming_state()
            raise
        return result_from_row(row)

    def cdk(self, smiles: str, timeout_s: float) -> CdkResult:
        """The V2000 molblock CDK lays out for `smiles`, or a decline; see `CdkLayout.molfile`."""
        if self._cdk is None:
            raise RuntimeError("the engine was not started")
        try:
            return self._cdk.molfile(smiles, timeout_s)
        finally:
            if self._cdk.needs_new_process:
                self.recycle_requested = True  # a Java thread is still running: replace us
