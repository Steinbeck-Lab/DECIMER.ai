from pathlib import Path

import pytest

from decimer_golden.score import (
    Label,
    Prediction,
    read_labels,
    read_predictions,
    score,
    to_inchikey,
    write_predictions,
)

CAFFEINE = "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
CAFFEINE_KEY = "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
BENZENE = "c1ccccc1"


def label(number: str, smiles: str) -> Label:
    key = to_inchikey(smiles)
    assert key is not None
    return Label(number=number, page=1, inchikey=key, smiles=smiles, checked=True)


def pred(i: int, smiles: str) -> Prediction:
    return Prediction(index=i, smiles=smiles, inchikey=to_inchikey(smiles))


def test_to_inchikey() -> None:
    assert to_inchikey(CAFFEINE) == CAFFEINE_KEY
    assert to_inchikey("C1CC") is None  # unclosed ring: RDKit rejects it
    assert to_inchikey("") is None


def test_counts_correct_extra_invalid_and_dropped() -> None:
    labels = [label("1", CAFFEINE), label("2", BENZENE)]
    preds = [pred(0, CAFFEINE), pred(1, "CCO"), pred(2, "C1CC"), pred(3, "")]
    s = score(labels, preds)
    assert (s.correct, s.extra, s.invalid, s.dropped) == (1, 1, 1, 1)
    assert s.missing == ["2"]


def test_each_prediction_matches_at_most_one_label() -> None:
    # Two labelled caffeines, one predicted: one correct, one missing.
    s = score([label("1", CAFFEINE), label("1b", CAFFEINE)], [pred(0, CAFFEINE)])
    assert (s.correct, s.extra) == (1, 0)
    assert s.missing == ["1b"]
    # One labelled caffeine, predicted twice: the second is extra.
    s = score([label("1", CAFFEINE)], [pred(0, CAFFEINE), pred(1, CAFFEINE)])
    assert (s.correct, s.extra, s.missing) == (1, 1, [])


def test_order_of_predictions_does_not_matter() -> None:
    labels = [label("1", CAFFEINE), label("2", BENZENE)]
    a = score(labels, [pred(0, CAFFEINE), pred(1, BENZENE)])
    b = score(labels, [pred(0, BENZENE), pred(1, CAFFEINE)])
    assert a.correct == b.correct == 2


def test_an_empty_smiles_is_invalid_when_the_stack_says_it_tried_and_dropped_otherwise() -> None:
    tried = Prediction(index=0, smiles="", inchikey=None, valid=False)
    cut = Prediction(index=1, smiles="", inchikey=None)  # legacy's empty rows have valid=None
    s = score([], [tried, cut])
    assert (s.invalid, s.dropped) == (1, 1)


@pytest.mark.parametrize("valid", [True, False])
def test_an_empty_smiles_the_stack_returned_is_invalid_whatever_its_verdict(valid: bool) -> None:
    # Only the padding rows for what the cap cut (valid=None) are "cut"; a result the stack
    # reported, even a contradictory one (empty SMILES, valid=True), is not a cut structure.
    returned = Prediction(index=0, smiles="  ", inchikey=None, valid=valid)
    s = score([], [returned])
    assert (s.invalid, s.dropped) == (1, 0)


def test_predictions_round_trip_through_csv(tmp_path: Path) -> None:
    rows = [
        Prediction(
            index=0,
            smiles=CAFFEINE,
            inchikey=CAFFEINE_KEY,
            segment="crops/00_crop-0.png",
            classifier="True",
            page=3,
            valid=True,
            confidence=0.1234567890123,
            name="caffeine, 1,3,7-trimethylpurine-2,6-dione",
            name_tier="pin_verified",
        ),
        Prediction(index=1, smiles="", inchikey=None, valid=False, classifier=""),
        Prediction(index=2, smiles="", inchikey=None),
    ]
    write_predictions(tmp_path / "p.csv", rows)
    assert read_predictions(tmp_path / "p.csv") == rows


PREDICTIONS_HEADER = "index,segment,smiles,inchikey,classifier,page,valid,confidence,name,name_tier"


@pytest.mark.parametrize(("text", "valid"), [("True", True), ("False", False), ("", None)])
def test_the_valid_column_reads_true_false_or_empty(
    tmp_path: Path, text: str, valid: bool | None
) -> None:
    path = tmp_path / "p.csv"
    path.write_text(f"{PREDICTIONS_HEADER}\n0,,CCO,,,1,{text},,,\n")
    (p,) = read_predictions(path)
    assert p.valid is valid


@pytest.mark.parametrize("text", ["true", "false", "TRUE", "1", "0", "yes", "no", " True", "None"])
def test_any_other_valid_value_is_an_error_not_a_silent_false(tmp_path: Path, text: str) -> None:
    path = tmp_path / "p.csv"
    path.write_text(f"{PREDICTIONS_HEADER}\n0,,CCO,,,1,{text},,,\n")
    with pytest.raises(ValueError, match="valid"):
        read_predictions(path)


def test_legacy_runs_still_read_and_score() -> None:
    run = Path(__file__).resolve().parents[1] / "runs" / "legacy-20261022-140954"
    predictions = read_predictions(run / "predictions.csv")
    assert len(predictions) == 32
    legacy_only = (None, None, None, "", "")
    assert all(
        (p.page, p.valid, p.confidence, p.name, p.name_tier) == legacy_only for p in predictions
    )
    assert sum(1 for p in predictions if not p.smiles) == 12  # cut by the cap
    s = score([], predictions)
    assert s.dropped == 12
    assert s.invalid == 1


GOLDEN = Path(__file__).resolve().parents[1]


def test_the_committed_legacy_run_scores_exactly_as_the_baseline_against_the_real_labels() -> None:
    """Pins the oracle: the scoring rules and labels.csv together must keep giving the legacy
    baseline of the README (13/26 correct, 6 extra, 1 invalid, 12 cut by the cap). Change either
    only with a written reason."""
    labels = read_labels(GOLDEN / "labels.csv")
    predictions = read_predictions(GOLDEN / "runs" / "legacy-20261022-140954" / "predictions.csv")
    s = score(labels, predictions)
    assert (s.correct, s.labels, s.extra, s.invalid, s.dropped) == (13, 26, 6, 1, 12)
