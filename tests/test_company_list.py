"""The shipped starter list must stay valid, large enough, and free of mistakes."""

from pathlib import Path

import yaml

from job_search_mcp.config import BOARD_TOKEN, CompaniesConfig

EXAMPLE = Path(__file__).resolve().parent.parent / "companies.example.yaml"


def load():
    return CompaniesConfig.model_validate(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))


def test_the_starter_list_is_valid_and_has_hundreds_of_companies():
    config = load()
    total = sum(len(v) for v in config.model_dump().values())
    assert total >= 300
    assert len(config.greenhouse) > 100 and len(config.ashby) > 10 and len(config.lever) > 10


def test_every_name_is_a_safe_board_name():
    for system, names in load().model_dump().items():
        if system != "workday":  # Workday entries have their own format, tested below
            assert all(BOARD_TOKEN.fullmatch(name) for name in names)


def test_no_company_is_listed_twice_or_under_two_systems():
    seen: dict[str, str] = {}
    for system, names in load().model_dump().items():
        assert len(names) == len(set(names)), f"duplicates inside {system}"
        for name in names:
            assert name not in seen, f"{name} is under both {seen[name]} and {system}"
            seen[name] = system


def test_the_companies_from_the_original_sample_are_still_there():
    everyone = {n for names in load().model_dump().values() for n in names}
    for name in (
        "airbnb",
        "stripe",
        "databricks",
        "figma",
        "reddit",
        "notion",
        "ramp",
        "openai",
        "linear",
    ):
        assert name in everyone


def test_smartrecruiters_companies_are_present_and_unique():
    config = load()
    assert {"servicenow", "canva"} <= set(config.smartrecruiters)


def test_workday_entries_are_valid_unique_and_include_the_big_names():
    entries = load().workday
    assert len(entries) >= 15 and len(entries) == len(set(entries))
    assert "nvidia.wd5.NVIDIAExternalCareerSite" in entries
    assert all(e.count(".") == 2 for e in entries)  # tenant.wdN.Site
