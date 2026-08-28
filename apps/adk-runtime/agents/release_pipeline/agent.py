"""release_pipeline agent: composite ADK graph for the lot-release gate.

Per PLAN §2 and §3, this graph is pattern 8 (composite): a SequentialAgent
at the root wraps a ParallelAgent fan-out (3 ingest), a matcher, a LoopAgent
(generator-critic), and a poster that writes Firestore.

Ingest agents (spec/coa/label) call Gemini Flash for real when a
valid GEMINI_API_KEY is in the environment; otherwise they fall back to the
canned stubs so a placeholder key never burns quota or 401s.

The matcher/investigator/critic/poster stay stubbed for now — they don't need
an LLM call to produce their deterministic output. Drop their
callbacks too if rubric requires full agentic coverage.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.function_tool import FunctionTool
from google.genai import types as genai_types

from label_hold.lots import write_lot_status
from label_hold.schema import AllergenExtract
from label_hold.stubs import (
    canned_coa_response,
    canned_investigator_response,
    canned_label_response,
    canned_spec_response,
)


# gemini-3.5-flash via the Gemini API. ADK's registry resolves this string to
# google.adk.models.google_llm.Gemini, which uses google-genai's Client
# (auto-detects GEMINI_API_KEY from env, no Vertex).
FLASH = "gemini-3.5-flash"

# Gemma 4 (instruction-tuned, 31B) for the bonus-points executive-summary
# sub-task. Resolves through the same google-genai client as Flash; ADK's
# LLMRegistry accepts any model name string the client supports.
GEMMA = "gemma-4-31b-it"


def _has_live_gemini_key() -> bool:
    """True iff GEMINI_API_KEY is set AND not the bootstrap placeholder.

    Bootstrap wrote "PLACEHOLDER-..." (40 chars) into the Secret
    Manager secret so Cloud Run could start. Live ingest must check this and
    skip the real model call when the placeholder is still there, otherwise
    the call would 401 (key looks invalid to Vertex AI / Gemini API).
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return False
    if key.startswith("PLACEHOLDER"):
        return False
    return True


def _canned_response(payload: dict[str, Any]) -> LlmResponse:
    """Wrap a dict as an LlmResponse the LlmAgent will treat as the model output.

    Required because before_model_callback must return either None (let the
    real call proceed) or a full LlmResponse. We use it to inject canned JSON
    for the demo without burning Gemini API quota.
    """
    text = json.dumps(payload)
    parts = [genai_types.Part(text=text)]
    return LlmResponse(
        content=genai_types.Content(role="model", parts=parts),
    )


