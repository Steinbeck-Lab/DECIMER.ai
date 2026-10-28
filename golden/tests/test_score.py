from pathlib import Path

import pytest

from decimer_golden import main
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
GOLDEN = Path(__file__).resolve().parents[1]


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


def test_counts_correct_extra_invalid_refused_and_dropped() -> None:
    labels = [label("1", CAFFEINE), label("2", BENZENE)]
    refusal = Prediction(index=4, smiles="", inchikey=None, valid=False)
    preds = [pred(0, CAFFEINE), pred(1, "CCO"), pred(2, "C1CC"), pred(3, ""), refusal]
    s = score(labels, preds)
    assert (s.correct, s.extra, s.invalid, s.refused, s.dropped) == (1, 1, 1, 1, 1)
    assert s.missing == ["2"]


def incomplete_pred(i: int, smiles: str) -> Prediction:
    return Prediction(
        index=i, smiles=smiles, inchikey=to_inchikey(smiles), valid=False, incomplete=True
    )


def test_a_cut_off_prediction_is_incomplete_not_invalid_extra_or_correct() -> None:
    # The text is cut off at the token limit, so it is not a reading,
    # whatever RDKit says of it: an unclosed ring (RDKit rejects it) and a chain (RDKit reads it)
    # both go to `incomplete`, and a label they sit on stays unmatched.
    labels = [label("1", CAFFEINE), label("2", BENZENE)]
    preds = [
        pred(0, CAFFEINE),
        incomplete_pred(1, "C1CC"),
        incomplete_pred(2, "CCCCCC"),
        pred(3, "C1CC"),  # a plain invalid one, for contrast
    ]
    s = score(labels, preds)
    assert (s.correct, s.extra, s.invalid, s.incomplete, s.refused, s.dropped) == (1, 0, 1, 2, 0, 0)
    assert s.missing == ["2"]


def test_a_prediction_without_the_flag_is_scored_as_before() -> None:
    s = score([label("1", CAFFEINE)], [pred(0, "C1CC"), pred(1, "CCO")])
    assert (s.invalid, s.incomplete, s.extra) == (1, 0, 1)


def test_the_flag_on_an_empty_smiles_changes_nothing() -> None:
    cut = Prediction(index=0, smiles="", inchikey=None, valid=False, incomplete=True)
    s = score([], [cut])
    assert (s.refused, s.incomplete, s.invalid) == (1, 0, 0)


def test_the_newest_committed_run_reads_a_cut_off_excluded_label_as_incomplete() -> None:
    """A cut-off prediction of an excluded label is "incomplete", not a new failure; before the
    `incomplete` flag it was a refusal. The committed run `next-20261026-151657` has the
    token-limit crop at index 12, the `1 (Fig 3)` drawing, which is excluded from scoring and was
    refused (empty SMILES). Built with the flag it carries a cut-off SMILES that RDKit and CDK 2.8
    both reject (here a short unclosed-ring SMILES stands in for its 302
    characters, which does not change what RDKit says). Read the two ways:

    - without the flag it is one more invalid SMILES: invalid 2 against the earlier version's 1;
    - with it, as the API sends it: `invalid 1 · incomplete 1`, correct 13/24 = the earlier
      version, the pass rule's invalid count 1 against its 1.
    """
    labels = read_labels(GOLDEN / "labels.csv")
    run = GOLDEN / "runs" / "next-20261026-151657" / "predictions.csv"
    predictions = read_predictions(run)
    assert (predictions[12].smiles, predictions[12].valid) == ("", False)  # the refusal then
    as_run = score(labels, predictions)
    assert (as_run.correct, as_run.extra, as_run.invalid, as_run.refused, as_run.incomplete) == (
        13,
        5,
        1,
        1,
        0,
    )

    cut_off = "C1CC"
    unflagged = [*predictions[:12], pred(12, cut_off), *predictions[13:]]
    s = score(labels, unflagged)
    assert (s.correct, s.labels, s.extra, s.invalid, s.refused, s.incomplete) == (
        13,
        24,
        5,
        2,
        0,
        0,
    )
    flagged = [*predictions[:12], incomplete_pred(12, cut_off), *predictions[13:]]
    s = score(labels, flagged)
    assert (s.correct, s.labels, s.extra, s.invalid, s.refused, s.incomplete) == (
        13,
        24,
        5,
        1,
        0,
        1,
    )
    assert (s.dropped, s.excluded, s.missing) == (as_run.dropped, as_run.excluded, as_run.missing)


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


