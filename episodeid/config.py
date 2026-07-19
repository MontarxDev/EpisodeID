"""Application settings and secure API key storage."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from episodeid import __app_id__
from episodeid.renamer import DEFAULT_FORMAT

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None  # type: ignore


def config_dir() -> Path:
    return Path.home() / ".config" / __app_id__


def data_dir() -> Path:
    return Path.home() / ".local" / "share" / __app_id__


def settings_path() -> Path:
    return config_dir() / "settings.json"


def undo_dir() -> Path:
    return data_dir() / "undo"


def _load_durable_pref() -> bool:
    """Read durable_cache without full Settings load (avoid cycles)."""
    path = settings_path()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "durable_cache" in data:
            return bool(data["durable_cache"])
    except (OSError, json.JSONDecodeError):
        pass
    return True


def cache_dir() -> Path:
    """Active cache root (durable share path by default — survives reboot and ~/.cache cleans)."""
    from episodeid.cache_manager import cache_root, ensure_cache_layout

    durable = _load_durable_pref()
    ensure_cache_layout(durable)
    return cache_root(durable)


def tmdb_cache_dir() -> Path:
    return cache_dir() / "tmdb"


KEYRING_SERVICE = __app_id__
KEY_TMDB = "tmdb_api_key"
KEY_GEMINI = "gemini_api_key"
KEY_OPENAI = "openai_api_key"
KEY_GROK = "grok_api_key"
KEY_WYZIE = "wyzie_api_key"


@dataclass
class Settings:
    rename_format: str = DEFAULT_FORMAT
    move_to_season: bool = True
    low_threshold: float = 55.0
    auto_threshold: float = 70.0
    offset_minutes: float = 1.0
    scan_duration_minutes: float = 10.0
    max_lines: int = 40
    size_filter_enabled: bool = True
    size_filter_ratio: float = 0.25
    skip_already_named: bool = False
    theme: str = "light"  # light | dark | system — light default for readable tables
    llm_enabled: bool = False
    llm_provider: str = "ollama"  # ollama | gemini | openai | grok
    llm_model: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_only_when_low: bool = True
    last_folder: str = ""
    last_output_folder: str = ""
    # Empty output_folder means "same as scan folder"
    output_folder: str = ""
    output_same_as_scan: bool = True
    output_create_series_subfolder: bool = True
    last_series_id: int | None = None
    last_series_name: str = ""
    # None or 0 = all seasons; otherwise only match episodes in this season
    season_filter: int | None = None
    # Free accuracy boosters
    use_tvmaze: bool = True
    use_reference_subs: bool = True  # needs free Wyzie key for downloads; uses cache if present
    save_refsubs_to_cache: bool = True
    # download-missing | cache-only | force-refresh
    refsubs_network_policy: str = "download-missing"
    # Library scan
    recursive_scan: bool = True
    skip_sample_folders: bool = True
    auto_resolve_problems: bool = True
    # Cache location: durable = ~/.local/share/episodeid/cache (recommended)
    durable_cache: bool = True
    rename_in_place: bool = False  # False = Season folders under scan root
    # Multi-episode mega files
    detect_multi_episode: bool = True
    skip_split_if_episode_present: bool = True
    skip_split_if_in_output_library: bool = True
    force_splits_even_if_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return Settings()
        return Settings.from_dict(data)
    except (OSError, json.JSONDecodeError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")


def set_secret(name: str, value: str | None) -> None:
    if keyring is None:
        # Fallback file (less secure) under config with restrictive note
        secrets = _fallback_secrets_path()
        data = {}
        if secrets.exists():
            try:
                data = json.loads(secrets.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        if value:
            data[name] = value
        else:
            data.pop(name, None)
        secrets.parent.mkdir(parents=True, exist_ok=True)
        secrets.write_text(json.dumps(data), encoding="utf-8")
        try:
            secrets.chmod(0o600)
        except OSError:
            pass
        return
    if value:
        keyring.set_password(KEYRING_SERVICE, name, value)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass


def get_secret(name: str) -> str | None:
    if keyring is None:
        secrets = _fallback_secrets_path()
        if not secrets.exists():
            return None
        try:
            data = json.loads(secrets.read_text(encoding="utf-8"))
            val = data.get(name)
            return str(val) if val else None
        except (OSError, json.JSONDecodeError):
            return None
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def _fallback_secrets_path() -> Path:
    return config_dir() / ".secrets.json"


def get_tmdb_api_key() -> str | None:
    return get_secret(KEY_TMDB)


def set_tmdb_api_key(value: str | None) -> None:
    set_secret(KEY_TMDB, value)
