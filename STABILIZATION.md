# Stabilization backlog

Baseline: `c445a64` (post-beta.5 development). Do not publish a new release until acceptance tests pass.

## Constraints

- Preserve Karpathy's UI and Amiable's native Council algorithm.
- Preserve Quick, Balanced, High and Reasoning profiles.
- No unauthorized paid calls in Free, including helpers and fallbacks.
- Do not treat mocked tests, catalogue membership or HTTP startup checks as proof of successful live inference.
- No new features until stabilization is complete.

## P0 — Free model availability and routing

- [ ] Reproduce Stage 1 HTTP 404 with the same account, using direct OpenRouter requests and the pinned Amiable engine independently. Record safe HTTP status and routing error categories; never record secrets or prompt bodies.
- [ ] Compare direct and native requests, including URL, model ID, provider restrictions, tools and timeout. Identify the actual root cause before declaring fixed.
- [ ] Discover concrete free text models from OpenRouter's model catalogue (prefer account-filtered catalogue). Require :free variant, explicit zero prompt/completion pricing, no nonzero request price, text input/output and suitable context. Exclude routers and paid models.
- [ ] Select three distinct models deterministically, preferring different model families/providers where metadata supports this. Context length is not a quality metric; define quality preference separately.
- [ ] Keep Free profile identity independent of selected model IDs; migrate old YAML safely. Keep native profile settings intact.
- [ ] Freeze selected models, chairman, helper model and routing configuration per Council run; prevent settings/key mutation during active runs.
- [ ] Apply bounded replacement/retry for provider-specific 404, 429, 5xx and timeouts only where native integration permits without changing review identity. Stop on account quota, auth or billing failures; do not assume every 429 is provider-specific.
- [ ] Keep title, memory and every auxiliary inference on verified free routes. Fail closed when zero-priced routing cannot be established.
- [ ] Show actual models and truthful partial/failure status in UI.
- [ ] Avoid automatic completion probes that consume free request quota; catalogue checks do not prove account inference availability.

## P1 — Council correctness and reliability

- [ ] Exclude failed/empty final answers from conversation memory; invalidate affected legacy summaries.
- [ ] Handle memory model availability without paid fallback.
- [ ] Distinguish zero reviews, partial results, failed synthesis and successful synthesis; correct quality metric inputs.
- [ ] Validate settings before persistence; keep effective and displayed chairman consistent.
- [ ] Consolidate REST/SSE common execution where necessary, without rewriting Amiable.
- [ ] Minimize monkey patches; test actual native stage boundaries and timeout behavior.
- [ ] Detect incomplete SSE streams, reconcile optimistic UI with stored state, and prevent overlapping writes to the same conversation.
- [ ] Make JSON writes atomic; isolate malformed files; validate conversation IDs consistently.
- [ ] Preserve useful historical error rendering; remove only demonstrably unused code.

## P1 — Tests and release

- [ ] Catalogue and error classification unit tests, including paid/malformed entries, insufficient free models and account limits.
- [ ] Exercise pinned native engine with a mocked HTTP transport (not a mocked run_full_council); inspect outbound model IDs and routing in every stage/helper.
- [ ] Direct opt-in live OpenRouter test with an actual user key; no keys in repository or logs.
- [ ] Browser end-to-end tests for Free/native switching, follow-up, memory, progress, error states and settings.
- [ ] Fix Windows smoke test so failed config assertions fail the job.
- [ ] Test clean installation and upgrade with old Free settings; verify readiness before claiming install success.
- [ ] Unify version sources and release gate; verify packaged EXE with a real Council round before publishing a new beta.

## Acceptance

Three distinct free model IDs selected from current catalogue when enough eligible models exist; genuine stage results from the pinned engine; no paid requests; bounded provider recovery; explicit failure for account limits or insufficient candidates; native profiles remain unchanged. Successful live run and clean/upgrade Windows test are required for release.

## References

- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints
- https://openrouter.ai/docs/guides/routing/provider-selection
