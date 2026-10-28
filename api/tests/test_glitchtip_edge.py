"""How error reports get to GlitchTip: the edge's `/glitchtip/` location, the gate
for the api and the workers, and the one setting of the entrypoint that opens the location.

These are the nginx configuration files themselves, read as text: nginx is not in this environment,
and what has to stay true is a short list of lines that a careless edit could delete. (The behaviour
was tried against a real nginx and a real GlitchTip, see deploy/README.md, "Error reports: GlitchTip".) Inside
the monorepo a missing file FAILS (see test_engines.py).

What they hold, in short: the privacy page says an error report never holds an IP address. GlitchTip
reads a client address from `X-Forwarded-For`, `X-Real-IP`, `Client-IP`, `X-Forwarded`,
`X-Cluster-Client-IP`, `Forwarded-For`, `Forwarded` and more, and stores it for 90 days (anonymised
to the /24 at best), so the edge sends it only the headers it needs and none of those; and the
browsers' endpoint is closed in every other way it can be (one project, no compression, a small
body, global and per-client limits).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IN_MONOREPO = (REPO / "api" / "pyproject.toml").is_file() and (REPO / "deploy").is_dir()
pytestmark = pytest.mark.skipif(
    not IN_MONOREPO, reason="the api is not run from inside the monorepo"
)

NGINX = REPO / "deploy" / "nginx"
# Everything GlitchTip may be sent from the edge, and nothing else.
SENT_TO_GLITCHTIP = {"connection", "host", "content-type", "content-length"}
LOCATION = re.compile(
    r'location ~ "\^/glitchtip/api/\(\?<gt_project>\[0-9\]\{1,9\}\)/envelope/\$" \{'
)


def block_at(text: str, brace: int) -> str:
    """The text of the `{ ... }` block whose opening brace is at `brace` (nested blocks inside)."""
    assert text[brace] == "{"
    depth = 0
    for position in range(brace, len(text)):
        depth += {"{": 1, "}": -1}.get(text[position], 0)
        if depth == 0:
            return text[brace + 1 : position]
    raise AssertionError("an unclosed block")


def strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def reports_location() -> str:
    edge = (NGINX / "edge.conf").read_text()
    matches = LOCATION.findall(edge)
    assert len(matches) == 1, "the browsers' reports have one location"
    found = LOCATION.search(edge)
    assert found is not None
    return strip_comments(block_at(edge, found.end() - 1))


def test_glitchtip_is_sent_only_the_headers_it_needs() -> None:
    location = reports_location()
    assert "proxy_pass_request_headers off;" in location
    names = {
        name.lower() for name in re.findall(r"^\s*proxy_set_header\s+(\S+)", location, re.MULTILINE)
    }
    assert names == SENT_TO_GLITCHTIP, names
    for line in re.findall(r"^\s*proxy_set_header\s+.*$", location, re.MULTILINE):
        # what is set must come from the request's own body framing or be fixed text: never an
        # address
        assert "$remote_addr" not in line and "$http_" not in line, line
        assert "$proxy_add_x_forwarded_for" not in line and "$realip_remote_addr" not in line, line


def test_the_endpoint_takes_one_project_one_method_and_no_compression() -> None:
    location = reports_location()
    assert re.search(r"limit_except POST \{\s*deny all;\s*\}", location)
    assert re.search(
        r"if \(\$gt_project != \$glitchtip_web_project\) \{\s*return 404;\s*\}", location
    )
    assert re.search(r'if \(\$http_content_encoding != ""\) \{\s*return 415;\s*\}', location)
    assert "client_max_body_size 64k;" in location
    assert re.search(r"limit_req zone=glitchtip_ingest\b", location)
    assert re.search(r"limit_req zone=glitchtip_all\b", location)
    (target,) = re.findall(r"proxy_pass\s+(\S+);", location)
    assert target == "http://glitchtip:8000/api/$gt_project/envelope/$is_args$args"


def test_one_zone_counts_all_clients_together_with_a_constant_key() -> None:
    edge = strip_comments((NGINX / "edge.conf").read_text())
    assert re.search(
        r'^limit_req_zone "glitchtip" zone=glitchtip_all:\d+m rate=\d+r/m;', edge, re.M
    )
    assert re.search(
        r"^limit_req_zone \$binary_remote_addr zone=glitchtip_ingest:\d+m rate=\d+r/m;", edge, re.M
    )


def test_nothing_else_of_the_edge_reaches_glitchtip() -> None:
    edge = strip_comments((NGINX / "edge.conf").read_text())
    assert re.findall(r"proxy_pass\s+(\S*glitchtip\S*);", edge) == [
        "http://glitchtip:8000/api/$gt_project/envelope/$is_args$args"
    ]
    assert edge.count("/glitchtip/") == 1  # the one location, and no other mention of the path


def test_the_ui_listener_is_a_server_of_its_own_and_has_no_ingest_path() -> None:
    ui = strip_comments((NGINX / "glitchtip-ui.conf").read_text())
    assert re.findall(r"^\s*listen\s+(\S+);", ui, re.MULTILINE) == ["8081"]
    edge = (NGINX / "edge.conf").read_text()
    assert "8081" not in strip_comments(edge)  # the site's server never listens there


def test_the_gate_lets_the_python_sdks_reach_one_endpoint_and_nothing_else() -> None:
    gate = strip_comments((NGINX / "glitchtip-gate.conf").read_text())
    locations = re.findall(r"location\s+(=\s+\S+|~\s+\"[^\"]+\"|/)\s*\{", gate)
    assert locations == ["= /healthz", '~ "^/api/(?<gt_project>[0-9]{1,9})/envelope/$"', "/"], (
        locations
    )
    # the upstream path is rebuilt from the captured id: a raw $request_uri with encoded "../" could
    # match this location yet be routed elsewhere by GlitchTip
    assert "$request_uri" not in gate
    assert "/api/$gt_project/envelope/" in gate
    found = re.search(r'location ~ "\^/api/[^"]+" \{', gate)
    assert found is not None
    envelope = block_at(gate, found.end() - 1)
    assert re.search(r"limit_except POST \{\s*deny all;\s*\}", envelope)
    root = block_at(gate, gate.rindex("location / {") + len("location / "))
    assert root.strip() == "return 404;"
    assert "access_log off;" in gate  # nothing is logged: not a path, not a peer
    assert gate.count("proxy_pass") == 1


def entrypoint_output(tmp_path: Path, project: str | None) -> subprocess.CompletedProcess[str]:
    """The generated include of edge-entrypoint.sh, with its last lines (the error-log housekeeping,
    which needs the image's /usr/local/bin) taken off and `exec` of `true`."""
    text = (NGINX / "edge-entrypoint.sh").read_text()
    text = re.sub(r"^/usr/local/bin/edge-logs\.sh .*$", "", text, flags=re.MULTILINE)
    script = tmp_path / "entrypoint.sh"
    script.write_text(text)
    (tmp_path / "cache").mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "EDGE_GENERATED_CONF": str(tmp_path / "generated.conf"),
        "EDGE_CACHE_DIR": str(tmp_path / "cache"),
    }
    if project is not None:
        env["GLITCHTIP_WEB_PROJECT_ID"] = project
    return subprocess.run(  # noqa: S603  (our own shell script, a fixed command)
        [shutil.which("sh") or "sh", str(script), "true"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("project", "line"),
    [
        ("2", 'set $glitchtip_web_project "2";'),
        ("123456789", 'set $glitchtip_web_project "123456789";'),
        (None, 'set $glitchtip_web_project "";'),
        ("", 'set $glitchtip_web_project "";'),
    ],
)
def test_the_entrypoint_names_the_one_project_or_none(
    tmp_path: Path, project: str | None, line: str
) -> None:
    done = entrypoint_output(tmp_path, project)
    assert done.returncode == 0, done.stderr
    assert line in (tmp_path / "generated.conf").read_text().splitlines()


@pytest.mark.parametrize(
    "project", ["0", "02", "abc", "1 2", "1234567890", "2;set", '2"; x "', "-1"]
)
def test_the_entrypoint_refuses_what_is_not_a_project_id(tmp_path: Path, project: str) -> None:
    done = entrypoint_output(tmp_path, project)
    assert done.returncode == 1 and "not a project id" in done.stderr
    assert "glitchtip_web_project" not in (tmp_path / "generated.conf").read_text()


def test_the_image_checks_its_configuration_with_the_variable_defined() -> None:
    """`nginx -t` at build time needs every variable the generated include defines."""
    dockerfile = (REPO / "deploy" / "web.Dockerfile").read_text()
    assert 'set $glitchtip_web_project ""' in dockerfile
    assert "glitchtip-ui.conf" in dockerfile
