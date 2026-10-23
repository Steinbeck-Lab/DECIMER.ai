"""The privacy page's promises about retention and logs, held against the code that keeps them.

The page (web/src/strings.ts) says: a report is deleted at the latest after 12 months; the edge's
access log has no address, user agent, referrer or query string; its error log, the one place an
address is written, is kept seven days at most. Each is a number or a setting somewhere else; if one
of them changes without the page, or the page without it, one of these tests says so. Inside the
monorepo a missing file FAILS (see test_engines.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from decimer_api.settings import Settings

REPO = Path(__file__).resolve().parents[2]
IN_MONOREPO = (REPO / "api" / "pyproject.toml").is_file() and (REPO / "deploy").is_dir()
pytestmark = pytest.mark.skipif(
    not IN_MONOREPO, reason="the api is not run from inside the monorepo"
)

NGINX = REPO / "deploy" / "nginx"
PAGE = (REPO / "web" / "src" / "strings.ts") if IN_MONOREPO else Path()


def page_text() -> str:
    return PAGE.read_text()


def env_values(path: Path, variable: str) -> list[str]:
    """The values `variable` gets on the active (not commented) `NAME=value` lines of a file."""
    pattern = rf"^\s*{variable}\s*=\s*(\S*)"
    return re.findall(pattern, path.read_text(), re.MULTILINE)


def test_reports_are_deleted_after_the_twelve_months_the_page_promises() -> None:
    from decimer_api.reports import TIDY_INTERVAL_SECONDS

    day = 86400
    assert Settings(redis_url="redis://x:6379/0").reports_max_age_days == 364
    assert "at the latest after 12 months" in page_text()
    # The shortest twelve months are 365 days: the limit plus the wait for the next run stays within
    # them, wherever the limit comes from. The api reads REPORTS_MAX_AGE_DAYS from deploy/.env
    # (env_file), of which only deploy/.env.example is in the repository, and from compose's own
    # environment.
    for example in (REPO / "deploy" / ".env.example", REPO / ".env.example"):
        for value in env_values(example, "REPORTS_MAX_AGE_DAYS"):
            assert int(value) * day + TIDY_INTERVAL_SECONDS <= 365 * day, (example.name, value)
    compose = (REPO / "deploy" / "compose.yaml").read_text()
    assert not re.search(r"^\s+REPORTS_MAX_AGE_DAYS:", compose, re.MULTILINE)


def test_the_error_log_is_kept_the_seven_days_the_page_promises() -> None:
    script = (NGINX / "edge-logs.sh").read_text()
    assert "keep=${EDGE_LOG_KEEP_DAYS:-7}" in script
    assert "no line in it is kept longer than seven days" in page_text()


def test_nothing_sets_the_error_log_retention_for_the_edge_beyond_what_the_page_says() -> None:
    """The edge takes its environment from compose's `environment:` alone (no env_file, so nothing
    in deploy/.env reaches it); and no file that builds or starts it sets a longer retention, or a
    longer interval between the runs that enforce it."""
    compose = (REPO / "deploy" / "compose.yaml").read_text()
    edge_service = compose.split("\n  edge:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert "env_file" not in edge_service
    sources = [
        compose,
        (REPO / "deploy" / ".env.example").read_text(),
        (REPO / "deploy" / "web.Dockerfile").read_text(),
        (NGINX / "edge-entrypoint.sh").read_text(),
    ]
    text = "\n".join(
        line
        for source in sources
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    for value in re.findall(r"EDGE_LOG_KEEP_DAYS\W+(\d+)", text):
        assert int(value) <= 7
    for value in re.findall(r"EDGE_LOG_INTERVAL_SECONDS\W+(\d+)", text):
        assert int(value) <= 600
    assert not re.findall(
        r"EDGE_LOG_(?:NOW|GRACE_SECONDS|DIR|PID_FILE)", text
    )  # test-only or fixed


def test_nginx_writes_the_error_log_to_the_file_that_is_rotated_and_to_nowhere_else() -> None:
    main = (NGINX / "nginx.conf").read_text()
    edge = (NGINX / "edge.conf").read_text()
    directives = re.findall(r"^\s*error_log\s+(\S+)\s+(\S+);", main + edge, re.MULTILINE)
    # One error_log, a file in the directory edge-logs.sh works on, and no copy to stderr or syslog:
    # docker's log driver keeps stderr by size, not by age.
    assert directives == [("/var/log/nginx/error.log", "error")]
    script = (NGINX / "edge-logs.sh").read_text()
    assert "dir=${EDGE_LOG_DIR:-/var/log/nginx}" in script
    assert "name=error.log" in script
    assert "pidfile=${EDGE_LOG_PID_FILE:-/tmp/nginx.pid}" in script
    assert "pid /tmp/nginx.pid;" in main


def test_the_error_log_directory_is_a_volume_of_the_edge_and_the_image_has_the_script() -> None:
    compose = (REPO / "deploy" / "compose.yaml").read_text()
    edge_service = compose.split("\n  edge:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert "- edge-logs:/var/log/nginx" in edge_service
    assert re.search(r"^volumes:\n(?:.*\n)*?  edge-logs: \{\}", compose, re.MULTILINE)
    dockerfile = (REPO / "deploy" / "web.Dockerfile").read_text()
    assert "edge-logs.sh /usr/local/bin/edge-logs.sh" in dockerfile
    # The HEALTHCHECK enforces the retention itself (rotate, then check), so that it does not rest
    # on the loop beside nginx being alive.
    healthcheck = dockerfile.split("HEALTHCHECK", 1)[1].split("ENTRYPOINT", 1)[0]
    assert "/usr/local/bin/edge-logs.sh enforce" in healthcheck
    entrypoint = (NGINX / "edge-entrypoint.sh").read_text()
    # Before nginx opens the file: what is past its week goes first, and an error log that is not a
    # plain file stops the start.
    start = [
        entrypoint.index(f"/usr/local/bin/edge-logs.sh {command}")
        for command in ("rotate", "check")
    ]
    assert start == sorted(start)
    assert "/usr/local/bin/edge-logs.sh loop &\n" in entrypoint
    assert start[1] < entrypoint.index("edge-logs.sh loop") < entrypoint.index('exec "$@"')


def test_every_file_the_edge_image_copies_from_nginx_is_in_its_build_context() -> None:
    """The build context is `*` minus what web.Dockerfile.dockerignore lets back in: a COPY of a
    file it does not list fails the build ("not found")."""
    dockerfile = (REPO / "deploy" / "web.Dockerfile").read_text()
    ignore = (REPO / "deploy" / "web.Dockerfile.dockerignore").read_text().splitlines()
    copied = re.findall(r"^COPY (?:--\S+ )*(deploy/\S+) ", dockerfile, re.MULTILINE)
    assert "deploy/nginx/edge-logs.sh" in copied
    for path in copied:
        assert f"!{path}" in ignore, path


def test_the_access_log_has_no_address_user_agent_referrer_or_query_string() -> None:
    edge = (NGINX / "edge.conf").read_text()
    (log_format,) = re.findall(r"^log_format edge (.+);", edge, re.MULTILINE)
    variables = set(re.findall(r"\$\w+", log_format))
    assert variables == {
        "$time_iso8601",
        "$request_method",
        "$log_uri",  # the path, job ids replaced (the map above it), no query string
        "$status",
        "$request_time",
        "$body_bytes_sent",
    }
    access_logs = re.findall(r"^\s*access_log\s+(.+);", edge, re.MULTILINE)
    assert sorted(access_logs) == ["/dev/stdout edge", "off", "off"]
