# Label Hold evaluation notes

This file records what the lot-release gate **does measure**, and what it
does **not**. It is not a published F1 on a labeled photo corpus. Gemini
extraction is stochastic. The HOLD/RELEASE decision after extraction is not.

Code under test: `apps/adk-runtime/label_hold/allergens.py`, matcher
callback `_matcher_before` in
`apps/adk-runtime/agents/release_pipeline/agent.py`.

---

## What is deterministic

Given three allergen lists (already extracted), the verdict is Python:

```
undeclared = (normalize(spec) ∪ normalize(coa)) − normalize(label)
```

- If any ingest extract has `missing_document=true`, status is **held**
  and reason is `incomplete_packet`. The overlay still runs, but the
  missing-document flag wins.
- Else if `undeclared` is non-empty, status is **held**.
- Else status is **released**.

The matcher `LlmAgent` has a `before_model_callback` that writes this
result into `session.state["match"]`. The model may explain. It cannot
change `verdict_status` or `undeclared`. Same precedence as a rule
service: explain, never override.

`normalize_allergen()` returns `None` for unknown tokens. Those tokens
**do not** enter the set difference. That is fail-closed on invention
(a model hallucination like `"protein"` cannot create a hold) and also
means an unmapped synonym in `allergens[]` is dropped rather than
treated as a major-9 allergen.

The overlay reads only the structured `allergens` arrays. `mentions_raw`
is stored for audit. It is not an input to `undeclared()`.

---

## What is not a published accuracy rate

Ingest is Gemini 3.5 Flash with `output_schema=AllergenExtract`. Flash
usually canonicalizes on its own (for example `"anchovy"` to `fish`).
We do **not** have a 10k-image labeled set or an F1 for that step.

Reliability claims below are:

1. Overlay unit cases (closed alias table, exact set math).
2. Named fixture packets with expected status and `undeclared`.
3. Live Cloud Run smokes that asserted those statuses (not a statistical
   sample).

If Flash omits an allergen from `allergens[]` that was only in free
text, Python cannot recover it. The synonym map is a second pass on the
structured list, not a re-OCR of the documents.

---

## Synonym map (code, not the README summary)

Canonical ids are the FDA major-9 tokens in `schema.US_MAJOR_9_CANONICAL`
(`crustacean_shellfish`, `soybeans`, not "shellfish" / "soy").

Exact keys in `_ALIASES` today:

| Canonical | Aliases in code |
|---|---|
| milk | dairy, cream, butter, cheese, whey, casein, lactose, yogurt |
| eggs | egg, albumin, ovomucoid |
| fish | cod, salmon, tuna, anchovy |
| crustacean_shellfish | shellfish, shrimp, crab, lobster, crayfish |
| tree_nuts | almond, cashew, walnut, pecan, hazelnut, pistachio |
| peanuts | peanut, groundnut |
| wheat | gluten, flour, semolina, barley |
| soybeans | soy, soya, soybean, tofu |
| sesame | tahini, sesame seed, sesame seeds |

Also accepted: the canonical token itself, and space vs underscore
(`tree nuts` to `tree_nuts`).

Tokens often mentioned in docs or fixtures that are **not** in this
table (Flash is expected to canonicalize first): `lactalbumin`,
`butterfat`, `bass`, `halibut`, `prawn`, `arachis`, `durum`, `edamame`.

Known mapping choice: `barley` normalizes to `wheat`. That is a closed
table decision, not an FDA identity.

---

## Overlay cases (always true for these inputs)

These do not call Gemini. They are the contract of `undeclared()`.

| spec | coa | label | result |
|---|---|---|---|
| milk | milk | [] | `("milk",)` held |
| butter, whey | dairy | [] | `("milk",)` held (aliases collapse) |
| milk | milk | milk | `()` released |
| wheat, milk, eggs | wheat, milk, eggs | wheat | `("eggs", "milk")` held (partial Contains:) |
| tuna, soy | fish, soybeans | soybeans, sesame, wheat | `("fish",)` held |
| protein | milk | milk | `()` released (`protein` dropped) |
| milk | milk | dairy | `()` released (label alias counts as milk) |
| almond, wheat | cashew, wheat | tree_nuts, wheat | `()` released |

Reproduce:

```bash
cd apps/adk-runtime   # needs pydantic (same as the Cloud Run image)
PYTHONPATH=. python3 -c "
from label_hold.allergens import undeclared, normalize_allergen
assert normalize_allergen('butter') == 'milk'
assert normalize_allergen('lactalbumin') is None
assert undeclared(['wheat','milk','eggs'], ['wheat','milk','eggs'], ['wheat']) == ('eggs', 'milk')
assert undeclared(['butter','whey'], ['dairy'], []) == ('milk',)
print('overlay checks ok')
"
```

---

## Fixture packets (ingest plus overlay)

Expected outcomes for the shipped scenarios. Image presets live under
`fixtures/` (binaries may be gitignored locally; thumbs and
`generate_fixtures.py` define them). Text triples are in-repo.

| Packet | Expected status | Expected undeclared / reason | What it exercises |
|---|---|---|---|
| `hk-hold-milk` (text) | held | milk | butter/whey in spec, no Contains: Milk |
| `hk-release` (text) | released | empty | same recipe, label declares milk |
| `hk-incomplete` (text) | held | `incomplete_packet` | missing/unreadable document, LoopAgent |
| `hk-raw-tuna` | held | fish | tuna to fish; Contains omits fish |
| `hk-multi-allergen` | held | eggs, milk | partial Contains: WHEAT only |
| `hk-multi-allergen-released` | released | empty | full Contains: WHEAT, MILK, EGGS |
| `hk-empty-label` | held | wheat, milk, eggs | blank allergen panel, not incomplete_packet |
| `hk-tree-nuts-mix` | released | empty | almond/cashew/hazelnut to tree_nuts |

UI presets in `apps/frontend/src/components/UploadPanel.tsx` map to the
five image packets (not the three text triples).

Live smokes (Cloud Run, not a benchmark):

- `tests/smoke_end_to_end.py`: `HK-SMOKE-HOLD` held, `HK-SMOKE-MULTI` held,
  `HK-SMOKE-REL` released, then lean-view `write_id` match via CDC.
- `AGENTS.md`: `HK-HOLD-MILK` held `[milk]`; `HK-MULTI` held
  `{eggs, milk}`; Gemma hold `HK-GEMMA-MULTI` held `[eggs, milk]`;
  released path skipped Gemma (`summary` empty).

Those live rows prove the chain once per lot_id. They are not a
confidence interval.

---

## Fail-closed defaults

| Condition | Product behavior |
|---|---|
| Empty or unreadable packet | held, `incomplete_packet` |
| Partial Contains: | held, named missing allergens |
| Overlay empty | released |
| Gemma 4 31B | HELD lots only (executive summary). Released lots skip the call. Summary never changes status. |

---

## How to re-run live checks

```bash
# Overlay only (no GCP; pydantic required)
cd apps/adk-runtime && PYTHONPATH=. python3 -c "from label_hold.allergens import undeclared; print(undeclared(['milk'],['milk'],[]))"

# Full stack (needs ADC + live URLs)
python3 tests/smoke_cdc.py
python3 tests/smoke_end_to_end.py
```

There is no `pytest` suite for `allergens.py` in this repo yet
(`tests/README.md` still lists `test_allergens.py` as planned). The
overlay snippet above is the current unit check.
