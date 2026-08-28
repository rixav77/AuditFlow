"""Adapters: foreign payment sources -> canonical schema (docs/CANONICAL_SCHEMA.md)."""

from __future__ import annotations

from engine.adapters.base import SourceAdapter, empty_tabs, scan_report
from engine.adapters.builtin import OurBatchAdapter

__all__ = ["SourceAdapter", "OurBatchAdapter", "empty_tabs", "scan_report"]
