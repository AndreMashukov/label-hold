"""label_hold: core domain logic for the lot-release gate.

Pure-Python helpers (schema, allergen set difference) and Firestore writer.
No ADK imports here; ADK-only code lives in apps/adk-runtime/agents/.
"""
from label_hold.schema import AllergenExtract, LotStatus, Verdict
from label_hold.allergens import US_MAJOR_9, undeclared, normalize_allergen
from label_hold.lots import write_lot_status, get_lot_status
from label_hold.bus import publish_lot_event

__all__ = [
    "AllergenExtract",
    "LotStatus",
    "Verdict",
    "US_MAJOR_9",
    "undeclared",
    "normalize_allergen",
    "write_lot_status",
    "get_lot_status",
    "publish_lot_event",
]
