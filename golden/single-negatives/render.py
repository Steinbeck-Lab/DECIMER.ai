"""Regenerates the images and negatives.csv of this folder from the golden paper.

    cd api && uv run python ../golden/single-negatives/render.py

pypdfium2 is the API's renderer (it is not a dependency of decimer-golden). The pages are
rendered at 300 DPI, as the worker renders them, and each fixture is a rectangle of a page, in
page pixels, cut the way a user would paste a screenshot of a figure: with the compound number,
a reaction arrow, the text next to it, or the edge of the next structure. `n14` is the one
composite: rows of Table 1 (page 4) above compound 11 (page 3).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

HERE = Path(__file__).resolve().parent
PAPER = HERE.parent / "zou2024_penicillium_shentong.pdf"
DPI = 300

Rect = tuple[int, int, int, int]
# (file, page, rectangle in page pixels, kind, what is in it, label numbers in labels.csv)
FIXTURES: list[tuple[str, int, Rect, str, str, str]] = [
    (
        "n01_number_cmpd1",
        3,
        (301, 1792, 752, 2293),
        "structure+number",
        "compound 1 with its number and the (2R,3R,16R) caption",
        "1",
    ),
    (
        "n02_number_cmpd11_caption_band",
        3,
        (1178, 2770, 1754, 3163),
        "structure+number",
        "compound 11, its number, the grey caption band and the edge of compound 12",
        "11",
    ),
    (
        "n03_number_cmpd5",
        3,
        (326, 2344, 752, 2719),
        "structure+number",
        "compound 5 with its number",
        "5",
    ),
    (
        "n04_arrow_trp",
        7,
        (263, 927, 664, 1191),
        "structure+arrow",
        "tryptophan, a dashed reaction arrow and the enzyme label ShnA",
        "S1",
    ),
    (
        "n05_arrow_succinimide",
        7,
        (1729, 902, 2231, 1228),
        "structure+arrow",
        "the succinimide intermediate, a dashed arrow with -H2O, the previous structure edge",
        "S5",
    ),
    (
        "n06_dashedbox_cmpd1",
        7,
        (276, 2118, 602, 2514),
        "structure+box",
        "compound 1 in its red dashed box with its number",
        "S9",
    ),
    (
        "n07_dashedbox_cmpd2",
        7,
        (1855, 1441, 2181, 1830),
        "structure+box",
        "compound 2 in its red dashed box with its number",
        "S8",
    ),
    (
        "n08_arrow_prenyltrp",
        7,
        (639, 952, 1053, 1228),
        "structure+arrow",
        "reverse-prenylated tryptophan, a dashed arrow and ShnC, the edges of its neighbours",
        "S2",
    ),
    (
        "n09_two_5_6",
        3,
        (326, 2344, 1203, 2719),
        "two structures",
        "compounds 5 and 6 side by side",
        "5;6",
    ),
    (
        "n10_two_9_10",
        3,
        (326, 2770, 1203, 3083),
        "two structures",
        "compounds 9 and 10 side by side",
        "9;10",
    ),
    (
        "n11_two_1_2",
        3,
        (301, 1792, 1203, 2293),
        "two structures",
        "compounds 1 and 2 side by side",
        "1;2",
    ),
    (
        "n12_text_above_cmpd1",
        3,
        (238, 1366, 752, 2293),
        "structure+text",
        "the last lines of the left text column above compound 1 (structure under 40% of it)",
        "1",
    ),
    (
        "n13_text_beside_cmpd3",
        3,
        (1241, 1366, 1729, 2231),
        "structure+text",
        "the right text column, cut mid-sentence, above compound 3 and the edge of compound 4",
        "3",
    ),
]
TABLE = (4, (276, 627, 1216, 1053))  # rows 1-8a of Table 1
COMPOUND_11 = (3, (1178, 2770, 1754, 3095))
GAP = 60


def main() -> None:
    pdf = pdfium.PdfDocument(PAPER)
    scale = DPI / 72 * (1 - 1e-9)  # exactly 2480 x 3509 px, as the worker's renderer

    def page(number: int) -> Image.Image:
        return pdf[number - 1].render(scale=scale).to_pil().convert("RGB")

    pages = {number: page(number) for number in (3, 4, 7)}
    rows = []
    for name, number, rect, kind, contains, labels in FIXTURES:
        pages[number].crop(rect).save(HERE / f"{name}.png")
        rows.append((name, str(number), " ".join(map(str, rect)), kind, contains, labels))
    table = pages[TABLE[0]].crop(TABLE[1])
    structure = pages[COMPOUND_11[0]].crop(COMPOUND_11[1])
    composite = Image.new(
        "RGB", (max(table.width, structure.width), table.height + GAP + structure.height), "white"
    )
    composite.paste(table, (0, 0))
    composite.paste(structure, (0, table.height + GAP))
    composite.save(HERE / "n14_table_above_cmpd11.png")
    rows.append(
        (
            "n14_table_above_cmpd11",
            "4+3",
            f"{' '.join(map(str, TABLE[1]))} over {' '.join(map(str, COMPOUND_11[1]))}",
            "structure+table",
            "COMPOSITE: rows of Table 1 (page 4) above compound 11 (page 3), 60 px apart",
            "11",
        )
    )
    with (HERE / "negatives.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "page", "region_px", "kind", "contains", "labels"])
        w.writerows((f"{name}.png", *rest) for name, *rest in rows)


if __name__ == "__main__":
    main()
