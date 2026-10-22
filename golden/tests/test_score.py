from decimer_golden.score import Label, Prediction, score, to_inchikey

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
