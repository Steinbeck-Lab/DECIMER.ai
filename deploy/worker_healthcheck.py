"""Healthcheck of the arq worker container: is its health-check key in Redis?

arq's worker sets the key `<queue name>:health-check` every `health_check_interval` seconds (60,
decimer_api/worker/arq_app.py) with a lifetime of that plus one second, so the key exists exactly
while the worker's loop is alive and turning. The default queue is `arq:queue`; a worker that serves
another queue (the fast lane) names it in WORKER_QUEUE_NAME, which this script reads.
Exit 0 if the key exists, 1 otherwise (also when Redis does not answer). REDIS_URL and
WORKER_QUEUE_NAME come from the container's environment.
"""

import os
import sys

import redis

KEY = f"{os.environ.get('WORKER_QUEUE_NAME') or 'arq:queue'}:health-check"


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
