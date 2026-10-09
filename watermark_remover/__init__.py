"""Independent local PDF watermark removal tool."""

from .core import RemovalOptions, RemovalResult, remove_watermarks

__all__ = ["RemovalOptions", "RemovalResult", "remove_watermarks"]
