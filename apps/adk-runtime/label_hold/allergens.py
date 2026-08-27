"""US major-9 allergen set logic.

Fail-closed set difference: if any allergen is mentioned in the spec or CoA
but missing from the label, the lot is held. The matcher LLM explains but
cannot override this set arithmetic.
"""
from __future__ import annotations

from .schema import US_MAJOR_9_CANONICAL


# Public alias for tests and external code.
US_MAJOR_9: frozenset[str] = frozenset(US_MAJOR_9_CANONICAL)


# Aliases the model is likely to return (but we still normalize before use).
_ALIASES: dict[str, str] = {
    # milk
    "dairy": "milk", "cream": "milk", "butter": "milk", "cheese": "milk",
    "whey": "milk", "casein": "milk", "lactose": "milk", "yogurt": "milk",
    # eggs
    "egg": "eggs", "albumin": "eggs", "ovomucoid": "eggs",
    # fish
    "cod": "fish", "salmon": "fish", "tuna": "fish", "anchovy": "fish",
    # crustacean shellfish
    "shellfish": "crustacean_shellfish", "shrimp": "crustacean_shellfish",
    "crab": "crustacean_shellfish", "lobster": "crustacean_shellfish",
    "crayfish": "crustacean_shellfish",
    # tree nuts
    "almond": "tree_nuts", "cashew": "tree_nuts", "walnut": "tree_nuts",
    "pecan": "tree_nuts", "hazelnut": "tree_nuts", "pistachio": "tree_nuts",
    # peanuts
    "peanut": "peanuts", "groundnut": "peanuts",
    # wheat
    "gluten": "wheat", "flour": "wheat", "semolina": "wheat", "barley": "wheat",
    # soybeans
    "soy": "soybeans", "soya": "soybeans", "soybean": "soybeans", "tofu": "soybeans",
    # sesame
    "tahini": "sesame", "sesame seed": "sesame", "sesame seeds": "sesame",
}


def normalize_allergen(raw: str) -> str | None:
    """Map a free-form allergen mention to its canonical US major-9 form.

    Returns None if the raw string is not a known allergen. The mapping is
    intentionally narrow: it is the fail-closed gate that keeps the lot hold
    honest. Anything we cannot prove is in the major-9 list does not contribute
    to the verdict.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    # exact canonical hit
    if key in US_MAJOR_9:
        return key
    # alias hit
    canon = _ALIASES.get(key)
    if canon:
        return canon
    # case where the model wrote "tree nuts" with space instead of underscore
    if key.replace(" ", "_") in US_MAJOR_9:
        return key.replace(" ", "_")
    return None


def undeclared(spec_allergens: list[str], coa_allergens: list[str], label_allergens: list[str]) -> tuple[str, ...]:
    """Fail-closed set difference: spec | coa minus label.

    Returns the tuple of canonical allergen names that the spec and/or CoA
    declare but the label does not. Empty tuple means clean (release-eligible).
    """
    spec = {a for a in (normalize_allergen(x) for x in spec_allergens) if a is not None}
    coa = {a for a in (normalize_allergen(x) for x in coa_allergens) if a is not None}
    label = {a for a in (normalize_allergen(x) for x in label_allergens) if a is not None}
    # anything declared by spec or coa but missing from the label
    return tuple(sorted((spec | coa) - label))
