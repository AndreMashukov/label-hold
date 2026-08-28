"""Canned ingest responses for stub mode.

These let the graph run end-to-end without burning Gemini API quota. Each
callback returns the AllergenExtract JSON a real LlmAgent would produce
for one of the demo fixtures (HK-HOLD-MILK, HK-RELEASE, HK-INCOMPLETE).

Drop the callbacks when live Flash ingest fills these in.
"""
from __future__ import annotations

import json
from typing import Any

from google.adk.agents.callback_context import CallbackContext


def _lot_id_from_state(ctx: CallbackContext) -> str:
    """Read the lot_id the caller put into session.state, default 'unknown'."""
    try:
        return str(ctx.state.get("lot_id", "unknown"))
    except Exception:
        return "unknown"


def canned_spec_response(ctx: CallbackContext) -> dict[str, Any]:
    """Spec ingest: harbor kitchen honey BBQ, contains milk via butter + whey."""
    return {
        "product_name": "Harbor Kitchen Honey BBQ Sauce",
        "lot_hint": _lot_id_from_state(ctx),
        "allergens": ["milk"],
        "mentions_raw": ["butter", "whey", "contains milk"],
        "missing_document": False,
        "confidence": 0.95,
    }


def canned_coa_response(ctx: CallbackContext) -> dict[str, Any]:
    """CoA ingest: dairy present, lab-tested."""
    return {
        "product_name": "Harbor Kitchen Honey BBQ Sauce",
        "lot_hint": _lot_id_from_state(ctx),
        "allergens": ["milk"],
        "mentions_raw": ["dairy present", "lab-confirmed"],
        "missing_document": False,
        "confidence": 0.93,
    }


def canned_label_response(ctx: CallbackContext) -> dict[str, Any]:
    """Label ingest: no Contains statement. Bug: milk missing on the label."""
    return {
        "product_name": "Harbor Kitchen Honey BBQ",
        "lot_hint": _lot_id_from_state(ctx),
        "allergens": [],  # <-- the bug that triggers HOLD
        "mentions_raw": [],
        "missing_document": False,
        "confidence": 0.92,
    }


def canned_investigator_response(ctx: CallbackContext) -> dict[str, Any]:
    """Investigator: explains why we are holding the lot."""
    return {
        "summary": "Spec and CoA declare milk. Label does not list milk. Hold.",
        "evidence": ["spec.allergens: [milk]", "coa.allergens: [milk]", "label.allergens: []"],
        "proposed_status": "held",
    }
