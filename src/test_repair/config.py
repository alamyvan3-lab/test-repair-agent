"""Environment-driven settings. Every knob has an env var; secrets come only from env."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"


class ConfigError(RuntimeError):
    """Missing or invalid configuration."""


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = _str(name)
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _opt_float(name: str) -> float | None:
    raw = _str(name)
    return _float(name, 0.0) if raw else None


def _floats(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = _str(name)
    if not raw:
        return default
    try:
        return tuple(float(x) for x in raw.split(",") if x.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be comma-separated numbers, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _json(name: str) -> dict[str, Any]:
    raw = _str(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


@dataclass(frozen=True)
class RoleModel:
    """Model settings for one role ("ultra" or "nano")."""

    model: str
    base_url: str
    temperature: float
    max_tokens: int
    extra_body: dict[str, Any]
    price_in_per_m: float | None  # USD per 1M prompt tokens, for cost reporting only
    price_out_per_m: float | None


@dataclass(frozen=True)
class Settings:
    """Agent settings. Build with `Settings.from_env()`."""

    api_key: str
    ultra: RoleModel
    nano: RoleModel
    max_attempts: int = 5
    max_corrections: int = 2
    max_total_tokens: int = 250_000
    max_run_seconds: int = 900
    llm_timeout_s: float = 180.0
    llm_max_retries: int = 3
    max_diff_lines: int = 60
    top_k_files: int = 4
    max_file_chars: int = 12_000
    max_triage_chars: int = 12_000
    sanity_check: bool = True
    sandbox_backend: str = "docker"
    sandbox_image: str = "test-repair-sandbox:latest"
    test_timeout_s: int = 60
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1"
    sandbox_pids: int = 128
    max_output_bytes: int = 64_000
    # Techniques (see README "Techniques and sources"); each can be switched off for ablations.
    candidates: int = 3
    candidate_temperatures: tuple[float, ...] = (0.0, 0.1, 0.2)
    sbfl: bool = True
    isolation_rerun: bool = True
    minimize_patch: bool = True
    minimize_max_runs: int = 8
    nano_json_mode: bool = True

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        """Read settings from the environment (after loading `env_file` if it exists)."""
        if env_file and Path(env_file).is_file():
            load_dotenv(env_file, override=False)
        base_url = _str("TOKEN_FACTORY_BASE_URL", DEFAULT_BASE_URL)
        backend = _str("SANDBOX_BACKEND", "docker").lower()
        if backend not in {"docker", "local", "auto"}:
            raise ConfigError("SANDBOX_BACKEND must be docker, local, or auto")
        return cls(
            api_key=_str("TOKEN_FACTORY_API_KEY"),
            ultra=RoleModel(
                model=_str("MODEL_ULTRA"),
                base_url=_str("ULTRA_BASE_URL") or base_url,
                temperature=_float("ULTRA_TEMPERATURE", 0.1),
                max_tokens=_int("ULTRA_MAX_TOKENS", 8192),
                extra_body=_json("ULTRA_EXTRA_BODY"),
                price_in_per_m=_opt_float("PRICE_ULTRA_IN_PER_M"),
                price_out_per_m=_opt_float("PRICE_ULTRA_OUT_PER_M"),
            ),
            nano=RoleModel(
                model=_str("MODEL_NANO"),
                base_url=_str("NANO_BASE_URL") or base_url,
                temperature=_float("NANO_TEMPERATURE", 0.0),
                max_tokens=_int("NANO_MAX_TOKENS", 2048),
                extra_body=_json("NANO_EXTRA_BODY"),
                price_in_per_m=_opt_float("PRICE_NANO_IN_PER_M"),
                price_out_per_m=_opt_float("PRICE_NANO_OUT_PER_M"),
            ),
            max_attempts=_int("MAX_ATTEMPTS", 5),
            max_corrections=_int("MAX_CORRECTIONS", 2),
            max_total_tokens=_int("MAX_TOTAL_TOKENS", 250_000),
            max_run_seconds=_int("MAX_RUN_SECONDS", 900),
            llm_timeout_s=_float("LLM_TIMEOUT_S", 180.0),
            llm_max_retries=_int("LLM_MAX_RETRIES", 3),
            max_diff_lines=_int("MAX_DIFF_LINES", 60),
            top_k_files=_int("TOP_K_FILES", 4),
            max_file_chars=_int("MAX_FILE_CHARS", 12_000),
            max_triage_chars=_int("MAX_TRIAGE_CHARS", 12_000),
            sanity_check=_bool("SANITY_CHECK", True),
            sandbox_backend=backend,
            sandbox_image=_str("SANDBOX_IMAGE", "test-repair-sandbox:latest"),
            test_timeout_s=_int("TEST_TIMEOUT_S", 60),
            sandbox_memory=_str("SANDBOX_MEMORY", "512m"),
            sandbox_cpus=_str("SANDBOX_CPUS", "1"),
            sandbox_pids=_int("SANDBOX_PIDS", 128),
            max_output_bytes=_int("MAX_OUTPUT_BYTES", 64_000),
            candidates=max(1, _int("CANDIDATES", 3)),
            candidate_temperatures=_floats("CANDIDATE_TEMPERATURES", (0.0, 0.1, 0.2)),
            sbfl=_bool("SBFL", True),
            isolation_rerun=_bool("ISOLATION_RERUN", True),
            minimize_patch=_bool("MINIMIZE_PATCH", True),
            minimize_max_runs=_int("MINIMIZE_MAX_RUNS", 8),
            nano_json_mode=_bool("NANO_JSON_MODE", True),
        )

    def candidate_temperature(self, index: int, count: int) -> float:
        """Ultra temperature for candidate `index` of `count`; one candidate uses ULTRA_TEMPERATURE."""
        if count == 1 or not self.candidate_temperatures:
            return self.ultra.temperature
        return self.candidate_temperatures[index % len(self.candidate_temperatures)]

    def require_llm(self) -> None:
        """Fail fast with an actionable message if the model config is incomplete."""
        missing = [
            name
            for name, value in (
                ("TOKEN_FACTORY_API_KEY", self.api_key),
                ("MODEL_ULTRA", self.ultra.model),
                ("MODEL_NANO", self.nano.model),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in; "
                "run `python scripts/hello_tokenfactory.py` to list available model IDs."
            )


@dataclass(frozen=True)
class ServerSettings:
    """Web demo limits."""

    allow_uploads: bool = False
    max_upload_bytes: int = 1_000_000
    max_upload_files: int = 200
    max_uncompressed_bytes: int = 5_000_000
    rate_limit_runs: int = 5
    rate_limit_window_s: int = 600
    max_concurrent_runs: int = 2
    runs_dir: str = "runs"
    max_kept_runs: int = 50
    trust_proxy_headers: bool = False

    @classmethod
    def from_env(cls) -> ServerSettings:
        return cls(
            allow_uploads=_bool("ALLOW_UPLOADS", False),
            max_upload_bytes=_int("MAX_UPLOAD_BYTES", 1_000_000),
            max_upload_files=_int("MAX_UPLOAD_FILES", 200),
            max_uncompressed_bytes=_int("MAX_UNCOMPRESSED_BYTES", 5_000_000),
            rate_limit_runs=_int("RATE_LIMIT_RUNS", 5),
            rate_limit_window_s=_int("RATE_LIMIT_WINDOW_S", 600),
            max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 2),
            runs_dir=_str("RUNS_DIR", "runs"),
            max_kept_runs=_int("MAX_KEPT_RUNS", 50),
            trust_proxy_headers=_bool("TRUST_PROXY_HEADERS", False),
        )
