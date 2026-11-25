"""Painter lifecycle entry points. The sync core can be imported without Painter."""

import sys
import types

from .config import ConfigError, discover_config_path
from .config import load_config as _read_config

__version__ = "0.0.0"

_STATE = sys.modules.setdefault("_stou_texture_sync_state", types.ModuleType("_stou_texture_sync_state"))


def start_plugin():
    """Register one Painter controller, replacing any registration from a reload."""
    close_plugin()
    from .host_painter import PainterSyncController

    config = getattr(_STATE, "config_override", None)
    error = None
    if config is None:
        try:
            path = discover_config_path()
            if path is not None:
                config = _read_config(path)
        except ConfigError as exc:
            error = str(exc)
    _STATE.controller = PainterSyncController(config, config_error=error)


def close_plugin():
    """Disconnect Painter hooks, then stop the current controller."""
    controller = get_controller()
    if controller is not None:
        if not controller.close():
            raise RuntimeError("StoUTextureSync is still stopping")
        _STATE.controller = None


def load_config(path):
    """Load an explicit configuration and apply it if the plug-in is running."""
    config = _read_config(path)
    active = get_controller() is not None
    if active:
        close_plugin()
    _STATE.config_override = config
    if active:
        start_plugin()
    return config


def get_controller():
    return getattr(_STATE, "controller", None)