def test_an_empty_smiles_is_refused_when_the_stack_says_it_tried_and_dropped_otherwise() -> None:
    tried = Prediction(index=0, smiles="", inchikey=None, valid=False)
    cut = Prediction(index=1, smiles="", inchikey=None)  # an empty row with valid=None
    s = score([], [tried, cut])
    assert (s.invalid, s.refused, s.dropped) == (0, 1, 1)


@pytest.mark.parametrize("valid", [True, False])
def test_an_empty_smiles_the_stack_returned_is_refused_whatever_its_verdict(valid: bool) -> None:
    # Invalid means "SMILES that RDKit rejects": a recognition that
    # declined to answer (the new stack's 422) is a refusal, reported apart from invalid. Only the
    # padding rows for what the cap cut (valid=None) are "cut"; a result the stack reported, even
    # a contradictory one (empty SMILES, valid=True), is neither invalid nor a cut structure.
    returned = Prediction(index=0, smiles="  ", inchikey=None, valid=valid)
    s = score([], [returned])
    assert (s.invalid, s.refused, s.dropped) == (0, 1, 0)


def test_a_smiles_rdkit_rejects_is_invalid_not_refused_whatever_the_stacks_verdict() -> None:
    # Invalid stays "a SMILES RDKit rejects": it has text, so it is never a refusal, also when
    # the stack itself flagged it (valid=False) or vouched for it (valid=True).
    rows = [
        Prediction(index=0, smiles="C1CC", inchikey=None, valid=False),
        Prediction(index=1, smiles="C1CC", inchikey=None, valid=True),
        Prediction(index=2, smiles="C1CC", inchikey=None),
    ]
    s = score([], rows)
    assert (s.invalid, s.refused, s.dropped) == (3, 0, 0)


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
        Prediction(index=3, smiles="C1CC", inchikey=None, valid=False, incomplete=True),
    ]
    write_predictions(tmp_path / "p.csv", rows)
    assert read_predictions(tmp_path / "p.csv") == rows
    assert (tmp_path / "p.csv").read_text().splitlines()[0] == PREDICTIONS_HEADER_WITH_INCOMPLETE


@pytest.mark.parametrize(("text", "flag"), [("True", True), ("False", False), ("", False)])
def test_the_incomplete_column_reads_true_false_or_empty(
    tmp_path: Path, text: str, flag: bool
) -> None:
    path = tmp_path / "p.csv"
    path.write_text(f"{PREDICTIONS_HEADER_WITH_INCOMPLETE}\n0,,C1CC,,,1,False,,,,{text}\n")
    (p,) = read_predictions(path)
    assert p.incomplete is flag


