"""Draw the boxes the seg model returns on a PDF page, for a human to check by eye.

    uv run python scripts/overlay.py ../../golden/zou2024_penicillium_shentong.pdf 3 \
        --out /tmp/page3.png --write-bboxes tests/fixtures/page3_bboxes.json

The page is rendered with PyMuPDF at 300 DPI (the pipeline renders with pypdfium2), sent
through `SegModel.infer` exactly as the HTTP service would, and each returned bbox is drawn with
its index. Page numbers start at 1. Prints the PNG path, the timings and the memory use.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

# decimer_seg must be imported before cv2: it sets OPENCV_IO_MAX_IMAGE_PIXELS, which OpenCV reads
# once when cv2 is first imported.
from decimer_seg.model import SegModel

# isort: split
import cv2
import numpy as np
import pymupdf


def _status(key: str) -> str:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip()
    return "?"


def render_page(pdf: Path, page_number: int, dpi: int) -> bytes:
    with pymupdf.open(pdf) as document:
        pixmap = document[page_number - 1].get_pixmap(dpi=dpi, alpha=False)
        return bytes(pixmap.tobytes("png"))


def draw_boxes(page_png: bytes, segments: list[dict[str, object]]) -> np.ndarray:
    page = cv2.imdecode(np.frombuffer(page_png, np.uint8), cv2.IMREAD_COLOR)
    red = (0, 0, 255)
    for segment in segments:
        x0, y0, x1, y1 = segment["bbox"]  # type: ignore[misc]
        cv2.rectangle(page, (x0, y0), (x1, y1), red, 5)
        cv2.putText(
            page,
            str(segment["index"]),
            (x0, max(y0 - 12, 30)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.6,
            red,
            4,
        )
    return page


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("pdf", type=Path)
    parser.add_argument("page", type=int, help="page number, starting at 1")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--out", type=Path, help="overlay PNG (default: a file in the temp dir)")
    parser.add_argument("--write-bboxes", type=Path, help="also save the boxes as a JSON fixture")
    args = parser.parse_args()
    out = args.out or Path(tempfile.gettempdir()) / f"seg-overlay-{args.pdf.stem}-p{args.page}.png"

    page_png = render_page(args.pdf, args.page, args.dpi)

    start = time.perf_counter()
    model = SegModel()
    model.load()
    print(f"model load: {time.perf_counter() - start:.1f} s, VmRSS {_status('VmRSS')}")

    start = time.perf_counter()
    result = model.infer(page_png, {"page_index": str(args.page - 1)})
    print(
        f"one page: {time.perf_counter() - start:.1f} s, VmRSS {_status('VmRSS')}, "
        f"VmHWM {_status('VmHWM')}, threads {_status('Threads')}"
    )

    cv2.imwrite(str(out), draw_boxes(page_png, result["segments"]))
    print(f"{len(result['segments'])} boxes on a {result['width']}x{result['height']} page")
    for segment in result["segments"]:
        print(f"  {segment['index']:2d}  {segment['bbox']}")
    print(f"overlay written to {out}")

    if args.write_bboxes:
        fixture = {
            "pdf": args.pdf.name,
            "page_index": args.page - 1,
            "dpi": args.dpi,
            "width": result["width"],
            "height": result["height"],
            "bboxes": [s["bbox"] for s in result["segments"]],
        }
        args.write_bboxes.write_text(json.dumps(fixture, indent=2) + "\n")
        print(f"bboxes written to {args.write_bboxes}")


if __name__ == "__main__":
    main()
