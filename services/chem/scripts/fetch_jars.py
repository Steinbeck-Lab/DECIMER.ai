"""Development helper: put the jars on disk once, each checked against its pinned SHA-256.

The service never downloads at runtime; this is the explicit step that puts the jars on disk (the
Docker image does the same once, at build time, and bakes them in; `deploy/weights/fetch.sh chem`
fetches the same three files into the build context).

* OPSIN and centres: through Orthonym's own downloader, with Orthonym's pinned checksums.
* CDK 2.8 (`cdk-2.8.jar`, the D4 fallback): from the cdk-2.8 release of github.com/cdk/cdk, https
  only (also across redirects), refused unless its SHA-256 is the one the service pins.

    CHEM_JARS_DIR=/some/dir uv run python scripts/fetch_jars.py   # default: Orthonym's cache dir
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

CDK_URL = "https://github.com/cdk/cdk/releases/download/cdk-2.8/cdk-2.8.jar"


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: str
    ) -> Any:
        if not newurl.startswith("https://"):
            raise OSError(f"refusing a redirect to {newurl.split('?')[0]}: https only")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_cdk(directory: Path) -> Path:
    from decimer_chem.cdk import CDK_JAR_NAME, CDK_JAR_SHA256

    target = directory / CDK_JAR_NAME
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == CDK_JAR_SHA256:
        return target
    directory.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_HttpsOnly)
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".cdk-", delete=False) as out:
        temporary = Path(out.name)
        try:
            with opener.open(CDK_URL, timeout=300) as response:  # noqa: S310  # https, see above
                digest = hashlib.sha256()
                for block in iter(lambda: response.read(1 << 20), b""):
                    digest.update(block)
                    out.write(block)
            if digest.hexdigest() != CDK_JAR_SHA256:
                raise SystemExit(
                    f"{CDK_URL} has SHA-256 {digest.hexdigest()}, not the pinned {CDK_JAR_SHA256}"
                )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.chmod(0o644)  # the service user must be able to read it
    temporary.replace(target)
    return target


def main() -> None:
    if os.environ.get("CHEM_JARS_DIR"):
        os.environ["ORTHONYM_JAR_DIR"] = os.environ["CHEM_JARS_DIR"]
    from orthonym.jars import fetch_all, jar_dir

    jars = {kind: Path(path) for kind, path in fetch_all(verbose=True).items()}
    jars["cdk"] = fetch_cdk(Path(jar_dir()))
    for kind, path in jars.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{kind}: {path}  sha256={digest}")


if __name__ == "__main__":
    main()