@pytest.mark.parametrize("text", ["true", "1", "yes", " True", "None"])
def test_any_other_incomplete_value_is_an_error_not_a_silent_false(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "p.csv"
    path.write_text(f"{PREDICTIONS_HEADER_WITH_INCOMPLETE}\n0,,C1CC,,,1,False,,,,{text}\n")
    with pytest.raises(ValueError, match="incomplete"):
        read_predictions(path)


def test_a_run_from_before_the_column_reads_as_complete(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text(f"{PREDICTIONS_HEADER}\n0,,CCO,,,1,True,,,\n")
    (p,) = read_predictions(path)
    assert p.incomplete is False


PREDICTIONS_HEADER = "index,segment,smiles,inchikey,classifier,page,valid,confidence,name,name_tier"
# What `write_predictions` writes: the same columns and `incomplete`.
PREDICTIONS_HEADER_WITH_INCOMPLETE = PREDICTIONS_HEADER + ",incomplete"


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
    run = GOLDEN / "runs" / "legacy-20261022-140954"
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
    assert s.refused == 0


def test_the_committed_legacy_run_scores_exactly_as_the_baseline_against_the_real_labels() -> None:
    """Pins the oracle: the scoring rules and labels.csv together must keep giving the earlier
    version's baseline of the README (13/24 correct, 6 extra, 1 invalid, 0 refused, 12 cut by the
    cap). Change either only with a written reason.

    The count is 13/24, not 13/26: the two Figure 3 drawings are excluded from scoring
    (the label review marked them "Difficult", correlation arrows over the bonds; labels.csv
    `scored=no`). Evidence: the decision is the primary source; an independent scorer that does
    not import decimer_golden gives 13/24, 6, 1, 12 and 0 predictions on the excluded labels for
    this run (and 13/24 for every committed next run); reverting the exclusion fails this test.
    The earlier version has no refusals: its only empty SMILES are the 12 cut rows (valid empty).
    What would make it wrong: a stack that reads a Figure 3 copy correctly while missing the
    Figure 2 one is scored correct (multisets cannot tell the drawings apart)."""
    labels = read_labels(GOLDEN / "labels.csv")
    predictions = read_predictions(GOLDEN / "runs" / "legacy-20261022-140954" / "predictions.csv")
    s = score(labels, predictions)
    assert (s.correct, s.labels, s.extra, s.invalid, s.refused, s.dropped, s.excluded) == (
        13,
        24,
        6,
        1,
        0,
        12,
        0,
    )


def test_the_committed_new_stack_run_scores_exactly_as_the_baseline_against_the_real_labels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pins the new stack's baseline (run `next-20261023-121537`): 13/24
    correct, 5 extra, 1 invalid, 1 refused, 12 cut by the cap, 0 on an excluded label.

    Evidence: a scorer written apart from this one, which does not import
    decimer_golden (RDKit InChIKey multisets from labels.csv, the two `scored=no` labels as
    uncounted spares, empty SMILES split by the `valid` column), gives exactly these numbers for
    this run, and the same for the other committed next runs.

    Which rows they are. The one refusal is index 12: the `1 (Fig 3)` drawing (compound 1 with
    correlation arrows, the crop was looked at), the very drawing that is excluded from scoring.
    The classifier called it "not a structure", the OCSR declined it (empty SMILES, `valid=False`,
    the job's `recognition_failed` warning for structure 13). The earlier version read the same
    crop as a valid but wrong SMILES (an extra). The one invalid is index 13, a structure with no
    label whose SMILES RDKit rejects; the earlier version's one invalid is index 15, the
    `3 (Fig 3)` drawing.

    What the rule changes at the gate. Under the old rule (a refusal is invalid) the new stack had
    2 invalid against the earlier version's 1, which would fail only because of a refusal on an
    excluded drawing. With refusals counted apart (Invalid is "SMILES that RDKit rejects") it is 1
    against 1, and with the excluded drawings left out entirely it would
    also be 1 against 1. Counting the refusal as invalid again gives 2 and 0 and fails this test.

    The whole `score` output is pinned too: the line, the exact missing list (the scored labels
    only) and no WARNING, since every scored label is checked.
    What would make it wrong: a stack that refuses everything shows 0 invalid, so the pass
    rule's invalid limit cannot see it; the correct count is what catches that."""
    labels = read_labels(GOLDEN / "labels.csv")
    run = GOLDEN / "runs" / "next-20261023-121537" / "predictions.csv"
    s = score(labels, read_predictions(run))
    assert (s.correct, s.labels, s.extra, s.invalid, s.refused, s.dropped, s.excluded) == (
        13,
        24,
        5,
        1,
        1,
        12,
        0,
    )
    assert main(["score", str(run)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "correct 13/24 · extra 5 · invalid 1 · refused 1 · dropped by cap 12 · excluded 0\n"
        "missing labels: 2, 3, 3 (Fig 3), S4, S5, S6, S7, S8, S9, S10, S11\n"
    )
    assert captured.err == ""


def test_the_score_line_says_incomplete_only_for_a_run_that_has_some(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "p.csv"
    write_predictions(path, [pred(0, "C1CC"), incomplete_pred(1, "C1CC")])
    assert main(["score", str(path)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == (
        "correct 0/24 · extra 0 · invalid 1 · incomplete 1 · refused 0 · dropped by cap 0 · "
        "excluded 0"
    )
    write_predictions(path, [pred(0, "C1CC")])
    assert main(["score", str(path)]) == 0
    assert "incomplete" not in capsys.readouterr().out


def test_the_score_command_warns_about_an_unchecked_scored_label_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The two excluded drawings are checked=no in the real file and never warned about; a scored
    # label that loses its check is, and is named.
    text = (GOLDEN / "labels.csv").read_text()
    row = next(line for line in text.splitlines() if line.startswith("S5,"))
    assert ",yes,yes," in row
    flipped = tmp_path / "labels.csv"
    flipped.write_text(text.replace(row, row.replace(",yes,yes,", ",no,yes,", 1)))
    monkeypatch.setattr("decimer_golden.LABELS", flipped)
    run = GOLDEN / "runs" / "next-20261023-121537" / "predictions.csv"
    assert main(["score", str(run)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "WARNING: 1 labels not yet checked by hand: S5"
    assert len([line for line in lines if line.startswith("WARNING")]) == 1


def test_an_excluded_label_is_neither_counted_nor_turns_its_reading_into_an_extra() -> None:
    def label(number: str, key: str, scored: bool = True) -> Label:
        return Label(number=number, page=1, inchikey=key, smiles="C", checked=True, scored=scored)

    labels = [label("1", "K1"), label("1 (Fig 3)", "K1", scored=False), label("2", "K2")]
    both = [
        Prediction(index=0, smiles="C", inchikey="K1"),
        Prediction(index=1, smiles="C", inchikey="K1"),
    ]
    s = score(labels, both)
    assert (s.labels, s.correct, s.extra, s.excluded, s.missing) == (2, 1, 0, 1, ["2"])
    # a third reading of the same molecule has no label left: extra
    s = score(labels, [*both, Prediction(index=2, smiles="C", inchikey="K1")])
    assert (s.correct, s.extra, s.excluded) == (1, 1, 1)
    # the scored label is matched first, so one reading is correct, not excluded
    s = score(labels, both[:1])
    assert (s.correct, s.excluded) == (1, 0)
    # an unread molecule is missing under its scored label's number, never the excluded copy's
    assert score(labels, []).missing == ["1", "2"]


def test_the_scored_column_is_read_strictly(tmp_path: Path) -> None:
    head = "number,page,inchikey,smiles,checked,scored,note,legacy_segment\n"
    good = tmp_path / "good.csv"
    good.write_text(head + "1,1,K,C,yes,no,,0\n")
    assert [lb.scored for lb in read_labels(good)] == [False]
    old = tmp_path / "old.csv"
    old.write_text("number,page,inchikey,smiles,checked,note,legacy_segment\n1,1,K,C,yes,,0\n")
    assert [lb.scored for lb in read_labels(old)] == [True]  # older copies: every label scored
    bad = tmp_path / "bad.csv"
    bad.write_text(head + "1,1,K,C,yes,maybe,,0\n")
    with pytest.raises(ValueError, match="scored"):
        read_labels(bad)
