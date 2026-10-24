"""The memory-leak shim: a direct call of `MaskRCNN.keras_model` runs as `predict_on_batch`.

decimer-segmentation 6914cac runs its forward pass as `keras_model(inputs, training=False)` on
tensors, which leaks about 3.5 MB per call (`decimer_seg.model._PredictOnBatch` says why and when to
remove the shim). No weights and no TensorFlow here: the Keras model is a recorder that fails the
test when it is called the leaking way. The real model is in `test_integration.py`.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import numpy as np
import pytest

import decimer_seg.model as seg_model
from decimer_seg.model import _PredictOnBatch, _route_forward_pass_through_predict_on_batch


class FakeTensor:
    """What `tf.constant` gives the library: an object with `__array__` (and `.numpy()`)."""

    def __init__(self, value: np.ndarray[Any, Any]) -> None:
        self._value = value

    def __array__(self, dtype: Any = None, copy: Any = None) -> np.ndarray[Any, Any]:
        return self._value.astype(dtype) if dtype is not None else self._value

    def numpy(self) -> np.ndarray[Any, Any]:
        return self._value


class FakeKerasModel:
    """Records `predict_on_batch`; a direct call is the leak, so it is a failure unless allowed."""

    layers = ("conv", "dense")

    def __init__(self, outputs: Any, direct_call_allowed: bool = False) -> None:
        self.outputs = outputs
        self.batches: list[Any] = []
        self.direct_calls: list[Any] = []
        self.direct_call_allowed = direct_call_allowed

    def predict_on_batch(self, inputs: Any) -> Any:
        self.batches.append(inputs)
        return self.outputs

    def __call__(self, inputs: Any, training: Any = None, **kwargs: Any) -> Any:
        if not self.direct_call_allowed:
            raise AssertionError("direct eager call of the model: it leaks memory on every call")
        self.direct_calls.append((inputs, training, kwargs))
        return "direct"


def seven_outputs() -> list[np.ndarray[Any, Any]]:
    rng = np.random.default_rng(1)
    return [
        rng.random((1, 3, 6), dtype=np.float32),  # detections
        rng.random((1, 5, 2), dtype=np.float32),
        rng.random((1, 5, 2, 4), dtype=np.float32),
        rng.random((1, 3, 28, 28, 2), dtype=np.float32),  # masks
        rng.random((1, 5, 4), dtype=np.float32),
        rng.random((1, 7, 2), dtype=np.float32),
        rng.random((1, 7, 4), dtype=np.float32),
    ]


def library_inputs() -> list[FakeTensor]:
    """What the pinned `detect` builds: three float32 tensors, images, metas and anchors."""
    return [
        FakeTensor(np.full((1, 8, 8, 3), 1.5, dtype=np.float32)),
        FakeTensor(np.full((1, 14), 2.0, dtype=np.float32)),
        FakeTensor(np.full((1, 6, 4), 3.0, dtype=np.float32)),
    ]


def pinned_detect(keras_model: Any, inputs: list[FakeTensor]) -> tuple[Any, Any]:
    """The lines of the pinned `MaskRCNN.detect` that touch the model, as they are there."""
    outputs = keras_model(inputs, training=False)
    return outputs[0].numpy(), outputs[3].numpy()


def test_the_librarys_direct_call_runs_predict_on_batch_and_gets_the_outputs_unchanged() -> None:
    fake = FakeKerasModel(seven_outputs())
    inputs = library_inputs()

    detections, masks = pinned_detect(_PredictOnBatch(fake), inputs)

    # numpy in (what the tensors hold, dtype and values as they were), once, in the order given
    (batch,) = fake.batches
    assert len(batch) == 3
    for sent, tensor in zip(batch, inputs, strict=True):
        assert type(sent) is np.ndarray
        assert sent.dtype == np.float32
        np.testing.assert_array_equal(sent, tensor.numpy())
    # outputs 0 and 3 out, bit for bit, as plain arrays
    for got, want in ((detections, fake.outputs[0]), (masks, fake.outputs[3])):
        assert type(got) is np.ndarray
        assert got.dtype == want.dtype
        np.testing.assert_array_equal(got, want)


def test_every_output_comes_back_in_order_and_unchanged() -> None:
    fake = FakeKerasModel(seven_outputs())

    outputs = _PredictOnBatch(fake)(library_inputs(), training=False)

    assert isinstance(outputs, list)
    assert len(outputs) == 7
    for got, want in zip(outputs, fake.outputs, strict=True):
        assert got.dtype == want.dtype
        np.testing.assert_array_equal(got.numpy(), want)
        np.testing.assert_array_equal(got, want)  # still an array to anything that wants one


def test_a_model_with_one_output_gives_one_array_not_a_list() -> None:
    fake = FakeKerasModel(np.arange(6, dtype=np.float32).reshape(2, 3))

    out = _PredictOnBatch(fake)(library_inputs())

    np.testing.assert_array_equal(out.numpy(), fake.outputs)


def test_other_ways_of_calling_the_model_are_the_models_own() -> None:
    fake = FakeKerasModel(seven_outputs(), direct_call_allowed=True)
    shim = _PredictOnBatch(fake)
    inputs = library_inputs()

    assert shim(inputs, training=True) == "direct"  # not an inference call
    assert shim(inputs, mask=None) == "direct"  # arguments detect() never passes
    assert shim(inputs[0]) == "direct"  # not the list of three inputs
    assert [(training, kwargs) for _, training, kwargs in fake.direct_calls] == [
        (True, {}),
        (False, {"mask": None}),
        (False, {}),
    ]
    assert fake.batches == []


def test_everything_else_is_delegated_to_the_model() -> None:
    fake = FakeKerasModel(seven_outputs())
    shim = _PredictOnBatch(fake)

    assert shim.layers is fake.layers  # what load_weights() reads
    assert not hasattr(shim, "inner_model")  # and asks for, as the model does
    with pytest.raises(AttributeError):
        shim.no_such_attribute  # noqa: B018
    # a library that is fixed upstream calls predict_on_batch itself: straight to the model
    sentinel = object()
    batch = [np.zeros(1)]
    fake.outputs = sentinel
    assert shim.predict_on_batch(batch) is sentinel
    assert fake.batches == [batch]


def test_the_shim_survives_a_copy() -> None:
    import copy

    fake = FakeKerasModel(seven_outputs())
    duplicate = copy.copy(_PredictOnBatch(fake))

    assert isinstance(duplicate, _PredictOnBatch)
    assert duplicate.layers is fake.layers


class FakeMaskRCNN:
    def __init__(self, keras_model: Any) -> None:
        self.keras_model = keras_model


def test_routing_is_idempotent() -> None:
    fake = FakeKerasModel(seven_outputs())
    model = FakeMaskRCNN(fake)

    _route_forward_pass_through_predict_on_batch(model)
    first = model.keras_model
    _route_forward_pass_through_predict_on_batch(model)

    assert isinstance(first, _PredictOnBatch)
    assert model.keras_model is first  # not wrapped again
    assert first._keras_model is fake
    detections, _ = pinned_detect(model.keras_model, library_inputs())
    np.testing.assert_array_equal(detections, fake.outputs[0])
    assert len(fake.batches) == 1


def test_load_patches_the_model_the_library_segments_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKerasModel(seven_outputs())
    model = FakeMaskRCNN(fake)
    segmented: list[Any] = []

    def segment_chemical_structures(image: Any, **kwargs: Any) -> Any:
        segmented.append(image)
        return [image], [(0, 0, 1, 1)]

    library = types.ModuleType("decimer_segmentation")
    library.get_model = lambda: model  # type: ignore[attr-defined]
    library.segment_chemical_structures = segment_chemical_structures  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "decimer_segmentation", library)

    segment = seg_model._real_segment_fn()
    again = seg_model._real_segment_fn()  # a second load must not wrap the wrapper

    assert isinstance(model.keras_model, _PredictOnBatch)
    assert model.keras_model._keras_model is fake
    page = np.zeros((4, 4, 3), dtype=np.uint8)
    for load in (segment, again):
        segments, bboxes = load(page)
        assert segments == [page]
        assert list(bboxes) == [(0, 0, 1, 1)]
    assert len(segmented) == 2
