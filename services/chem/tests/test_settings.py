from pathlib import Path

import pytest

from decimer_chem.settings import Settings


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.jars_dir is None  # Orthonym's own jar directory
    assert s.allow_jar_download is False
    assert s.name_timeout_s == 10.0
    assert s.batch_timeout_s == 60.0


def test_environment_overrides() -> None:
    s = Settings.from_env(
        {
            "CHEM_JARS_DIR": "/opt/chem/jars",
            "CHEM_ALLOW_JAR_DOWNLOAD": "1",
            "CHEM_NAME_TIMEOUT_S": "2.5",
            "CHEM_BATCH_TIMEOUT_S": "30",
        }
    )
    assert s.jars_dir == Path("/opt/chem/jars")
    assert s.allow_jar_download is True
    assert (s.name_timeout_s, s.batch_timeout_s) == (2.5, 30.0)


@pytest.mark.parametrize("value", ["true", "yes", "on", "0", "", "2", " 1"])
def test_only_exactly_one_opts_in_to_downloads(value: str) -> None:
    assert Settings.from_env({"CHEM_ALLOW_JAR_DOWNLOAD": value}).allow_jar_download is False


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan", "inf", ""])
def test_a_bad_timeout_names_the_variable(value: str) -> None:
    with pytest.raises(ValueError, match="CHEM_NAME_TIMEOUT_S"):
        Settings.from_env({"CHEM_NAME_TIMEOUT_S": value})


def test_an_empty_jars_dir_means_unset() -> None:
    assert Settings.from_env({"CHEM_JARS_DIR": ""}).jars_dir is None


def test_a_relative_jars_dir_becomes_absolute() -> None:
    # the naming process runs in a scratch directory, so a relative path would point elsewhere
    jars = Settings.from_env({"CHEM_JARS_DIR": "jars"}).jars_dir
    assert jars is not None and jars.is_absolute() and jars.name == "jars"
