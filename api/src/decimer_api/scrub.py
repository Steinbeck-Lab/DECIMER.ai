"""What may leave the process in an error report or a log line.

Reports go to a GlitchTip on our own VM, but they are kept for 90 days, so they follow the same rule
as the logs: **no structure, no file, no address** (the privacy text promises that nothing a user
uploads or draws outlives the hour). Two layers:

* `scrub_event` is the `before_send` of the Sentry SDK. It is an ALLOWLIST: it builds a new event
  from the few fields that are needed to read a crash (the exception types, the stack as function
  names and line numbers, the SDK and runtime versions) and drops everything else, so a field a
  newer SDK adds cannot leak by default. Dropped: the request body, query string, cookies, every
  header but `Content-Type` and `Content-Length`, the user and its IP address, `extra`, the
  breadcrumbs, the local variables of every frame, the source lines and `server_name`.
* `scrub_text` cleans the free text that is kept (messages, exception values, tags, the logger's
  messages): a molfile (the whole text goes), file names, job ids, IP addresses and SMILES strings
  are replaced by a placeholder. It is a best-effort pattern match, because a message can carry
  anything; the allowlist above is what keeps the rest out, and the code does not put structures
  into messages.

Standard library only (the ingest children and the sidecar import nothing of this and do not
report).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator, Mapping
from typing import Any

MOLFILE_REMOVED = "[molfile removed]"
FILE = "[file]"
IP = "[ip]"
SMILES = "[smiles]"
JOB_ID = ":id"
SCRUB_FAILED = "[scrub failed]"
# A message longer than this is cut before the patterns run: an error text can hold a whole upload.
MAX_TEXT_CHARS = 20_000
TRUNCATED = "...[truncated]"

# --- patterns ------------------------------------------------------------------------------------

_MOLFILE = re.compile(r"M  END|V[23]000|\$\$\$\$")
_FILE_EXTENSIONS = (
    r"(?:pdf|png|jpe?g|jfif|webp|heic|heif|avif|gif|tiff?|bmp|svg|eps|mol2?|sdf|smi|smiles|cdx(?:ml)?"
    r"|ket|cml|rxn|rdf|zip|txt|csv|docx?|pptx?|xlsx?|odt|rtf)"
)
# A quoted name may hold spaces (`'Zou et al 2024 final.pdf'`): `uploads.label` shows names this
# way. Quotes of every kind a message may use: " ' ` and the typographic pairs.
_QUOTES = (
    ('"', '"'),
    ("'", "'"),
    ("`", "`"),
    ("\u00ab", "\u00bb"),
    ("\u201c", "\u201d"),
    ("\u2018", "\u2019"),
)
_QUOTED_FILES = tuple(
    re.compile(
        rf"{re.escape(opening)}[^{re.escape(closing)}\n]{{1,200}}?\.{_FILE_EXTENSIONS}"
        rf"{re.escape(closing)}",
        re.IGNORECASE,
    )
    for opening, closing in _QUOTES
)
# What a token may not contain: white space, quotes of every kind, brackets that are not SMILES,
# separators, and the punctuation that ends a sentence or joins a query (`!`, `?`, `&`).
_DELIMITERS = "\\s\"'`<>,;|{}!?&\u00ab\u00bb\u201c\u201d\u2018\u2019"
# A bare name, with whatever directory is in front of it. Not followed by a letter, or by a dot and
# more: `x.pdf.` ends a sentence, `x.pdf.zip` is another file.
_FILE_TOKEN = re.compile(
    rf"[^{_DELIMITERS}]{{0,300}}[^{_DELIMITERS}/\\]\.{_FILE_EXTENSIONS}(?!\w)(?!\.\S)",
    re.IGNORECASE,
)
# What follows the path of a URL in a message (`?smiles=CCO`, `#frag`) goes; the path stays.
_URL_QUERY = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s\"'<>?#]*)[?#][^\s\"'<>]*", re.IGNORECASE)
# ... also when there is no scheme (`/api/x?smiles=CCO`, `host:8000/x?a=b`): a token with a `/` and
# then a `?` (`_cut_query`). A SMILES has `/` but never `?`.
_WORD = re.compile(r"[^\s\"'<>]+")
_HEX_DIGIT = re.compile(r"[0-9A-Fa-f]")
_JOB_ID = re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Candidates only: `ipaddress` decides (a time like 12:30:45 or `std::vector` is not an address).
_IPV6 = re.compile(
    r"(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z.])(?!:[0-9A-Za-z])"
)

# SMILES. A token is a SMILES when all of it is made of SMILES pieces. Bracket atoms follow the real
# grammar (isotope, symbol, chirality, H count, charge, class), so `[ip]` and `[file]`, our own
# placeholders, are not taken for atoms. Possessive quantifiers: no backtracking, linear time.
_BRACKET_ATOM = (
    r"\[\d*(?:[A-Z][a-z]?|se|as|te|[bcnops]|\*)(?:@@?)?(?:H\d*)?(?:[+-]{1,2}\d*)?(?::\d+)?\]"
)
_ORGANIC_ATOM = r"Br|Cl|[BCNOPSFI]|[bcnops]"
_SMILES_PIECE = rf"(?:{_BRACKET_ATOM}|{_ORGANIC_ATOM}|[()=#\-+\\/:~@.*$]|%\d\d|\d)"
_SMILES_FULL = re.compile(rf"{_SMILES_PIECE}++")
_ATOM = re.compile(rf"{_BRACKET_ATOM}|{_ORGANIC_ATOM}")
# A SMILES starts with an atom: never with a digit or a bond (so not `10s`, `25C`).
_MARKER = re.compile(r"[\d\[\]()=#@/\\.%*$+:~-]")
_UPPER_ATOMS = re.compile(r"(?:Br|Cl|[BCNOPSFI])++")
_TOKEN = re.compile(rf"[^{_DELIMITERS}]+")
_KEY_VALUE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*[=:](.+)", re.DOTALL)
_OPENING = "([{"
_CLOSING = ")]}.,:;"
MAX_NESTING = 3
MAX_PASSES = 4
MIN_SMILES_CHARS = 3
# A SMILES that starts with a lower-case (aromatic) atom is a ring: `n=1` is a counter, not a
# molecule.
MIN_LOWER_START_CHARS = 5
# Upper-case atom letters alone (CCCCO) with no marker: common words such as INFO or SPIN are made
# of the same letters, so these need a carbon, or to be long.
MIN_UPPER_SMILES_CHARS = 5


def _looks_like_smiles(token: str) -> bool:
    if len(token) < MIN_SMILES_CHARS or _SMILES_FULL.fullmatch(token) is None:
        return False
    if _ATOM.match(token) is None:
        return False  # a date, a version, a number, `10s`: no SMILES starts like that
    if token[0].islower() and len(token) < MIN_LOWER_START_CHARS:
        return False
    if _UPPER_ATOMS.fullmatch(token):
        return "C" in token or len(token) >= MIN_UPPER_SMILES_CHARS
    # A plain lower-case word made of atom letters is a word: it needs a digit, a bond, a bracket.
    return _MARKER.search(token) is not None


def _unwrap(token: str) -> Iterator[tuple[str, str, str]]:
    """`((CCO)).` as the brackets in front, the middle, and the brackets and punctuation behind,
    with as few of them taken off as will do: every way to take off up to MAX_NESTING in front and
    MAX_NESTING + 1 behind. (A bracket atom such as `[Cl-]` ends in a closing bracket: only the
    candidate that leaves it be is a SMILES.)"""
    front = len(token) - len(token.lstrip(_OPENING))
    back = len(token) - len(token.rstrip(_CLOSING))
    options = [
        (head, tail)
        for head in range(min(front, MAX_NESTING) + 1)
        for tail in range(min(back, MAX_NESTING + 1) + 1)
        if head or tail
    ]
    for head, tail in sorted(options, key=lambda option: sum(option)):
        if head + tail < len(token):
            yield token[:head], token[head : len(token) - tail], token[len(token) - tail :]


def _scrub_token(token: str, depth: int = 0) -> str:
    """The token with a SMILES in it replaced: all of it, or what is left of it inside brackets
    (`(CCO).`, `[SMILES:CCO]`), or its value after a name (`id=CCO`, `mol:{smiles=CCO}`)."""
    if _looks_like_smiles(token):
        return SMILES
    if depth >= MAX_NESTING:
        return token
    for lead, core, trail in _unwrap(token):
        inner = _scrub_token(core, depth + 1)
        if inner != core:
            return lead + inner + trail
    pair = _KEY_VALUE.fullmatch(token)
    if pair is not None:
        inner = _scrub_token(pair.group(1), depth + 1)
        if inner != pair.group(1):
            return token[: pair.start(1)] + inner
    if "/" in token:  # a path or a URL with a structure in one of its parts (`/names/CCO`)
        parts = [_scrub_token(part, depth + 1) for part in token.split("/")]
        if parts != token.split("/"):
            return "/".join(parts)
    return token


def _cut_query(match: re.Match[str]) -> str:
    word = match.group(0)
    mark = word.find("?")
    return word[:mark] if 0 < mark and "/" in word[:mark] else word


def _smiles_in_token(match: re.Match[str]) -> str:
    return _scrub_token(match.group(0))


def _ipv6(candidate: str, *, public_only: bool) -> str:
    """`candidate` with a real IPv6 address in it replaced (a sentence colon after it stays)."""
    if _HEX_DIGIT.search(candidate) is None:
        return candidate  # "::" on its own is punctuation here, not an address
    tail = ":" if candidate.endswith(":") and not candidate.endswith("::") else ""
    try:
        address = ipaddress.IPv6Address(candidate[: len(candidate) - len(tail)])
    except ValueError:
        return candidate
    return candidate if public_only and not address.is_global else IP + tail


def _replace_ipv6(match: re.Match[str]) -> str:
    return _ipv6(match.group(0), public_only=False)


def _replace_public_ipv4(match: re.Match[str]) -> str:
    try:
        return IP if ipaddress.IPv4Address(match.group(0)).is_global else match.group(0)
    except ValueError:
        return match.group(0)


def _replace_public_ipv6(match: re.Match[str]) -> str:
    return _ipv6(match.group(0), public_only=True)


def scrub_text(text: str, *, job_ids: bool = True, public_addresses_only: bool = False) -> str:
    """`text` without molfiles, file names, job ids, IP addresses and SMILES strings.

    `job_ids=False` keeps job ids (the container logs name them to find a job; a report stored for
    90 days does not). `public_addresses_only=True` keeps private, loopback and link-local addresses
    (docker's own: `replica 172.29.0.5 joined`) and removes every other address: for the logs, where
    the operator needs the infrastructure addresses and no client address is ever logged. Never
    raises.
    """
    try:
        return _scrub_text(text, job_ids, public_addresses_only)
    except Exception:  # a pattern bug must not take the report or the log line down with it
        return SCRUB_FAILED


def _scrub_text(text: str, job_ids: bool, public_addresses_only: bool) -> str:
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + " " + TRUNCATED  # a space: a cut SMILES stays its own token
    if _MOLFILE.search(text):
        return MOLFILE_REMOVED
    # Until nothing changes: a token with several things in it (`id=CCO/id=CCN`) is cleaned a part
    # at a time, and what one step leaves can be what the next needs. A pass only removes: it ends.
    for _ in range(MAX_PASSES):
        cleaned = _scrub_pass(text, job_ids, public_addresses_only)
        if cleaned == text:
            break
        text = cleaned
    return text


def _scrub_pass(text: str, job_ids: bool, public_addresses_only: bool) -> str:
    text = _URL_QUERY.sub(r"\1", text)
    text = _WORD.sub(_cut_query, text)
    for quoted in _QUOTED_FILES:
        text = quoted.sub(FILE, text)
    text = _FILE_TOKEN.sub(FILE, text)
    if job_ids:
        text = _JOB_ID.sub(JOB_ID, text)
    if public_addresses_only:
        text = _IPV4.sub(_replace_public_ipv4, text)
        text = _IPV6.sub(_replace_public_ipv6, text)
    else:
        text = _IPV4.sub(IP, text)
        text = _IPV6.sub(_replace_ipv6, text)
    return _TOKEN.sub(_smiles_in_token, text)


# --- the Sentry event ----------------------------------------------------------------------------

Event = dict[str, Any]

_COPIED = (
    "event_id",
    "timestamp",
    "platform",
    "level",
    "release",
    "environment",
    "fingerprint",
    "type",
)
SAFE_HEADERS = frozenset({"content-type", "content-length"})
_CONTEXTS_KEPT = ("runtime", "os")
_FRAME_KEPT = ("function", "module", "lineno", "colno", "in_app")


def _text(value: Any) -> str:
    return scrub_text(value if isinstance(value, str) else str(value))


def _strip_url(url: Any) -> str:
    """The URL of the request without its query string and fragment, ids and names scrubbed."""
    if not isinstance(url, str):
        return ""
    return _text(url.split("#", 1)[0].split("?", 1)[0])


def _frames(stacktrace: Any) -> dict[str, Any] | None:
    if not isinstance(stacktrace, Mapping):
        return None
    frames = []
    for frame in stacktrace.get("frames") or []:
        if not isinstance(frame, Mapping):
            continue
        kept = {key: frame[key] for key in _FRAME_KEPT if key in frame}
        for key in ("filename", "abs_path"):
            if isinstance(frame.get(key), str):
                kept[key] = _strip_url(frame[key])
        frames.append(kept)
    return {"frames": frames}


def _exception(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    kept: dict[str, Any] = {}
    if isinstance(value.get("type"), str):
        kept["type"] = _text(value["type"])
    if value.get("value") is not None:
        kept["value"] = _text(value["value"])
    if isinstance(value.get("module"), str):
        kept["module"] = value["module"]
    mechanism = value.get("mechanism")
    if isinstance(mechanism, Mapping):
        kept["mechanism"] = {key: mechanism[key] for key in ("type", "handled") if key in mechanism}
    stacktrace = _frames(value.get("stacktrace"))
    if stacktrace is not None:
        kept["stacktrace"] = stacktrace
    return kept


def _request(request: Any) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        return {}
    kept: dict[str, Any] = {}
    if isinstance(request.get("method"), str):
        kept["method"] = request["method"]
    if "url" in request:
        kept["url"] = _strip_url(request["url"])
    headers = request.get("headers")
    if isinstance(headers, Mapping):
        kept["headers"] = {
            str(name): _text(value)
            for name, value in headers.items()
            if str(name).lower() in SAFE_HEADERS
        }
    return kept


def scrub_event(event: Any, hint: Any = None) -> Any:
    """The `before_send` of the Sentry SDK: a new event with only what a crash report needs."""
    del hint
    try:
        return _scrub_event(event)
    except Exception:
        # Better no report than one that could not be checked.
        return None


def _scrub_event(event: Mapping[str, Any]) -> Event:
    kept: Event = {key: event[key] for key in _COPIED if key in event}
    if isinstance(event.get("sdk"), Mapping):
        kept["sdk"] = {key: event["sdk"][key] for key in ("name", "version") if key in event["sdk"]}
    if isinstance(event.get("logger"), str):
        kept["logger"] = event["logger"]
    if isinstance(event.get("server_name"), str):
        kept["server_name"] = event["server_name"]  # set by us: the service's name, not a host
    if event.get("message") is not None:
        kept["message"] = _text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, Mapping):
        formatted = logentry.get("formatted") or logentry.get("message")
        kept["logentry"] = {"message": _text(formatted)} if formatted is not None else {}
    if event.get("transaction") is not None:
        kept["transaction"] = _text(event["transaction"])
    tags = event.get("tags")
    if isinstance(tags, Mapping):
        kept["tags"] = {str(key): _text(value) for key, value in tags.items()}
    contexts = event.get("contexts")
    if isinstance(contexts, Mapping):
        kept["contexts"] = {
            key: {k: v for k, v in contexts[key].items() if isinstance(v, str)}
            for key in _CONTEXTS_KEPT
            if isinstance(contexts.get(key), Mapping)
        }
    exception = event.get("exception")
    if isinstance(exception, Mapping):
        kept["exception"] = {"values": [_exception(item) for item in exception.get("values") or []]}
    threads = event.get("threads")
    if isinstance(threads, Mapping):
        kept["threads"] = {
            "values": [
                {"stacktrace": stack}
                for item in threads.get("values") or []
                if isinstance(item, Mapping) and (stack := _frames(item.get("stacktrace")))
            ]
        }
    if "request" in event:
        kept["request"] = _request(event["request"])
    return kept


def scrub_transaction(event: Any, hint: Any = None) -> Any:
    """Performance data is off; if an SDK ever sent a transaction, none of it leaves."""
    del event, hint
    return None
