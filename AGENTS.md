# Label Hold — agent guide

## Project overview

A lot-release gate for a 12-person co-packer. Three independent documents decide if a lot ships: the product spec, the supplier Certificate of Analysis, and a photo of the printed label. An ADK composite pipeline (SequentialAgent > ParallelAgent > matcher LlmAgent > LoopAgent > poster LlmAgent) writes `lots/{id}.status = held|released` to Firestore. The product is the hold, not the extract.

**Track:** The Taskmaster (All Things Agentic Hackathon, Devpost).
**Deadline:** Aug 31, 2026 5:00pm PDT.

| Component | Directory | Cloud Run service | GCP primitive |
|---|---|---|---|
| ADK Control Service | `apps/adk-runtime/` | `adk-runtime` | Cloud Run v2 + Firestore `lots-db` |
| Lean-view consumer | `apps/leanview-consumer/` | `leanview-consumer` | Cloud Run v2 + Pub/Sub push + Firestore `lean-db` |
| Dashboard BFF | `apps/dashboard/` | `dashboard` | Cloud Run v2 (nginx + HTML) + reads `lean-db` |
| Event hub | `terraform/modules/event-hub/` | n/a | Pub/Sub topic `label-hold-events` + DLQ |
| Identity | `terraform/modules/identity/` | n/a | (kept for symmetry; not used in v1) |
| BFF module | `terraform/modules/bff-service/` | n/a | Lifted from pastebin-app-gcp; extended for raw Pub/Sub push |
| IaC | `terraform/envs/dev/` | n/a | Terraform 1.5+, hashicorp/google ~> 6.0 |

**Region:** `asia-southeast1`. **Default env:** `dev`. **Project:** `serverless-503308`.

## Live URLs (deployed)

- adk-runtime:       https://adk-runtime-hnvjxkvfoq-as.a.run.app  (Day 1 code, 2026-08-26 12:55 digest `sha256:6d662096...`)
- leanview-consumer: https://leanview-consumer-hnvjxkvfoq-as.a.run.app (IAM-protected, OIDC push from Pub/Sub only)
- dashboard:         https://dashboard-hnvjxkvfoq-as.a.run.app
- Pub/Sub topic:     label-hold-events (project serverless-503308)
- Pub/Sub sub:       label-hold-lean-view-sub (push, OIDC)

**Day 1 end-to-end smoke (2026-08-26 12:59):**
- `GET /health` → 200, `{"stub_mode":false,"gemini_key_present":true,"firestore_db":"lots-db"}`
- `GET /list-apps` → `["release_pipeline"]` (ADK loader found the composite graph)
- `POST /demo/run lot_id=HK-HOLD-MILK` → 200, `{"status":"held","undeclared":["milk"],"write_id":"3e4efc8d..."}`
- `GET /demo/lots/HK-HOLD-MILK` → full payload from Firestore (spec/CoA/label extracts + status + reason)
- leanview-consumer `/pubsub/push` handler: still a Day 0 ack-only stub (planned for Day 2 wiring).

**Day 2 wire-up — bus publish from inside the graph (2026-08-27 02:09):**
- `/demo/run` now self-calls `/run` instead of bypassing the graph. The poster LlmAgent's `write_lot_status_tool` mutates Firestore AND publishes a `lot.*` event to the `label-hold-events` topic.
- `label_hold/bus.py` is the only place that imports `google-cloud-pubsub`. `publish_lot_event()` is fire-and-forget: errors are logged and swallowed so a Pub/Sub outage cannot fail the Firestore write.
- `bus_message_id` is stamped into the Firestore row so the lean view can dedupe replays without an extra round-trip.
- Live smoke: `tests/smoke_bus_publish.py` runs three lots through `/demo/run` and asserts the Firestore row + leanview-consumer OIDC push. Exits 0 on all-pass, 1 on first failure.
- leanview-consumer log proves receipt: `INFO: 169.254.169.126:30748 - "POST /pubsub/push HTTP/1.1" 200 OK` within 90s of each `/demo/run`.

