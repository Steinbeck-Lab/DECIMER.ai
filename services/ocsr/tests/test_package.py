import os
import subprocess
import sys

import decimer_ocsr


def test_package_imports() -> None:
    assert decimer_ocsr.__version__ == "0.1.0"


def _run(code: str, **env: str) -> None:
    clean = {k: v for k, v in os.environ.items() if k != "TF_CPP_MIN_LOG_LEVEL"}
    subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=60, env={**clean, **env})


def test_importing_the_package_quiets_tensorflow_info_and_warning_lines() -> None:
    # TensorFlow reads TF_CPP_MIN_LOG_LEVEL when it is imported, which `load()` does later, so the
    # package sets it first. 2 hides INFO and WARNING but leaves ERROR visible.
    _run("import os, decimer_ocsr; assert os.environ['TF_CPP_MIN_LOG_LEVEL'] == '2'")


def test_a_log_level_set_by_the_operator_wins() -> None:
    _run(
        "import os, decimer_ocsr; assert os.environ['TF_CPP_MIN_LOG_LEVEL'] == '0'",
        TF_CPP_MIN_LOG_LEVEL="0",
    )
