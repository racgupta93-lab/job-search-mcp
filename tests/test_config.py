import os

import pytest

from job_search_mcp.config import ConfigError, load_settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("ADZUNA_", "JOB_SEARCH_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("JOB_SEARCH_HOME", str(tmp_path))
    return tmp_path


def test_defaults_with_no_files(home):
    s = load_settings()
    assert s.companies.greenhouse == []
    assert s.max_age_days == 30
    assert s.adzuna_app_id is None


def test_loads_and_dedupes_companies(home):
    (home / "companies.yaml").write_text("greenhouse: [Airbnb, airbnb, stripe]\n")
    assert load_settings().companies.greenhouse == ["airbnb", "stripe"]


def test_falls_back_to_example_file_with_note(home):
    (home / "companies.example.yaml").write_text("greenhouse: [stripe]\n")
    s = load_settings()
    assert s.companies.greenhouse == ["stripe"]
    assert "example" in s.companies_note


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "a b", "a?x=1", "-lead", "x" * 65, "a%2f"])
def test_rejects_unsafe_board_names(home, bad):
    (home / "companies.yaml").write_text(f"greenhouse: ['{bad}']\n")
    with pytest.raises(ConfigError):
        load_settings()


def test_rejects_unknown_keys_and_bad_shape(home):
    (home / "companies.yaml").write_text("workday: [x]\n")
    with pytest.raises(ConfigError):
        load_settings()
    (home / "companies.yaml").write_text("- just\n- a list\n")
    with pytest.raises(ConfigError):
        load_settings()


def test_yaml_is_loaded_safely(home):
    (home / "companies.yaml").write_text("greenhouse: !!python/object/apply:os.system ['id']\n")
    with pytest.raises(ConfigError):
        load_settings()


def test_dotenv_loaded_but_real_env_wins(home, monkeypatch):
    (home / ".env").write_text("ADZUNA_APP_ID=fromfile\nADZUNA_APP_KEY='quoted'\nPATH=/evil\n")
    monkeypatch.setenv("ADZUNA_APP_KEY", "fromenv")
    s = load_settings()
    assert s.adzuna_app_id.get_secret_value() == "fromfile"
    assert s.adzuna_app_key.get_secret_value() == "fromenv"
    assert os.environ["PATH"] != "/evil"  # only ADZUNA_/JOB_SEARCH_ names accepted


def test_placeholder_values_treated_as_unset(home):
    (home / ".env").write_text("ADZUNA_APP_ID=your_adzuna_app_id_here\n")
    assert load_settings().adzuna_app_id is None


def test_secrets_do_not_appear_in_repr(home):
    (home / ".env").write_text("ADZUNA_APP_KEY=supersecretvalue\n")
    s = load_settings()
    assert "supersecretvalue" not in repr(s)
    assert "supersecretvalue" not in s.model_dump_json()


def test_bad_numeric_env_is_clean_error(home, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_MAX_AGE_DAYS", "abc")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("JOB_SEARCH_MAX_AGE_DAYS", "9999")
    with pytest.raises(ConfigError):
        load_settings()
