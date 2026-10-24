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
    for names in load().model_dump().values():
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
