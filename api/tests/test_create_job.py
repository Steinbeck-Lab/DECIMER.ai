"""POST /api/jobs: accepted uploads, every rejection, normalisation."""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from PIL import Image

from decimer_api.models import JOB_ID_PATTERN
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeEnqueue
from tests.factories import (
    PNG_SIGNATURE,
    make_corrupt_pdf,
    make_encrypted_pdf,
    make_heic,
    make_jpeg,
    make_palette_png_with_transparency,
    make_pdf,
    make_png,
    make_transparent_png,
    make_webp,
    multipart,
    pdf_page_texts,
)


async def post(client: httpx.AsyncClient, files: list[tuple[str, bytes]]) -> httpx.Response:
    body, headers = multipart(files)
    return await client.post("/api/jobs", content=body, headers=headers)


def job_dirs(settings: Settings) -> list[Path]:
    return sorted(settings.data_dir.iterdir()) if settings.data_dir.exists() else []


def code(response: httpx.Response) -> str:
    return str(response.json()["detail"]["code"])


def stored_images(settings: Settings, job_id: str) -> list[Path]:
    return sorted((settings.data_dir / job_id / "input").iterdir())


# --- accepted uploads -----------------------------------------------------------------------


async def test_png_is_accepted_and_queued(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue
) -> None:
    response = await post(client, [("structure.png", make_png())])
    assert response.status_code == 202
    job = response.json()
    assert JOB_ID_PATTERN.fullmatch(job["id"])
    assert response.headers["location"] == f"/api/jobs/{job['id']}"
    assert job["status"] == "queued"
    assert job["queuePosition"] == 1
    assert job["pages"] == 1 and job["pagesProcessed"] == 1
    assert job["truncated"] is False
    assert job["results"] == [] and job["warnings"] == []
    assert [(s["name"], s["state"]) for s in job["stages"]][:2] == [
        ("upload", "done"),
        ("render", "pending"),
    ]
    assert "clientIpHash" not in job and "clientIp" not in job
    assert enqueue.job_ids == [job["id"]]
    assert [p.name for p in stored_images(settings, job["id"])] == ["0001.png"]


@pytest.mark.parametrize(
    "data", [make_jpeg(), make_webp(), make_heic()], ids=["jpeg", "webp", "heic"]
)
async def test_other_image_formats_are_normalised_to_png(
    client: httpx.AsyncClient, settings: Settings, data: bytes
) -> None:
    response = await post(client, [("x.bin", data)])
    assert response.status_code == 202, response.text
    (stored,) = stored_images(settings, response.json()["id"])
    with Image.open(stored) as image:
        assert image.format == "PNG" and image.mode == "RGB" and image.size == (48, 40)


