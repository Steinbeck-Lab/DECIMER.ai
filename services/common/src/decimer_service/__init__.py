"""Shared HTTP skeleton for the DECIMER model services."""

from .app import Limits, Model, create_app

__all__ = ["Limits", "Model", "create_app"]
__version__ = "0.1.0"
