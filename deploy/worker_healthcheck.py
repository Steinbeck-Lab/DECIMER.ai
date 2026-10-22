"""Healthcheck of the arq worker container: is its health-check key in Redis?

arq's worker sets the key `arq:queue:health-check` (queue name + ":health-check") every
`health_check_interval` seconds (60, decimer_api/worker/arq_app.py) with a lifetime of that plus one
second, so the key exists exactly while the worker's loop is alive and turning. Exit 0 if it exists,
1 otherwise (also when Redis does not answer). REDIS_URL comes from the container's environment.
"""

import os
import sys

import redis

KEY = "arq:queue:health-check"


def main() -> int:
    try:
        client = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=5)
        if client.exists(KEY):
            return 0
        print(f"worker healthcheck: no {KEY} in Redis", file=sys.stderr)
    except Exception as error:  # a missing variable, a refused connection, a timeout
        print(f"worker healthcheck: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