async def test_several_images_are_kept_in_upload_order(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    sizes = [(10, 11), (20, 21), (30, 31)]
    response = await post(client, [(f"{n}.png", make_png(size)) for n, size in enumerate(sizes)])
    assert response.status_code == 202
    job = response.json()
    assert job["pages"] == 3
    stored = stored_images(settings, job["id"])
    assert [p.name for p in stored] == ["0001.png", "0002.png", "0003.png"]
    assert [Image.open(p).size for p in stored] == sizes


async def test_a_pasted_image_is_one_part_with_any_filename(
    client: httpx.AsyncClient,
) -> None:
    response = await post(client, [("blob", make_png())])
    assert response.status_code == 202


async def test_pdf_is_stored_with_a_server_chosen_name(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    response = await post(client, [("my paper (final).pdf", make_pdf(3))])
    assert response.status_code == 202
    job = response.json()
    assert job["pages"] == 3 and job["pagesProcessed"] == 3 and job["truncated"] is False
    assert [p.name for p in stored_images(settings, job["id"])] == ["document.pdf"]


async def test_client_filenames_never_reach_the_filesystem(
    make_client: Callable[..., httpx.AsyncClient], settings: Settings
) -> None:
    names = ["../../etc/passwd.png", "..\\..\\evil.png", "/abs/path.png", "x" * 500]
    for number, name in enumerate(names):
        # one address per upload: this test is not about the per-IP limit
        response = await post(make_client(f"203.0.113.{number + 1}"), [(name, make_png())])
        assert response.status_code == 202
    everything = [str(p.relative_to(settings.data_dir)) for p in settings.data_dir.rglob("*")]
    assert all(
        "passwd" not in item and "evil" not in item and "abs" not in item for item in everything
    )
    assert not (settings.data_dir.parent / "etc").exists()
    for directory in job_dirs(settings):
        assert JOB_ID_PATTERN.fullmatch(directory.name)
        assert [p.name for p in directory.iterdir()] == ["input"]


async def test_the_job_record_holds_a_hash_not_the_address(
    client: httpx.AsyncClient, store: JobStore
) -> None:
    response = await post(client, [("a.png", make_png())])
    job = await store.get(response.json()["id"])
    assert job is not None
    assert "203.0.113.10" not in job.model_dump_json(by_alias=True)
    assert len(job.client_ip_hash) == 32


# --- magic bytes ----------------------------------------------------------------------------


async def test_a_png_that_is_really_a_pdf_is_treated_as_a_pdf(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    response = await post(client, [("figure.png", make_pdf(2))])
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["pages"] == 2
    assert [p.name for p in stored_images(settings, job["id"])] == ["document.pdf"]


async def test_a_pdf_that_is_really_a_png_is_treated_as_an_image(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    response = await post(client, [("paper.pdf", make_png())])
    assert response.status_code == 202, response.text
    assert [p.name for p in stored_images(settings, response.json()["id"])] == ["0001.png"]


@pytest.mark.parametrize(
    "data",
    [
        b"just some text",
        b"",
        b"GIF89a" + b"\x00" * 40,
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00mif1avif" + b"\x00" * 20,
    ],
)
async def test_unknown_content_is_rejected_whatever_the_name_says(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue, data: bytes
) -> None:
    for name in ("structure.png", "paper.pdf", "photo.jpg"):
        response = await post(client, [(name, data)])
        assert response.status_code == 415
        assert code(response) == "unsupported"
    assert job_dirs(settings) == [] and enqueue.job_ids == []


async def test_a_bad_file_among_good_ones_rejects_the_whole_upload(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue
) -> None:
    response = await post(client, [("a.png", make_png()), ("b.png", b"not an image")])
    assert response.status_code == 415
    assert "2" in response.json()["detail"]["message"]  # says which file
    assert job_dirs(settings) == [] and enqueue.job_ids == []


# --- mixes and counts -----------------------------------------------------------------------


async def test_pdf_and_image_together_are_rejected(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue
) -> None:
    response = await post(client, [("a.pdf", make_pdf()), ("b.png", make_png())])
    assert response.status_code == 400
    assert code(response) == "mixed_upload"
    assert "pdf" in response.json()["detail"]["message"].lower()
    assert job_dirs(settings) == [] and enqueue.job_ids == []


async def test_a_mix_is_found_by_content_not_by_extension(client: httpx.AsyncClient) -> None:
    # Both are called .png; one of them is a PDF.
    response = await post(client, [("a.png", make_png()), ("b.png", make_pdf())])
    assert response.status_code == 400
    assert code(response) == "mixed_upload"


async def test_two_pdfs_are_rejected(client: httpx.AsyncClient) -> None:
    response = await post(client, [("a.pdf", make_pdf()), ("b.pdf", make_pdf())])
    assert response.status_code == 400
    assert code(response) == "multiple_pdfs"


@pytest.mark.parametrize("extra", [1, 2, 3, 20])
async def test_too_many_files_is_one_answer_however_far_over_the_limit(
    client: httpx.AsyncClient, settings: Settings, extra: int
) -> None:
    """The parser's own cap and ours used to give different codes at 52 files and more."""
    count = settings.max_upload_files + extra
    response = await post(client, [(f"{n}.png", make_png((4, 4))) for n in range(count)])
    assert response.status_code == 400
    assert code(response) == "too_many_files"


async def test_exactly_the_file_limit_is_fine(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    files = [(f"{n}.png", make_png((4, 4))) for n in range(settings.max_upload_files)]
    assert (await post(client, files)).status_code == 202


@pytest.mark.parametrize(("fields", "status"), [(0, 202), (2, 202), (3, 400)])
async def test_only_a_couple_of_extra_form_fields_are_read(
    client: httpx.AsyncClient, fields: int, status: int
) -> None:
    body, headers = multipart(
        [("a.png", make_png())], fields=[(f"f{n}", "x") for n in range(fields)]
    )
    response = await client.post("/api/jobs", content=body, headers=headers)
    assert response.status_code == status
    if status == 400:
        assert code(response) == "invalid_request"


async def test_no_file_part(client: httpx.AsyncClient) -> None:
    body, headers = multipart([("a.png", make_png())], field="image")  # wrong field name
    response = await client.post("/api/jobs", content=body, headers=headers)
    assert response.status_code == 400
    assert code(response) == "empty_upload"


async def test_not_multipart(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/jobs", json={"file": "x"})
    assert response.status_code == 415
    assert code(response) == "unsupported"


# --- size limits ----------------------------------------------------------------------------


async def test_upload_above_the_size_limit_is_413(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue
) -> None:
    big = PNG_SIGNATURE + b"\x00" * (3 * settings.max_upload_bytes)
    response = await post(client, [("big.png", big)])
    assert response.status_code == 413
    assert code(response) == "too_large"
    assert job_dirs(settings) == [] and enqueue.job_ids == []


async def test_size_limit_holds_without_a_content_length(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """A chunked upload cannot dodge the limit by omitting Content-Length."""
    body, headers = multipart(
        [("big.png", PNG_SIGNATURE + b"\x00" * (3 * settings.max_upload_bytes))]
    )
    sent = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal sent
        for start in range(0, len(body), 65536):
            sent += 65536
            yield body[start : start + 65536]

    request = client.build_request("POST", "/api/jobs", content=chunks(), headers=headers)
    assert "content-length" not in request.headers
    response = await client.send(request)
    assert response.status_code == 413
    assert sent < len(body)  # the server stopped reading


async def test_total_size_counts_all_files(client: httpx.AsyncClient, settings: Settings) -> None:
    part = PNG_SIGNATURE + b"\x00" * (settings.max_upload_bytes // 2 + 10)
    response = await post(client, [("a.png", part), ("b.png", part)])
    assert response.status_code == 413


async def test_files_just_above_the_limit_are_413_even_if_the_body_is_small_enough(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    # The request body has some framing overhead; the limit is on the files themselves.
    over = PNG_SIGNATURE + b"\x00" * (settings.max_upload_bytes - len(PNG_SIGNATURE) + 1)
    response = await post(client, [("a.png", over)])
    assert response.status_code == 413


async def test_an_image_with_too_many_pixels_is_413(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue
) -> None:
    # A 1-bit white image: a few KB on the wire, 36 million pixels once decoded.
    bomb = io.BytesIO()
    Image.new("1", (6000, 6000), 1).save(bomb, format="PNG")
    assert len(bomb.getvalue()) < 50_000
    response = await post(client, [("bomb.png", bomb.getvalue())])
    assert response.status_code == 413
    assert code(response) == "too_many_pixels"
    assert job_dirs(settings) == [] and enqueue.job_ids == []


async def test_pixel_cap_is_checked_before_decoding(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: Image.Image) -> None:
        raise AssertionError("decoded an image that is over the pixel cap")

    monkeypatch.setattr("PIL.ImageFile.ImageFile.load", explode)
    bomb = io.BytesIO()
    Image.new("1", (6000, 6000), 1).save(bomb, format="PNG")
    response = await post(client, [("bomb.png", bomb.getvalue())])
    assert response.status_code == 413


async def test_a_pdf_page_that_would_render_too_large_is_413(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    # 300 DPI of a 200 x 200 inch page is 3.6 billion pixels.
    response = await post(client, [("poster.pdf", make_pdf(1, page_size=(14400, 14400)))])
    assert response.status_code == 413
    assert code(response) == "too_many_pixels"
    assert job_dirs(settings) == []


# --- pdf page cap, corrupt and encrypted input ----------------------------------------------


async def test_pdf_above_ten_pages_keeps_the_first_ten_and_warns(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    response = await post(client, [("long.pdf", make_pdf(12))])
    assert response.status_code == 202
    job = response.json()
    assert job["truncated"] is True
    assert job["pages"] == 12 and job["pagesProcessed"] == 10
    assert [w["code"] for w in job["warnings"]] == ["pages_truncated"]
    assert "10" in job["warnings"][0]["message"] and "12" in job["warnings"][0]["message"]
    (stored,) = stored_images(settings, job["id"])
    assert pdf_page_texts(stored) == [f"page {n}" for n in range(1, 11)]


async def test_pdf_of_exactly_ten_pages_is_not_truncated(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    original = make_pdf(10)
    response = await post(client, [("ten.pdf", original)])
    job = response.json()
    assert response.status_code == 202
    assert job["truncated"] is False and job["warnings"] == []
    assert (stored_images(settings, job["id"])[0]).read_bytes() == original


async def test_corrupt_pdf_is_400(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue
) -> None:
    response = await post(client, [("broken.pdf", make_corrupt_pdf())])
    assert response.status_code == 400
    assert code(response) == "corrupt_file"
    assert job_dirs(settings) == [] and enqueue.job_ids == []


async def test_encrypted_pdf_is_400(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await post(client, [("locked.pdf", make_encrypted_pdf())])
    assert response.status_code == 400
    assert code(response) == "encrypted_pdf"
    assert job_dirs(settings) == []


@pytest.mark.parametrize(
    "data",
    [
        PNG_SIGNATURE + b"garbage that is not a png chunk" * 4,
        make_png((64, 64))[:60],  # truncated
        make_jpeg()[:30],
        make_heic()[:40],
        make_webp()[:20],
    ],
    ids=["png-garbage", "png-truncated", "jpeg-truncated", "heic-truncated", "webp-truncated"],
)
async def test_corrupt_images_are_400(
    client: httpx.AsyncClient, settings: Settings, data: bytes
) -> None:
    response = await post(client, [("x.png", data)])
    assert response.status_code == 400, response.text
    assert code(response) == "corrupt_file"
    assert job_dirs(settings) == []


# --- normalisation --------------------------------------------------------------------------


async def test_transparent_png_is_flattened_onto_white(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """The seg service reads with IMREAD_COLOR, which turns transparency black."""
    response = await post(client, [("transparent.png", make_transparent_png((60, 40)))])
    assert response.status_code == 202
    (stored,) = stored_images(settings, response.json()["id"])
    with Image.open(stored) as image:
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((59, 0)) == (255, 255, 255)
        # the opaque black stroke survives
        assert image.getpixel((30, 20))[0] < 40


async def test_palette_png_with_transparency_is_flattened_onto_white(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    response = await post(client, [("p.png", make_palette_png_with_transparency())])
    (stored,) = stored_images(settings, response.json()["id"])
    with Image.open(stored) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)


async def test_exif_orientation_is_applied_and_metadata_dropped(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    exif[0x010F] = "SecretCamera"
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), (10, 10, 10)).save(buffer, format="JPEG", exif=exif)
    response = await post(client, [("phone.jpg", buffer.getvalue())])
    (stored,) = stored_images(settings, response.json()["id"])
    with Image.open(stored) as image:
        assert image.size == (20, 40)
        assert not image.getexif()
        assert b"SecretCamera" not in stored.read_bytes()


# --- failure handling -----------------------------------------------------------------------


async def test_enqueue_failure_is_503_and_leaves_nothing_behind(
    client: httpx.AsyncClient, settings: Settings, enqueue: FakeEnqueue, store: JobStore
) -> None:
    enqueue.error = ConnectionError("redis is down")
    response = await post(client, [("a.png", make_png())])
    assert response.status_code == 503
    assert code(response) == "unavailable"
    assert job_dirs(settings) == []
    assert await store.queue_length() == 0
    enqueue.error = None
    assert (await post(client, [("a.png", make_png())])).status_code == 202


async def test_error_bodies_have_one_shape(client: httpx.AsyncClient) -> None:
    response = await post(client, [("a.png", b"nope")])
    assert set(response.json()) == {"detail"}
    assert set(response.json()["detail"]) == {"code", "message"}
    json.dumps(response.json())
