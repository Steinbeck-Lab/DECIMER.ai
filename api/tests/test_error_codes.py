"""`ErrorDetail.code` is an enum: the schema lists every code."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import decimer_api
from decimer_api.errors import ApiError, ErrorCode, ErrorDetail
from decimer_api.ingest_runner import CHILD_ERRORS

SOURCE = Path(decimer_api.__file__).parent


def schema(app: FastAPI) -> dict[str, dict[str, object]]:
    components: dict[str, dict[str, object]] = app.openapi()["components"]["schemas"]
    return components


def test_the_schema_lists_every_code(app: FastAPI) -> None:
    schemas = schema(app)
    assert set(schemas["ErrorCode"]["enum"]) == {code.value for code in ErrorCode}  # type: ignore[call-overload]
    code = schemas["ErrorDetail"]["properties"]["code"]  # type: ignore[index]
    assert code == {"$ref": "#/components/schemas/ErrorCode"}


def test_the_codes_are_the_ones_the_frontend_knows_and_the_new_ones() -> None:
    assert {code.value for code in ErrorCode} == {
        "empty_upload",
        "too_many_files",
        "unsupported",
        "mixed_upload",
        "multiple_pdfs",
        "corrupt_file",
        "encrypted_pdf",
        "too_many_pixels",
        "processing_limit",
        "too_large",
        "invalid_request",
        "rate_limited",
        "queue_full",
        "busy",
        "unavailable",
        "not_found",
        "no_results",
        "method_not_allowed",
        "http_error",
        "internal_error",
    }


def test_an_unknown_code_is_a_bug_found_where_it_is_raised() -> None:
    with pytest.raises(ValueError, match="made_up"):
        ApiError(400, "made_up", "x")
    with pytest.raises(ValueError):
        ErrorDetail(code="made_up", message="x")  # type: ignore[arg-type]


def test_a_code_given_as_a_string_is_the_enum_member() -> None:
    error = ApiError(404, "not_found", "x")
    assert error.code is ErrorCode.NOT_FOUND
    assert error.code == "not_found"  # and still equals the string, for callers that compare


def _literal_codes() -> dict[str, list[str]]:
    """Every string that is passed as the code of an `ApiError(...)` in the source, by file."""
    found: dict[str, list[str]] = {}
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ApiError"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                found.setdefault(path.name, []).append(node.args[1].value)
    return found


def test_every_code_written_as_a_string_in_the_source_is_in_the_enum() -> None:
    literals = _literal_codes()
    assert literals  # the scan finds something: uploads.py, ingest.py, routes.py, ...
    known = {code.value for code in ErrorCode}
    unknown = {
        (file, code) for file, codes in literals.items() for code in codes if code not in known
    }
    assert unknown == set()


def test_the_codes_a_child_process_may_report_are_in_the_enum() -> None:
    assert set(CHILD_ERRORS) <= {code.value for code in ErrorCode}


def test_every_code_is_used_somewhere() -> None:
    """A code nobody raises is a promise to the frontend that is never kept."""
    used: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        if path.name == "errors.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "ErrorCode":
                    used.add(ErrorCode[node.attr].value)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in {code.value for code in ErrorCode}:
                    used.add(node.value)
    assert {code.value for code in ErrorCode} - used == set()


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/jobs/" + "0" * 32, 404),  # not_found
        ("GET", "/nowhere", 404),
        ("PUT", "/api/jobs/" + "0" * 32, 405),  # method_not_allowed
        ("POST", "/api/engines", 405),
        ("POST", "/api/jobs/" + "0" * 32 + "/results/x/validate", 422),  # invalid_request
    ],
)
async def test_the_answers_the_api_gives_carry_a_code_from_the_enum(
    client: httpx.AsyncClient, method: str, path: str, status: int
) -> None:
    response = await client.request(method, path, json={})
    assert response.status_code == status
    ErrorDetail.model_validate(response.json()["detail"])  # raises for a code outside the enum
