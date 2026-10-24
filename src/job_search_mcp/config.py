"""Settings: environment variables, an optional .env file, and companies.yaml.

Nothing here is hard-coded to one person. Where files live is controlled by
JOB_SEARCH_HOME (default: the folder the server was started in).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# A company "board token" ends up inside a URL path, so it must be boring:
# letters, digits, "-" and "_" only. This blocks "../" and similar tricks.
BOARD_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_COMPANIES_PER_SOURCE = 1000
_ENV_PREFIXES = ("ADZUNA_", "MUSE_", "JOB_SEARCH_")
_PLACEHOLDER_HINT = "_here"


class ConfigError(Exception):
    """The user's configuration is invalid (message is safe to show)."""


class CompaniesConfig(BaseModel):
    """Contents of companies.yaml: which company boards to search."""

    model_config = ConfigDict(extra="forbid")

    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    lever_eu: list[str] = Field(default_factory=list)  # companies on Lever's EU site
    ashby: list[str] = Field(default_factory=list)

    @field_validator("greenhouse", "lever", "lever_eu", "ashby")
    @classmethod
    def _check_tokens(cls, tokens: list[str]) -> list[str]:
        if len(tokens) > MAX_COMPANIES_PER_SOURCE:
            raise ValueError(f"at most {MAX_COMPANIES_PER_SOURCE} companies per source")
        seen: dict[str, None] = {}
        for token in tokens:
            if not isinstance(token, str) or not BOARD_TOKEN.fullmatch(token):
                raise ValueError(
                    f"invalid company board name {str(token)[:40]!r}: "
                    "use letters, digits, '-' or '_' only"
                )
            seen[token.lower()] = None
        return list(seen)


class Settings(BaseModel):
    home: Path
    companies_file: Path
    max_age_days: int = Field(default=30, ge=1, le=365)
    max_response_bytes: int = Field(default=15 * 1024 * 1024, ge=1024)
    requests_per_second: float = Field(default=5.0, ge=0.5, le=10)
    adzuna_app_id: SecretStr | None = None
    adzuna_app_key: SecretStr | None = None
    muse_api_key: SecretStr | None = None
    companies: CompaniesConfig = Field(default_factory=CompaniesConfig)
    companies_note: str = ""


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader. Real environment variables always win.

    Only ADZUNA_*, MUSE_* and JOB_SEARCH_* names are accepted, so a stray .env can't
    change unrelated settings such as PATH.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key.startswith(_ENV_PREFIXES) and key not in os.environ:
            os.environ[key] = value


def _secret(name: str) -> SecretStr | None:
    value = os.environ.get(name, "").strip()
    if not value or value.endswith(_PLACEHOLDER_HINT):
        return None  # unset, or still the .env.example placeholder
    return SecretStr(value)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number") from None


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None


def _load_companies(path: Path, fallback: Path) -> tuple[CompaniesConfig, str]:
    note = ""
    if not path.is_file():
        if fallback.is_file():
            note = f"{path.name} not found; using the example list {fallback.name}"
            path = fallback
        else:
            return CompaniesConfig(), f"no companies file found at {path}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        raise ConfigError(f"could not read {path.name}: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a mapping of source: [companies]")
    try:
        return CompaniesConfig.model_validate(data), note
    except ValueError as exc:
        raise ConfigError(f"invalid {path.name}: {exc}") from None


def load_settings() -> Settings:
    home = Path(os.environ.get("JOB_SEARCH_HOME") or Path.cwd()).resolve()
    _load_dotenv(home / ".env")

    companies_path = Path(
        os.environ.get("JOB_SEARCH_COMPANIES_FILE") or home / "companies.yaml"
    ).expanduser()
    companies, note = _load_companies(companies_path, home / "companies.example.yaml")

    rate = _float_env("JOB_SEARCH_REQUESTS_PER_SECOND", 5.0)
    if not 0.5 <= rate <= 10:
        raise ConfigError("JOB_SEARCH_REQUESTS_PER_SECOND must be between 0.5 and 10")
    max_mb = _int_env("JOB_SEARCH_MAX_RESPONSE_MB", 15)
    if not 1 <= max_mb <= 50:
        raise ConfigError("JOB_SEARCH_MAX_RESPONSE_MB must be between 1 and 50")
    try:
        return Settings(
            home=home,
            companies_file=companies_path,
            max_age_days=_int_env("JOB_SEARCH_MAX_AGE_DAYS", 30),
            max_response_bytes=max_mb * 1024 * 1024,
            requests_per_second=rate,
            adzuna_app_id=_secret("ADZUNA_APP_ID"),
            adzuna_app_key=_secret("ADZUNA_APP_KEY"),
            muse_api_key=_secret("MUSE_API_KEY"),
            companies=companies,
            companies_note=note,
        )
    except ValueError as exc:
        raise ConfigError(f"invalid settings: {exc}") from None