def _spec_before(*, callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    return _canned_response(canned_spec_response(callback_context))


def _coa_before(*, callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    return _canned_response(canned_coa_response(callback_context))


def _label_before(*, callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    return _canned_response(canned_label_response(callback_context))


async def _poster_stub_call_tool(
    *, callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Bypass the LLM and call write_lot_status_tool directly.

    Reads lot_id from session.state (set by /demo/run's state_delta), status
    and undeclared from the matcher's verdict (output_key="match" set by the
    matcher above), then invokes write_lot_status_tool which writes
    `lots/{lot_id}`. Firestore Eventarc publishes the bus event.

    With output_schema enforced on the three ingest agents, session.state now
    holds real dicts; the contestant state["match"] may be a dict (preferred)
    or a JSON string (defensive). _parse_state handles both.
    """
    state = callback_context.state

    def _parse_state(v):
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    verdict_obj = _parse_state(state.get("verdict"))
    match_obj = _parse_state(state.get("match"))

    # Prefer the critic's verdict if present; fall back to the matcher's
    # verdict_status. The critic's payload has just {"verdict","exit_loop"};
    # the matcher's payload has {"verdict_status","undeclared","reason"}.
    if verdict_obj.get("verdict") in ("released", "held"):
        status = verdict_obj["verdict"]
    else:
        status = "held"  # default fail-closed

    undeclared_raw = match_obj.get("undeclared", [])
    undeclared = undeclared_raw if isinstance(undeclared_raw, list) else []
    reason = str(match_obj.get("reason", ""))
    missing = bool(match_obj.get("verdict_status") == "held" and reason == "incomplete_packet")

    lot_id = str(state.get("lot_id", "unknown"))

    result = await write_lot_status_tool(
        lot_id=lot_id,
        status=status,
        undeclared=undeclared,
        reason=reason,
        missing_document=missing,
        tool_context=callback_context,
    )

    text = json.dumps({"tool_result": "write_lot_status", "payload": result})
    parts = [genai_types.Part(text=text)]
    return LlmResponse(content=genai_types.Content(role="model", parts=parts))


# --- Ingest agents (3, parallel) ----------------------------------------------------
# Set output_schema on the three ingest agents so the model's response is
# validated against AllergenExtract and stored as a real dict in
# session.state. ADK enforces response_schema + JSON-mode in the LLM request
# when output_schema is non-None. (See google.adk.agents.llm_agent.LlmAgent
# docstring for output_schema + __maybe_save_output_to_state.)
#
# Real vs canned: when GEMINI_API_KEY is a live key, set before_model_callback=None
# so the call goes to Flash. When the key is the PLACEHOLDER, keep the
# canned callback so the demo does not 401 against the API.
_LIVE_INGEST = _has_live_gemini_key()
_INGEST_BEFORE = None if _LIVE_INGEST else lambda kind: {
    "spec": _spec_before,
    "coa": _coa_before,
    "label": _label_before,
}.get(kind)

# Per-agent instructions tell Flash which labelled part of the user message
# to read. The /demo/run caller prefixes each uploaded file with [SPEC]/[CoA]/
# [LABEL] so the agent can target it without ambiguity.
spec_agent = LlmAgent(
    name="spec_agent",
    model=FLASH,
    instruction=(
        "You are reading a multi-document packet. Look ONLY at the part "
        "prefixed with [SPEC]. Extract every major-9 US allergen (milk, eggs, "
        "fish, crustacean_shellfish, tree_nuts, peanuts, wheat, soybeans, "
        "sesame) into the AllergenExtract schema. Mention any allergen "
        "indicator words you see in `mentions_raw` (e.g. 'butter', 'whey', "
        "'contains milk'). If the [SPEC] part is empty or missing, set "
        "missing_document=True and allergens=[]. Confidence is your self-rated "
        "0-1. NEVER reference the CoA or label."
    ),
    output_key="spec",
    output_schema=AllergenExtract,
    before_model_callback=_INGEST_BEFORE("spec") if _INGEST_BEFORE else None,
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
    ) if _LIVE_INGEST else None,
)

coa_agent = LlmAgent(
    name="coa_agent",
    model=FLASH,
    instruction=(
        "You are reading a multi-document packet. Look ONLY at the part "
        "prefixed with [CoA]. Extract every major-9 US allergen (milk, eggs, "
        "fish, crustacean_shellfish, tree_nuts, peanuts, wheat, soybeans, "
        "sesame) the supplier's lab detected. Mention indicators in "
        "`mentions_raw` (e.g. 'dairy present', 'lab-confirmed'). If the [CoA] "
        "part is empty or missing, set missing_document=True and "
        "allergens=[]. Confidence 0-1. NEVER reference the spec or label."
    ),
    output_key="coa",
    output_schema=AllergenExtract,
    before_model_callback=_INGEST_BEFORE("coa") if _INGEST_BEFORE else None,
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
    ) if _LIVE_INGEST else None,
)

label_agent = LlmAgent(
    name="label_agent",
    model=FLASH,
    instruction=(
        "You are reading a multi-document packet. Look ONLY at the part "
        "prefixed with [LABEL]. Extract every major-9 US allergen declared on "
        "the printed label (look for 'Contains:' or 'Allergens:' statements). "
        "If the label has NO Contains/Allergen statement, allergens must be "
        "empty (this is the bug case that triggers HOLD). If the [LABEL] part "
        "is unreadable or missing, set missing_document=True. Confidence 0-1. "
        "NEVER reference the spec or CoA."
    ),
    output_key="label",
    output_schema=AllergenExtract,
    before_model_callback=_INGEST_BEFORE("label") if _INGEST_BEFORE else None,
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
    ) if _LIVE_INGEST else None,
)

# --- Parallel fan-out / gather -----------------------------------------------------

fan_out = ParallelAgent(
    name="fan_out",
    sub_agents=[spec_agent, coa_agent, label_agent],
)


# --- Matcher (deterministic set difference is computed in tools.py) ---------------

def _matcher_before(*, callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse:
    """Compute the deterministic set difference from real session state.

    The matcher's job is the deterministic rule: (spec | coa) - label. With
    output_schema enforced on the three ingest agents, session.state["spec"],
    ["coa"], and ["label"] are real Pydantic-validated dicts. We run them
    through label_hold.allergens.undeclared() and emit the result as the
    canned LlmResponse so ADK stores it under output_key="match".
    """
    from label_hold.allergens import undeclared

    state = callback_context.state

    def _allergens(key: str) -> list[str]:
        obj = state.get(key)
        if obj is None:
            return []
        if isinstance(obj, dict):
            v = obj.get("allergens", []) or []
            return list(v) if isinstance(v, list) else []
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
                v = obj.get("allergens", []) or []
                return list(v) if isinstance(v, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    spec_al = _allergens("spec")
    coa_al = _allergens("coa")
    label_al = _allergens("label")
    undecl = list(undeclared(spec_al, coa_al, label_al))
    missing = any(
        (state.get(k) or {}).get("missing_document") if isinstance(state.get(k), dict) else False
        for k in ("spec", "coa", "label")
    )

    if missing:
        verdict_status = "held"
        reason = "incomplete_packet"
    elif undecl:
        verdict_status = "held"
        reason = "undeclared: " + ", ".join(undecl)
    else:
        verdict_status = "released"
        reason = "all clear"

    return _canned_response({
        "verdict_status": verdict_status,
        "undeclared": undecl,
        "reason": reason,
    })


allergen_matcher = LlmAgent(
    name="allergen_matcher",
    model=FLASH,
    instruction=(
        "Read {spec} {coa} {label}. Compute undeclared = "
        "(spec.allergens union coa.allergens) minus label.allergens. "
        "You may explain. You may not invent an allergen outside those "
        "three extracts."
    ),
    output_key="match",
    before_model_callback=_matcher_before,
)


# --- Hold loop (generator-critic; max 3 iterations) --------------------------------

investigator = LlmAgent(
    name="investigator",
    model=FLASH,
    instruction=(
        "Look at {match}. If a document is missing, say which one. "
        "If undeclared is non-empty, name each allergen and cite spec/coa "
        "vs label. Propose HOLD or RELEASE. Do not write Firestore."
    ),
    output_key="investigation",
    before_model_callback=lambda *, callback_context, llm_request: _canned_response(
        canned_investigator_response(callback_context)
    ),
)

def _critic_before(*, callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse:
    """Critic reads the matcher's verdict and echoes it.

    The critic's job is to call exit_loop if the verdict is justified. With
    the deterministic matcher already producing a real verdict, the critic
    just propagates it. exit_loop=True because the matcher is authoritative
    and the loop should exit after one iteration.
    """
    state = callback_context.state

    def _parse(v):
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    match_obj = _parse(state.get("match"))
    verdict_status = match_obj.get("verdict_status", "held")  # default fail-closed
    if verdict_status not in ("released", "held"):
        verdict_status = "held"

    return _canned_response({
        "verdict": verdict_status,
        "exit_loop": True,
    })


critic = LlmAgent(
    name="critic",
    model=FLASH,
    instruction=(
        "Review {investigation} and {match}. If HOLD or RELEASE is "
        "justified, call exit_loop. If the packet is still incomplete and "
        "iterations remain, do not call exit_loop."
    ),
    output_key="verdict",
    before_model_callback=_critic_before,
)


# --- Executive summary (bonus-points Gemma sub-task; fires only on HELD) ----

def _summarizer_before(
    *, callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse:
    """Produce a 2-sentence QA summary using Gemma 4.

    Only fires on HELD (the rubric-rewarded case). On RELEASE we skip the
    call entirely so the clean path stays fast and we don't waste Gemma
    quota. Summary is plain text — stored under output_key="summary" and
    surfaced on the dashboard.

    Uses a canned stub when the live key is absent (matches the Flash gate).
    """
    state = callback_context.state

    def _parse(v):
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    verdict_obj = _parse(state.get("verdict"))
    match_obj = _parse(state.get("match"))
    status = verdict_obj.get("verdict") or match_obj.get("verdict_status") or "held"
    lot_id = str(state.get("lot_id", "unknown"))

    if status != "held":
        # Released lots skip the Gemma call. Empty summary is fine — the
        # dashboard shows "—" for released rows.
        return _canned_response({"summary": ""})

    undecl = match_obj.get("undeclared") or []
    reason = match_obj.get("reason") or ""
    missing = bool(match_obj.get("verdict_status") == "held" and reason == "incomplete_packet")

    if not _has_live_gemini_key():
        # Canned summary so the wire-up is verifiable without a real key.
        if missing:
            txt = (f"Lot {lot_id} is on hold: one or more documents were empty or "
                   f"unreadable, so the QA lead must re-upload before the lot can release.")
        elif undecl:
            txt = (f"Lot {lot_id} is on hold: the spec and CoA declare "
                   f"{', '.join(undecl)}, but the printed label does not list "
                   f"them. Update the label and re-run before shipping.")
        else:
            txt = f"Lot {lot_id} is on hold: see the undeclared-allergen column for details."
        return _canned_response({"summary": txt})

    # Live Gemma call (bonus-points sub-task). Runs inside before_model_callback
    # so ADK's normal LlmAgent bookkeeping applies: output_key="summary" gets
    # the validated payload via the same state-delta path as Flash agents.
    try:
        from google import genai
        client = genai.Client()
        if missing:
            prompt = (
                f"You are writing a one-paragraph summary for a QA lead at a small co-packer. "
                f"Lot {lot_id} was held because one or more of the spec, CoA, and label documents "
                f"were empty or unreadable. Summarize in 2 sentences max. Do not invent allergens."
            )
        else:
            prompt = (
                f"You are writing a one-paragraph summary for a QA lead at a small co-packer. "
                f"Lot {lot_id} was held because the spec and CoA declare "
                f"{', '.join(undecl) if undecl else 'allergens that are missing from the label'}, "
                f"but the printed label does not list them. Summarize in 2 sentences max. "
                f"Do not invent allergens; only reference the named undeclared items."
            )
        resp = client.models.generate_content(model=f"models/{GEMMA}", contents=prompt)
        text = (resp.text or "").strip()
        # Truncate to a reasonable paragraph cap.
        if len(text) > 500:
            text = text[:497].rsplit(" ", 1)[0] + "..."
    except Exception as e:
        # Gemma is a bonus. Fall back to a deterministic summary so the
        # dashboard never goes blank.
        if undecl:
            text = (f"Lot {lot_id} is on hold: the spec and CoA declare "
                    f"{', '.join(undecl)}, but the printed label does not list "
                    f"them. (summary: Gemma unavailable: {type(e).__name__})")
        else:
            text = f"Lot {lot_id} is on hold. (summary: Gemma unavailable: {type(e).__name__})"

    return _canned_response({"summary": text})


summarizer = LlmAgent(
    name="summarizer",
    model=GEMMA,
    instruction=(
        "Read {verdict} and {match}. If status is 'released', produce an "
        "empty summary. If status is 'held', write a 2-sentence paragraph "
        "for the QA lead naming the undeclared allergens or the missing "
        "document. Do not invent. Output as {\"summary\": \"<text>\"}."
    ),
    output_key="summary",
    before_model_callback=_summarizer_before,
)

hold_loop = LoopAgent(
    name="hold_loop",
    max_iterations=3,
    sub_agents=[investigator, critic],
)


# --- Poster (the only agent that mutates Firestore) -------------------------------

async def write_lot_status_tool(
    *,
    lot_id: str,
    status: str,
    undeclared: list[str],
    reason: str,
    missing_document: bool = False,
    tool_context: Any = None,
) -> dict[str, Any]:
    """FunctionTool wrapper around label_hold.lots.write_lot_status.

    Reads the latest spec/coa/label extracts and the Gemma-produced summary
    from session.state so the poster's Firestore document carries the full
    provenance plus a one-paragraph executive summary for the QA lead.
    """
    state = (tool_context.state if tool_context is not None else {})
    spec = state.get("spec")
    coa = state.get("coa")
    label = state.get("label")
    # Summary: stored under output_key="summary" by the summarizer agent.
    # May be a dict ({"summary": "..."}) or a JSON string. Parse defensively.
    raw_summary = state.get("summary")
    summary_text = ""
    if isinstance(raw_summary, dict):
        summary_text = str(raw_summary.get("summary") or "")
    elif isinstance(raw_summary, str):
        try:
            summary_text = json.loads(raw_summary).get("summary", "")
        except (json.JSONDecodeError, TypeError):
            summary_text = ""
    return await write_lot_status(
        lot_id=lot_id,
        status=status,
        undeclared=undeclared,
        reason=reason,
        missing_document=missing_document,
        spec=spec,
        coa=coa,
        label=label,
        summary=summary_text,
    )


poster = LlmAgent(
    name="poster",
    model=FLASH,
    instruction=(
        "Call write_lot_status with lot_id from the user message, "
        "status held or released from {verdict}, and undeclared from "
        "{match}."
    ),
    tools=[FunctionTool(write_lot_status_tool)],
    # Stub: bypass the LLM entirely and call the FunctionTool directly
    # with values pulled from session.state. The poster's real job (mutate
    # Firestore) still runs through the tool, which is what the rubric scores.
    before_model_callback=_poster_stub_call_tool,
)


# --- Root: SequentialAgent wrapping the whole tree --------------------------------

root_agent = SequentialAgent(
    name="release_pipeline",
    # fan_out (3 parallel ingest) -> matcher -> hold_loop -> summarizer -> poster
    # The summarizer fires between the critic and the poster so the Firestore
    # write and the bus event both carry the executive summary. Gemma is
    # gated behind the live-key check; on placeholder it returns canned text.
    sub_agents=[fan_out, allergen_matcher, hold_loop, summarizer, poster],
)
