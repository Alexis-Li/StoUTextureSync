"""Load the settings for the Painter sync loop."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .naming import NamingError, TextureSetNaming

DEFAULT_DEBOUNCE_S = 1.0
DEFAULT_THROTTLE_MS = 500
MIN_THROTTLE_MS = 500
DEFAULT_SIZE_LOG2 = 10
MAX_SIZE_LOG2 = 11
DEFAULT_PUBLISH_RETRIES = 5
DEFAULT_PUBLISH_RETRY_DELAY_S = 0.2
DEFAULT_STAGE_DIR = str(Path(tempfile.gettempdir()) / "stou_texture_sync_stage")


class ConfigError(ValueError):
    pass


@dataclass
class TextureSetConfig:
    texture_set: str
    mode: str = "keep"
    strip_tile_suffix: bool = True
    character_prefix: str = ""
    tile_parts: dict[int, str] = field(default_factory=dict)
    size_log2: int | None = None
    tile_size_log2: dict[int, int] = field(default_factory=dict)
    enabled: bool = True

    def to_naming(self) -> TextureSetNaming:
        naming = TextureSetNaming(self.texture_set, self.mode, self.strip_tile_suffix,
                                  self.character_prefix, self.tile_parts.copy())
        naming.validate()
        return naming


@dataclass
class SyncConfig:
    preset: str
    target_dir: str
    preset_url: str | None = None
    stage_dir: str = DEFAULT_STAGE_DIR
    log_path: str = ""
    sample_name: str = ""
    debounce_s: float = DEFAULT_DEBOUNCE_S
    throttle_ms: int | None = DEFAULT_THROTTLE_MS
    size_log2: int = DEFAULT_SIZE_LOG2
    enabled: bool = False
    publish_retries: int = DEFAULT_PUBLISH_RETRIES
    publish_retry_delay_s: float = DEFAULT_PUBLISH_RETRY_DELAY_S
    texture_sets: dict[str, TextureSetConfig] = field(default_factory=dict)

    def __post_init__(self):
        if not self.log_path:
            self.log_path = str(Path(self.stage_dir) / "sync_log.jsonl")


def _object(value, field_name: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a JSON object")
    return value


def _string(value, field_name: str, *, empty=False) -> str:
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        raise ConfigError(f"{field_name} must be a valid string")
    return value


def _boolean(value, field_name: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{field_name} must be a boolean")
    return value


def _integer(value, field_name: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{field_name} must be an integer {bounds}")
    return value


def _seconds(value, field_name: str) -> float:
    if type(value) not in (int, float) or not 0 <= value <= sys.float_info.max:
        raise ConfigError(f"{field_name} must be a finite number >= 0")
    return float(value)


def _size(value, field_name="size_log2") -> int:
    return _integer(value, field_name, 1, MAX_SIZE_LOG2)


def _tile_map(value, field_name: str, validate) -> dict:
    result = {}
    for key, item in _object(value, field_name).items():
        if isinstance(key, str) and len(key) == 4 and key.isascii() and key.isdecimal():
            key = int(key)
        udim = _integer(key, f"{field_name} UDIM", 1001, 1999)
        if udim in result:
            raise ConfigError(f"{field_name} repeats UDIM {udim}")
        result[udim] = validate(item, f"{field_name}[{udim}]")
    return result


def _path(value, field_name: str) -> Path:
    try:
        return Path(_string(value, field_name)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigError(f"Cannot resolve {field_name}: {exc}") from exc


def parse_config(data: dict) -> SyncConfig:
    data = _object(data, "config")
    preset = _string(data.get("preset"), "preset")
    preset_url = data.get("preset_url")
    if preset_url is not None:
        preset_url = _string(preset_url, "preset_url")
    target = _path(data.get("target_dir"), "target_dir")
    stage = _path(data.get("stage_dir", DEFAULT_STAGE_DIR), "stage_dir")
    if stage.is_relative_to(target) or target.is_relative_to(stage):
        raise ConfigError("stage_dir and target_dir must be separate directory trees")
    log = _path(data.get("log_path", data.get("log", str(stage / "sync_log.jsonl"))), "log_path")
    if log == stage or log.is_relative_to(target):
        raise ConfigError("log_path must be a file outside target_dir")
    throttle = data.get("throttle_ms", DEFAULT_THROTTLE_MS)
    if throttle is not None:
        throttle = _integer(throttle, "throttle_ms", MIN_THROTTLE_MS)
    legacy = {name: _size(value, f"texture_set_size_log2[{name}]") for name, value in
              _object(data.get("texture_set_size_log2", {}), "texture_set_size_log2").items()}
    texture_sets = {}
    for name, raw in _object(data.get("texture_sets", {}), "texture_sets").items():
        _string(name, "Texture Set name")
        spec = _object(raw, f"texture_sets[{name}]")
        size = spec.get("size_log2", legacy.get(name))
        binding = TextureSetConfig(
            name,
            mode=_string(spec.get("mode", "keep"), "mode"),
            strip_tile_suffix=_boolean(spec.get("strip_tile_suffix", True), "strip_tile_suffix"),
            character_prefix=_string(spec.get("character_prefix", ""), "character_prefix", empty=True),
            tile_parts=_tile_map(spec.get("tile_parts", {}), "tile_parts", _string),
            size_log2=_size(size) if size is not None else None,
            tile_size_log2=_tile_map(spec.get("tile_size_log2", {}), "tile_size_log2", _size),
            enabled=_boolean(spec.get("enabled", True), "Texture Set enabled"),
        )
        try:
            binding.to_naming()
        except NamingError as exc:
            raise ConfigError(str(exc)) from exc
        texture_sets[name] = binding
    return SyncConfig(
        preset, str(target), preset_url=preset_url, stage_dir=str(stage), log_path=str(log),
        sample_name=_string(data.get("sample_name", ""), "sample_name", empty=True),
        debounce_s=_seconds(data.get("debounce_s", DEFAULT_DEBOUNCE_S), "debounce_s"),
        throttle_ms=throttle,
        size_log2=_size(data.get("size_log2", DEFAULT_SIZE_LOG2)),
        enabled=_boolean(data.get("enabled", False), "enabled"),
        publish_retries=_integer(data.get("publish_retries", DEFAULT_PUBLISH_RETRIES),
                                 "publish_retries", 1),
        publish_retry_delay_s=_seconds(data.get("publish_retry_delay_s", DEFAULT_PUBLISH_RETRY_DELAY_S),
                                       "publish_retry_delay_s"),
        texture_sets=texture_sets,
    )


def load_config(path: str | Path) -> SyncConfig:
    try:
        with Path(path).expanduser().open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise ConfigError(f"Cannot read sync configuration: {exc}") from exc
    return parse_config(data)


def discover_config_path() -> Path | None:
    override = os.environ.get("STOU_TEXTURE_SYNC_CONFIG")
    if override:
        return _path(override, "STOU_TEXTURE_SYNC_CONFIG")
    default = Path.home() / ".stou_texture_sync" / "m1a_config.json"
    return default if default.is_file() else None