**Day 2.5 — Live Gemini ingest (gated on real key) (2026-08-27 02:51):**
- `_has_live_gemini_key()` detects whether `GEMINI_API_KEY` starts with `PLACEHOLDER` (Day 0 bootstrap sentinel). When the key is still the placeholder, ingest agents keep the canned stubs; the moment a real key lands in Secret Manager the agents flip to calling Gemini Flash with no further deploy.
- `output_schema=AllergenExtract` set on spec_agent/coa_agent/label_agent. ADK now enforces `response_schema` + `response_mime_type=application/json` and stores the validated dict under `output_key` in session.state (no more JSON-string round-trip).
- The matcher reads the three real dicts from session.state and runs `label_hold.allergens.undeclared()` to compute the deterministic set difference. With canned stubs that path returns milk; with real Flash ingest the three fixtures diverge (held / released / held-incomplete).
- `/demo/run` builds the `/run` payload with the three uploaded files as typed Parts on `new_message`: text uploads become `[{text: "[SPEC]\n..."}, ...]` parts; non-text (PDF/image) become `[{inline_data: {mime_type, data: <base64>}}, {text: "[LABEL attached as image/jpeg]"}]` so the per-agent instruction's "Look at the [LABEL] part" still makes sense.
- Real fixtures: `fixtures/hk-hold-milk/{spec,coa,label}.txt`, `fixtures/hk-release/`, `fixtures/hk-incomplete/`. Three real scenarios — dairy vs no-allergens vs empty documents — that genuinely produce different match outputs.
- All 6 rubric greps still pass (SequentialAgent, ParallelAgent, LoopAgent, LlmAgent, output_key, get_fast_api_app).
- Smoke test still passes (3/3) — wire-up didn't regress.

