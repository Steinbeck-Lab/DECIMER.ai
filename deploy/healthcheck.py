"""Container healthcheck of the Python images: `python healthcheck.py <port> <path> [timeout]`.

Asks http://127.0.0.1:$PORT<path> and exits 0 on a 2xx answer, 1 on anything else (an error
status, no answer within the timeout, a refused connection). $PORT, when set, wins over the port
argument, so changing a service's port needs no change here. Standard library only: there
is no curl in the images.
"""

import os
import sys
import urllib.request


def main() -> int:
    port = os.environ.get("PORT") or sys.argv[1]
    path = sys.argv[2]
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout):  # noqa: S310
            return 0
    except Exception as error:  # HTTPError (non-2xx), URLError, timeout: all mean "not healthy"
        print(f"healthcheck {path}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
