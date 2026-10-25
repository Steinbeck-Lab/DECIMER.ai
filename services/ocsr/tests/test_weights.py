"""The weights check: DECIMER 2.8.1 downloads silently when it loads models, so we look first."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from decimer_ocsr import model as ocsr_model
from decimer_ocsr.model import OcsrModel

OCSR_ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "saved_model.pb",
    "variables/variables.index",
    "variables/variables.data-00000-of-00001",
)
TOKENIZER = "assets/tokenizer_SMILES.pkl"


def store(home: Path) -> Path:
    return home / "DECIMER-V2"


def put_weights(home: Path, *, hand_drawn: bool = False) -> None:
    """A weights tree with every file DECIMER 2.8.1 reads for printed structures (contents are
    irrelevant), and, if asked for, the hand-drawn model's files that the image also carries."""
    directories = ["DECIMER_model", *(["DECIMER_HandDrawn_model"] if hand_drawn else [])]
    for directory in directories:
        for name in [*FILES, *([TOKENIZER] if directory == "DECIMER_model" else [])]:
            path = store(home) / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")


@pytest.fixture
def real_loader(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replaces the function that imports DECIMER, so these tests never touch TensorFlow."""
    calls: list[str] = []

    def fake_loader() -> ocsr_model.PredictFn:
        calls.append("imported")
        return lambda array: ("", [])

    monkeypatch.setattr(ocsr_model, "_real_predict_fn", fake_loader)
    monkeypatch.delenv("OCSR_ALLOW_WEIGHT_DOWNLOAD", raising=False)
    return calls


def test_the_url_is_the_one_decimer_2_8_1_checks_its_marker_file_against() -> None:
    assert ocsr_model.MODEL_URLS == {
        "DECIMER_model": "https://zenodo.org/record/8300489/files/models.zip",
    }


def test_missing_weights_raise_a_clear_error_before_the_library_is_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))

    with pytest.raises(RuntimeError) as excinfo:
        OcsrModel().load()

    message = str(excinfo.value)
    assert str(store(tmp_path) / "DECIMER_model" / "saved_model.pb") in message
    assert "OCSR_ALLOW_WEIGHT_DOWNLOAD" in message
    assert "PYSTOW_HOME" in message
    assert real_loader == []
    assert not store(tmp_path).exists()  # checking must not create anything


def test_every_file_the_library_needs_is_checked_and_named_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    needed = [f"DECIMER_model/{name}" for name in (*FILES, TOKENIZER)]
    # The checker's own list is pinned too: these four are what `load_models(printed=True)` reads.
    assert set(needed) == {
        f"{directory}/{name}"
        for directory, names in ocsr_model.REQUIRED_FILES.items()
        for name in names
    }
    assert len(needed) == 4

    for missing in needed:
        put_weights(tmp_path)
        (store(tmp_path) / missing).unlink()
        with pytest.raises(RuntimeError) as excinfo:
            OcsrModel().load()
        assert str(store(tmp_path) / missing) in str(excinfo.value), missing
    assert real_loader == []


def test_the_hand_drawn_weights_are_not_needed_because_the_library_never_loads_them_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    # decimer 2.8.0 loaded both models on import and would download a missing one. 2.8.1 touches
    # the hand-drawn directory only for `hand_drawn=True`, which nothing here asks for, so a tree
    # without it (or with a stale marker in it, or with it incomplete) starts the service.
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    put_weights(tmp_path)
    OcsrModel().load()

    put_weights(tmp_path, hand_drawn=True)
    (store(tmp_path) / "DECIMER_HandDrawn_model" / "saved_model.pb").unlink()
    (store(tmp_path) / "DECIMER_HandDrawn_model" / ".model_url").write_text("https://elsewhere/x")
    OcsrModel().load()

    assert real_loader == ["imported", "imported"]


def test_complete_weights_let_the_library_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    put_weights(tmp_path, hand_drawn=True)  # as in the image

    OcsrModel().load()

    assert real_loader == ["imported"]


def test_a_marker_file_with_the_expected_url_is_fine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    put_weights(tmp_path)
    for directory, url in ocsr_model.MODEL_URLS.items():
        (store(tmp_path) / directory / ".model_url").write_text(url)

    OcsrModel().load()

    assert real_loader == ["imported"]


@pytest.mark.parametrize("directory", list(ocsr_model.MODEL_URLS))
def test_a_marker_file_with_another_url_would_make_the_library_download_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str], directory: str
) -> None:
    # DECIMER 2.8.1 deletes the model directory and downloads again when `.model_url` differs, so
    # a stale marker must stop the start-up.
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    put_weights(tmp_path)
    marker = store(tmp_path) / directory / ".model_url"
    marker.write_text("https://zenodo.org/record/1/files/models.zip")

    with pytest.raises(RuntimeError) as excinfo:
        OcsrModel().load()

    assert str(marker) in str(excinfo.value)
    assert "download" in str(excinfo.value)
    assert real_loader == []
    for name in (*FILES, TOKENIZER):
        assert (store(tmp_path) / directory / name).exists()  # nothing deleted


def test_every_model_directory_is_covered_by_the_marker_check() -> None:
    # The parametrised test above follows MODEL_URLS; pin what it follows.
    assert list(ocsr_model.MODEL_URLS) == ["DECIMER_model"]
    assert list(ocsr_model.REQUIRED_FILES) == list(ocsr_model.MODEL_URLS)


def test_weight_download_has_to_be_asked_for_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    real_loader: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    for not_a_yes in ("0", "true", "yes", ""):
        monkeypatch.setenv("OCSR_ALLOW_WEIGHT_DOWNLOAD", not_a_yes)
        with pytest.raises(RuntimeError, match="saved_model.pb"):
            OcsrModel().load()
    assert real_loader == []

    monkeypatch.setenv("OCSR_ALLOW_WEIGHT_DOWNLOAD", "1")
    with caplog.at_level(logging.WARNING, logger="decimer_ocsr"):
        OcsrModel().load()
    assert real_loader == ["imported"]
    assert any("download" in r.getMessage() for r in caplog.records)


def test_an_injected_predictor_skips_the_check_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))  # no weights here
    OcsrModel(lambda array: ("", [])).load()
    assert real_loader == []


def run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=OCSR_ROOT,
    )


def test_missing_weights_never_import_tensorflow_or_the_library(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "OCSR_ALLOW_WEIGHT_DOWNLOAD"}
    env["PYSTOW_HOME"] = str(tmp_path)
    done = run_python(
        "import sys\n"
        "from decimer_ocsr.model import OcsrModel\n"
        "try:\n"
        "    OcsrModel().load()\n"
        "except RuntimeError as exc:\n"
        "    print(exc)\n"
        "else:\n"
        "    sys.exit('load() did not raise')\n"
        "bad = [m for m in ('tensorflow', 'DECIMER') if m in sys.modules]\n"
        "assert not bad, bad\n",
        env,
    )
    assert done.returncode == 0, done.stderr
    assert str(store(tmp_path) / "DECIMER_model" / "saved_model.pb") in done.stdout


def test_whatever_the_library_prints_while_loading_stays_off_stdout(tmp_path: Path) -> None:
    # DECIMER prints "Downloading trained model to ..." and tqdm bars while it loads its models.
    # The stand-in also records how it was asked to load: the printed model, and only that.
    (tmp_path / "lib" / "DECIMER").mkdir(parents=True)
    (tmp_path / "lib" / "DECIMER" / "__init__.py").write_text(
        "loads = []\n"
        "def load_models(**kwargs):\n"
        "    print('Downloading trained model to /some/dir')\n"
        "    loads.append(kwargs)\n"
        "def predict_SMILES(image, confidence=False, hand_drawn=False):\n"
        "    return '', []\n"
    )
    put_weights(tmp_path / "home")
    env = {k: v for k, v in os.environ.items() if k != "OCSR_ALLOW_WEIGHT_DOWNLOAD"}
    env["PYSTOW_HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = str(tmp_path / "lib")

    done = run_python(
        "import sys\n"
        "from decimer_ocsr.model import OcsrModel\n"
        "OcsrModel().load()\n"
        "assert sys.modules['DECIMER'].loads == [{'printed': True, 'hand_drawn': False}]\n",
        env,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