**Day 3 — multimodal ingest + lean materializer + live dashboard (2026-08-27 05:18):**
- `/demo/run` accepts real PNG/JPEG uploads via `inline_data` Parts on `/run`. Verified end-to-end against the deployed service: a PNG spec, PNG CoA, and JPEG label photo all extract correctly through Gemini 3.5 Flash. New fixtures: `fixtures/hk-hold-milk-v2-binary/` (the same Harbor Kitchen Honey BBQ scenario, but as scanned PNG/JPEG), `fixtures/hk-synonym-stress/` (spec/CoA use only synonyms — dairy, casein, butterfat, lactalbumin — never the literal token "milk"), `fixtures/hk-multi-allergen/` (wheat + milk + eggs in spec/CoA, `CONTAINS: WHEAT` on label), `fixtures/hk-multi-allergen-released/` (same recipe, `CONTAINS: WHEAT, MILK, EGGS`).
- Allergen synonyms: Flash canonicalizes on its own (`anchovy → fish`, `dairy/casein/butter/whey/lactalbumin/butterfat → milk`). `label_hold.allergens.normalize_allergen()` is the second line of defense (maps any straggler synonym to canonical form before the set difference).
- Multi-allergen: `HK-MULTI` correctly computes `{wheat, milk, eggs} ∪ {wheat, milk, eggs} − {Wheat} = {eggs, milk}` and holds with the exact allergen list. `HK-MULTI-REL` (same recipe, full declaration) releases. Verifies the FDA `Contains:` rule is enforced even when the label declares *some* allergens but not all.
- `apps/leanview-consumer/main.py` is no longer a stub. It decodes the Pub/Sub envelope, validates the leaf payload (CPCQ contract: `event_type`, `lot_id`, `status`, `write_id`, `undeclared`, `reason`), and upserts `lots-listen/{lot_id}` in the `lean-db` Firestore database. Stale replays (older `ts` than the stored row) are dropped. Returns 400 on malformed input so Pub/Sub doesn't retry forever.
- `apps/dashboard/` is now a FastAPI app (replaced the Day 0 nginx stub). Serves the SPA shell at `/` plus three JSON endpoints: `GET /api/lots` (live list of `lots-listen/` rows, ordered by `ts` desc), `GET /api/lots/{lot_id}`, and `POST /api/run` (multipart proxy to adk-runtime's `/demo/run`). The dashboard polls `/api/lots` every 5s so the UI updates automatically when a new lot lands.
- `tests/smoke_end_to_end.py` (replaces the earlier `smoke_bus_publish.py`) drives three lots through the dashboard BFF and asserts (1) `/api/run` returns the right verdict, (2) lean-db `lots-listen/{lot_id}` materializes with the matching `write_id`. Currently 3/3 PASS.
- All five services on Cloud Run:
  - `adk-runtime`        `adk-runtime-00009-rrt`        `sha256:c319950b...`  (Day 2.5 base)
  - `leanview-consumer`  `leanview-consumer-00002-5tw`   `sha256:329b566e...`  (Day 3 materializer)
  - `dashboard`          `dashboard-00006-g9n`           `sha256:9a5e791b...`  (Day 3 SPA + BFF)
  - Pub/Sub topic        `label-hold-events`             (no change from Day 2)
  - Firestore DBs        `lots-db` (system of record), `lean-db` (read model)

**Day 4 — single-source deploy + smoke + demo scripts (2026-08-27):**
- `scripts/deploy_all.sh` rebuilds and pins all three services in order (adk-runtime → leanview-consumer → dashboard). Each step delegates to the existing `deploy-image.sh`. Token bootstrap uses command substitution, never inline-prefix, to dodge the `*** ` redaction filter. Idempotent: re-running with no source changes just re-pins the same digest.
- `scripts/smoke_all.sh` drives one held lot (wheat+milk+eggs in spec/CoA, only Wheat on label) and one released lot (same recipe, full declaration) through `dashboard/api/run`, then polls `dashboard/api/lots/{lot_id}` for the lean-db materialization. Prints `HELD PASS / RELEASED PASS / PASS 2/2` or exits 1 with `FAIL`. Exit 2 if the dashboard is unreachable.
- `scripts/record_demo.sh` is the Day 4 recording shell: opens the dashboard root + proof-of-action harness in a browser, then runs `smoke_all.sh` in a separate terminal. The actual screen-capture (ffmpeg/OBS) is left to the operator because the recording host is out-of-band.
- All three scripts are `set -euo pipefail`, env-vars + flags only, no heredoc, no `>`/`<`/tee (only `>&2` for explicit stderr). `bash -n` parses clean on all three; `shellcheck` not installed in this container.
- AGENTS.md "Commands" section now shows how to invoke each script with the right env vars.

**Day 3.5 — Gemma executive summary + proof-of-action harness (2026-08-27 06:35):**
- New `summarizer` LlmAgent inserted between `hold_loop` and `poster`. Uses `gemma-4-31b-it` (resolved through the same `google-genai` client as Flash, no Vertex) and only fires on HELD lots — released lots skip the call to keep the clean path fast and avoid burning Gemma quota. Stores the 2-sentence paragraph under `output_key="summary"`.
- The poster reads `session.state["summary"]` (defensively parsed as dict or JSON string), threads it into `write_lot_status(summary=...)`, which persists it on `lots/{lot_id}` AND publishes it in the bus event payload. The leanview-consumer materializer also persists `summary` on `lots-listen/{lot_id}`. Dashboard surfaces the summary in the recent verdicts table (new column).
- Verified end-to-end with `HK-GEMMA-MULTI`: held, undeclared=[eggs, milk], summary="Lot HK-GEMMA-MULTI is on hold due to a labeling discrepancy. While the specification and CoA declare eggs and milk, these allergens are missing from the printed label." (167 chars from Gemma 4).
- `HK-GEMMA-REL` (released) came back with `summary: ""` — Gemma skipped, as designed. The graph now emits `events=12` instead of 11 (one more node traversed).
- `apps/dashboard/public/proof-of-action.html` is a 3-pane harness (terminal log + live verdicts + raw Firestore read) for the demo video. Bundled fixtures (`hk-multi-allergen/`, `hk-multi-allergen-released/`) are now part of the dashboard image so the harness runs without external file hosting. The dashboard serves them under `/fixtures/`.
- `.dockerignore` + `.gcloudignore` added to keep the Cloud Build tarball small (was 526 MiB / 23k files, now 368 KiB / 13 files for the dashboard; saves ~3 min per build).
- All five services on Cloud Run:
  - `adk-runtime`        `adk-runtime-00010-r98`        `sha256:1a1cebcc...`  (Day 3.5 Gemma summarizer)
  - `leanview-consumer`  `leanview-consumer-00003-czb`   `sha256:b3894e45...`  (Day 3.5 summary field)
  - `dashboard`          `dashboard-00008-cf2`           `sha256:944e6797...`  (Day 3.5 summary column + harness)
  - Pub/Sub topic        `label-hold-events`             (no change from Day 2)
  - Firestore DBs        `lots-db`, `lean-db`            (no schema change; new `summary` field is just additional data)

## Architecture rules (must hold)

1. **SequentialAgent wraps Parallel + Loop.** Root is `SequentialAgent` in `apps/adk-runtime/agents/release_pipeline/agent.py`. Anything less is the wrong architecture for this track.
2. **Three ingest agents, each with a unique `output_key`.** `spec`, `coa`, `label`. Parallel children must not share a key (ADK race). Downstream instructions literally contain `{spec} {coa} {label}`.
3. **Match overlay is deterministic.** `(spec.allergens | coa.allergens) - label.allergens`. The matcher LLM explains. The set difference decides. Same precedence as the book's rule service: model can explain, never override.
4. **`lots/` is the system of record; `lots-listen/` is the lean replicated read model.** Only `adk-runtime` writes `lots/`. Only `leanview-consumer` writes `lots-listen/`. The dashboard reads `lots-listen` via Firestore live subscribe.
5. **No HTTP RPC between BFF services for verdict flow.** All inter-service mutation flows through Pub/Sub (publish from adk-runtime, push to leanview-consumer). The CPCQ Publish -> Consume leg is the only path.
6. **Idempotent on `lot_id`.** Replaying HK-HOLD-MILK on the same `lot_id` produces exactly one row in `lots/` and one row in `lots-listen/` with the same `undeclared` list.

## Commands

```bash
# Build + push a new image digest
./scripts/deploy-image.sh <app-name>    # app-name in {adk-runtime, leanview-consumer, dashboard}

# Day 4: deploy all three in order
PROJECT_ID=serverless-503308 REGION=asia-southeast1 REPO=label-hold-apps-dev \
  bash scripts/deploy_all.sh

# Day 4: end-to-end smoke (held + released lot)
DASHBOARD_URL=https://dashboard-hnvjxkvfoq-as.a.run.app bash scripts/smoke_all.sh

# Day 4: demo recording (skeleton — see RECORDER_CMD comment)
DASHBOARD_URL=https://dashboard-hnvjxkvfoq-as.a.run.app bash scripts/record_demo.sh

# Terraform
cd terraform/envs/dev
terraform init -input=false
terraform fmt -check
terraform validate
terraform plan -input=false -out=./dev.tfplan
# STOP — ask for approval
terraform apply -input=false ./dev.tfplan

# Local lint / typecheck (Day 1 onward)
cd apps/adk-runtime && python3 -m py_compile main.py
cd apps/leanview-consumer && python3 -m py_compile main.py
```

## Debugging live GCP resources

When the task involves deployed state (Cloud Run errors, Firestore docs, Pub/Sub messages), use `gcloud` with the bearer-token unblock if needed.

```bash
CLOUDSDK_AUTH_ACCESS_TOKEN=*** gcloud run services describe adk-runtime --region=asia-southeast1 --project=serverless-503308 --format=json | jq
gcloud firestore databases list --project=serverless-503308
gcloud pubsub topics list --project=serverless-503308
gcloud pubsub subscriptions describe label-hold-lean-view-sub --project=serverless-503308
```

Per the gcp-terraform-cloud-run skill: the container's ADC is an `authorized_user` blob. `gcloud` CLI commands that need a real user fail unless you set the bearer token from ADC first. See `references/container-auth-model.md` in that skill.

## Known gotchas (verified during Day 0 deploy)

- **Cloud Run v2 first-region init.** First deploy in a region takes 1-3 min. Wait, re-plan, re-apply. Do not tight-loop.
- **Pub/Sub push OIDC requires TWO IAM bindings.** The runtime SA must have (a) `roles/iam.serviceAccountTokenCreator` on itself (to mint the OIDC token) AND (b) `roles/run.invoker` on its own Cloud Run service (so the OIDC token grants access). The bff-service module wires both via `enable_bus_push_subscription = true`. Without (b), Pub/Sub pushes arrive with no Authorization header and 401.
- **IAM propagation is slow.** Even with bindings in place, Pub/Sub pushes fail for 60-120s while IAM propagates. Wait, then retry.
- **Image digest pinning.** Update `terraform.tfvars` with `@sha256:...` digests, not `:latest` tags. Plan diffs are visible only when the digest changes.
- **Docker multi-stage `pip install --target=/install` does NOT put entry-point scripts in `/usr/local/bin`.** Use plain `pip install .` (no `--target`) and `COPY --from=builder /usr/local/bin /usr/local/bin` to keep `uvicorn` reachable.
- **Pre-existing resources need `terraform import`.** When bootstrapping outside Terraform (faster than waiting for plan + apply), import each resource with `terraform import <addr> <id>` so the next plan treats it as no-op.
- **nginx alpine default port is 80, not 8080.** Cloud Run probes 8080. Override with a custom `nginx.conf` that listens on 8080, and `chmod -R a+r /usr/share/nginx/html/` so the `nginx` user can read the copied files.
- **One Firestore DB per module instance is the default.** When two modules share a DB (e.g. leanview-consumer and dashboard both writing to `lean-db`), set `create_firestore_database = false` on the secondary and import the existing DB into its state.
- **`module.<svc>.service_account_email` outputs the bare email, not the IAM member string.** Prefix it with `serviceAccount:` when using in `google_pubsub_topic_iam_member`, `google_cloud_run_v2_service_iam_member`, etc. Easy gotcha; Terraform treats it as an invalid IAM member and the error message is buried at the bottom of `terraform plan`.
- **Cloud Build takes 3-5 min for first ADK image** (google-adk + cloud libs is ~3 GB). Use `gcloud builds submit --async` with `--format=json` to capture the build ID, then `gcloud builds describe <id> --format=json` to get the digest. Don't use `:latest`; tag with `dayN-rebuild` for traceability and read back the digest from `results.images[].digest`.
- **Bearer token auth (`CLOUDSDK_AUTH_ACCESS_TOKEN=...`) works for `gcloud run services describe`, `artifacts docker images list`, and `logging read`** but the token must be fresh. ADC alone (`gcloud auth application-default`) only works for some endpoints. The `gcloud-wrap.sh` script in `scripts/` re-prints the current token; source it before any `gcloud` call.
- **Day 2: `py_compile` passes even if a runtime name is undefined.** The poster stub callback reads from `callback_context.state` and returns a JSON-text Part that ADK stores under `output_key`. When Day 2's `/demo/run` started parsing those payloads with `json.loads(...)`, a missing `import json` slipped through `python3 -m py_compile` and only surfaced at runtime. Lesson: for code that does JSON I/O at request time, write a tiny smoke test (one curl) before declaring it done — local py_compile is necessary but not sufficient.
- **Day 2: ADK's `/run` returns tool payloads inside the poster event's `content.parts[].text`, NOT inside `actions.stateDelta`.** The poster's stub callback wraps the tool result in `{"tool_result": "write_lot_status", "payload": {...}}` as a JSON-encoded text Part. Parsing it requires walking the event stream and JSON-decoding each Part's text. Trying to surface `bus_message_id` via a state-delta write does not work because the stub skips the LLM call entirely.
- **Day 2.5: `output_schema` is the right knob for structured outputs.** Setting `output_schema=PydanticModel` on an LlmAgent: (a) auto-injects `response_schema` + `response_mime_type=application/json` into the LLM request, (b) validates the response against the Pydantic model, and (c) stores the **validated dict** in session.state via `actions.stateDelta[output_key]`. The previous "JSON-string-in-state" round-trip pattern is gone.
- **Day 2.5: with output_schema enforced, before_model_callback's "skip the LLM" pattern still works**, but the dict stored in state is the validated model output, not the raw text. Downstream nodes (matcher, poster) read from state directly with no `_parse_state` workaround. However, keep a defensive `_parse_state(v)` helper — the in-process /run stub callbacks can sometimes return the text-shape, and the cost of a 5-line safety net is negligible.
- **Day 2.5: gate live LLM calls behind a key-validity check, not just a presence check.** `GEMINI_API_KEY` is set in the environment either way, but the Day 0 bootstrap wrote `PLACEHOLDER-...` so the service could start. Calling Gemini with a placeholder burns quota and 401s. The agent module checks `not key.startswith("PLACEHOLDER")` before flipping to live mode, so the deploy is safe to ship and a real key flips behavior with no further deploy.
- **Day 2.5: wire real documents into /run as Parts, not as state.** `state_delta` is for control signals (lot_id, presence flags). Documents go on `new_message.parts`: text uploads become `[{text: "[LABEL]\n..."}, ...]`; non-text become `[{inline_data: {mime_type, display_name, data: base64}}, {text: "[LABEL attached as image/jpeg]"}]`. The per-agent instruction then says "Look at the [LABEL] part" and the model targets it. base64 is mandatory on the wire for inline_data.
- **Day 3: FastAPI's `Form(...)` / `File(...)` require `python-multipart`.** It is NOT pulled in by `pip install fastapi`. Adding it to the dashboard's pyproject was the fix for `RuntimeError: Form data requires "python-multipart" to be installed.` Lesson: any BFF that handles multipart uploads needs `python-multipart` in its dependencies, full stop.
- **Day 3: Firestore REST API requires a different auth scope than gcloud CLI.** The `gcloud` CLI's bearer token works for `gcloud firestore ...` commands but returns `403 PERMISSION_DENIED` for `firestore.googleapis.com/v1/...` REST queries. For Python smoke tests against Firestore, use the `google-cloud-firestore` SDK with ADC — same scope as the deployed services and no scope mismatch.
- **Day 3: Firestore `order_by("ts", direction=DESCENDING)` requires the field exists in every row.** Stale rows or rows that pre-date the field being added break the query. Use `firestore.Query.DESCENDING` only when you control all writers.
- **Day 3: leanview-consumer idempotency needs the write_id, not just the lot_id.** Pub/Sub can re-deliver the same message (network blips, ack timeouts). The consumer checks the stored `write_id` against the incoming one — if they differ AND the incoming `ts` is older, drop the replay. Same pattern Firestore uses for `set(..., merge=True)` on the system-of-record side.
- **Day 3: FastAPI + httpx for proxy file uploads.** `httpx.AsyncClient.post(..., files={...}, data={...})` correctly forwards multipart form-data including the file content. The `files` value is a 3-tuple `(filename, content_bytes, mime_type)`. The dashboard's `/api/run` uses this to forward the three uploads to adk-runtime without buffering through a temp file.
- **Day 3: nginx-on-Cloud-Run static assets require `chmod -R a+r`** because the alpine nginx image runs as the unprivileged `nginx` user. When you replace nginx with FastAPI, the asset-permission gotcha disappears but you still need to copy the public/ dir into the container image (the Dockerfile does `COPY apps/dashboard/public/ ./public/`).
- **Day 3: GCP project number vs project ID for service-account emails.** The dashboard SA is `lh-dashboard-runtime@serverless-503308.iam.gserviceaccount.com` — the project ID, not the number (`472857763269`). Using the wrong form returns `NOT_FOUND` from IAM lookups.
- **Day 3.5: `gcloud builds submit` reads `.gcloudignore`, not `.dockerignore`.** The first dashboard deploys shipped 526 MiB / 23k files into the build tarball (the entire repo including `.venv`, `node_modules`, etc.). Adding a `.gcloudignore` cut it to 368 KiB / 13 files. Note: `gcloud builds submit` has its OWN ignore rules and ignores `.dockerignore` — write both if you want symmetry.
- **Day 3.5: bash variable interpolation breaks when an f-string contains `*** `.** The shell redaction strips `*** ` plus the following token from file-write output. Build deploy scripts with `export CLOUDSDK_AUTH_ACCESS_TOKEN=*** + tk` (string concatenation, no f-string) and write the token to a file rather than interpolating into the script source. Same pattern for any string that has `*** ` adjacent to user data.
- **Day 3.5: Gemma `gemma-4-31b-it` is accessible via `models/` prefix on the Gemini API.** No Vertex AI setup required. Same auth (`GEMINI_API_KEY`) as Flash. `google-genai.Client` accepts it via `client.models.generate_content(model="models/gemma-4-31b-it", contents=prompt)`.
- **Day 3.5: ADK graph emits one event per agent traversal.** Day 3 graphs returned `events=11` (fan_out 3 + matcher + hold_loop 2 + critic_exit + poster + final-state = 11). Adding the summarizer bumped it to 12. Use event count as a sanity check when you change the graph topology.
- **Day 3.5: Cloud Build runs in parallel for different apps in the same project/region.** Kicking off adk-runtime + leanview-consumer + dashboard builds back-to-back is fine; they don't contend. The bottleneck is the 1-2 GB image download per build (Google ADK + google-genai is heavy).
- **Day 4: deploy scripts must use command substitution for the bearer token, never inline-prefix.** `export CLOUDSDK_AUTH_ACCESS_TOKEN=*** + tok` is exactly the pattern the redaction filter corrupts. Use `CLOUDSDK_AUTH_ACCESS_TOKEN=*** scripts/gcloud-wrap.sh")"` so the token is captured at runtime, never appears as a literal in the script source.
- **Day 4: idempotent smoke scripts need a polling loop.** User constraints are env-vars + flags + idempotent + no heredoc + no `>`/`<`/tee. A `while` poll for lean-db materialization is functional, not control-flow noise — it's the only way to wait deterministically without `sleep && curl`.
- **Day 4: dashboard `/api/run` is the only stable external entry point.** adk-runtime's `/demo/run` requires OIDC for production traffic; the dashboard BFF is the public ingress. Day 4 smoke drives everything through `/api/run` so the harness route (`/proof-of-action.html`) and the dashboard SPA both see the same data.

## Companion docs

- `../../hackathon/all-things-agentic/PLAN.md` — the build plan (source of truth).
- `../../hackathon/all-things-agentic/brainstorm.md` — the design rationale.
