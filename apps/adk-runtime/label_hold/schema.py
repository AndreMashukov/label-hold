"""Pydantic schemas for the lot-release gate.

These types are shared between the ADK ingest agents (as `output_schema`)
and the Firestore writer (as the stored document shape).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# US FDA "Big 9" major allergens. Hard-coded (not a rules engine) per PLAN §4.
US_MAJOR_9_CANONICAL: tuple[str, ...] = (
    "milk",
    "eggs",
    "fish",
    "crustacean_shellfish",
    "tree_nuts",
    "peanuts",
    "wheat",
    "soybeans",
    "sesame",
)


class AllergenExtract(BaseModel):
    """Output of one ingest agent (spec, CoA, or label)."""

    product_name: str = Field(default="", description="Product name as read from the document")
    lot_hint: str | None = Field(default=None, description="Lot id mentioned in the document, if any")
    allergens: list[str] = Field(default_factory=list, description="Subset of US_MAJOR_9_CANONICAL")
    mentions_raw: list[str] = Field(default_factory=list, description='Free-form allergen mentions, e.g. ["butter", "whey", "contains milk"]')
    missing_document: bool = Field(default=False, description="True if the document was empty / unreadable")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Self-reported extraction confidence")


# Allowed values for the lot status (kept literal so misspells fail loudly).
LotStatus = Literal["held", "released"]


class Verdict(BaseModel):
    """The matcher's verdict, derived deterministically from the three extracts."""

    lot_id: str
    status: LotStatus
    undeclared: list[str] = Field(default_factory=list)
    reason: str = Field(default="")
    missing_document: bool = Field(default=False)
    spec: AllergenExtract | None = None
    coa: AllergenExtract | None = None
    label: AllergenExtract | None = None
