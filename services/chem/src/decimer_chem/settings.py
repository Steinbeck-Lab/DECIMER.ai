"""Settings read from the environment (documented in the README)."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _positive_seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, got {raw!r}")
    return value


@dataclass(frozen=True)
class Settings:
    #: Where the OPSIN and centres jars are. None: Orthonym's own jar directory (`ORTHONYM_JAR_DIR`,
    #: else the user cache). The image sets this to the directory it baked the jars into.
    jars_dir: Path | None = None
    #: Development only: let Orthonym download a missing jar. A service without network cannot.
    allow_jar_download: bool = False
    #: Soft limit for one structure (Orthonym's own wall-clock limit inside the naming process).
    name_timeout_s: float = 10.0
    #: Budget for one request. Structures after it is spent are declined with reason "timeout".
    batch_timeout_s: float = 60.0
    #: How long after the soft limit the naming process may keep running before it is killed.
    kill_grace_s: float = 5.0
    #: How long a naming process may take to become ready (JVM start, jar check).
    startup_timeout_s: float = 120.0
    #: After a naming process failed to start, wait this long before trying again.
    respawn_backoff_s: float = 5.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            # Absolute: the naming process runs in a scratch directory.
            jars_dir=Path(env["CHEM_JARS_DIR"]).absolute() if env.get("CHEM_JARS_DIR") else None,
            allow_jar_download=env.get("CHEM_ALLOW_JAR_DOWNLOAD") == "1",
            name_timeout_s=_positive_seconds(env, "CHEM_NAME_TIMEOUT_S", cls.name_timeout_s),
            batch_timeout_s=_positive_seconds(env, "CHEM_BATCH_TIMEOUT_S", cls.batch_timeout_s),
        )
