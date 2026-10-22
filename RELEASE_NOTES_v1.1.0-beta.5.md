# LLM Council Web v1.1.0 Beta 5 — Windows x64

**Download `LLMCouncilWeb-Windows-x64.zip` under Assets**, extract the entire ZIP, and double-click `LLMCouncilWeb.exe`. Keep the `_internal` folder beside the EXE. Windows may warn because this beta is unsigned. No Python, Node.js, Git, or BAT file is required.

## Included

- Original Karpathy-style React frontend connected to Amiable `llm-council-core==0.53.0`.
- Three-stage Council, rankings, structured verdicts, and follow-up conversations with AI memory.
- Free OpenRouter preset (requires a personal API key; free quotas and model availability apply), plus Quick / Balanced / High / Reasoning.
- Free tier notice describing advanced-feature limitations and potential charges in paid profiles.
- Custom LLM Council logo in EXE, browser, and Windows system tray.
- Background launch without CMD; tray menu offers Open and Quit; local server only on 127.0.0.1.
- Optional pinned PowerShell installer under `scripts/install.ps1`.

## Fixed in Beta 2 — Independent Free profile

Free is an **additional fifth application-level profile**, not a replacement
or alias of Quick. Selecting Free applies its own OpenRouter `:free` models
and Chairman without modifying the existing native Quick, Balanced, High or
Reasoning tier. Returning to Quick restores Amiable's normal Quick pool.

Amiable's pinned engine accepts four native tier names. The Free preset
therefore leaves the native tier unchanged for engine timeout calculations,
rather than forcing the tier to `quick`. Existing beta.1 settings and chat
history remain usable, but launching from a new extracted folder does not
automatically migrate them.

## Fixed in Beta 5 — Free Chairman rate limits

If the primary Free Chairman hits OpenRouter's `rate_limited` response at
Stage 3, the app automatically tries the other two configured zero-priced
OpenRouter models as alternate Chairmen. It reruns only Amiable's native Stage 3
synthesis, **not Stage 1/2**, so earlier answers and rankings are retained.
Progress reports the actual alternate model; the successful model is recorded
as Chairman. If all free models are rate-limited, a visible error explains the
limit instead of displaying an error as a green final answer.

This does not bypass **account-wide daily quotas**, provider outages, or
model availability limits. At most two extra free requests are attempted.
The Quick/Balanced/High/Reasoning pools and model routing are not changed,
and no paid-model fallback is enabled in Free.

## Fixed in Beta 5 — Qwen 404 after Gemma rate limit

OpenRouter may return an HTTP 404 or 5xx provider error when an available
free model cannot serve a particular request. After Gemma's 429 rate limit,
the beta.3 retry stopped when Qwen returned a 404, preventing the third Free
model (Nemotron) from trying.

Beta.4 treats **429, HTTP 404, and 5xx** as eligible for bounded Stage 3
recovery. It retries only native Amiable Stage 3, never repeats the expensive
individual responses or reviews, and uses only the three fixed `:free` models.
Authentication/billing/invalid-request errors do not trigger model rotation.
All attempts failing is explicitly marked as a failure; older saved Stage 3
error text is no longer rendered as a successful green answer.

A published model slug does not guarantee a provider can serve every request.
Account-wide daily free usage limits still apply. This improvement is tested
with simulated failures; it cannot promise that every Free run will finish.

## Fixed in Beta 5 — zero Stage 1 responses

If every Council model fails before generating an initial answer, Amiable's
native engine returns an error placeholder without an `error_status`, and the
old frontend displayed that placeholder as a green Stage 3 final answer.

Beta.5 **marks Stage 1 as failed** when it returns zero responses, stores
an explicit `no_responses` status, and shows a clear failure notice instead
of pretending that a Chairman synthesis happened. For a running request,
the app captures each model's native structured result status (such as
`rate_limited`, `auth_error`, HTTP 404, or timeout) **without making any extra
API calls**. Only safe model identifiers, statuses, HTTP code and bounded
Retry-After values appear in the UI; raw provider error strings and API
credentials are not persisted in the conversation.

Previously saved `model: error` placeholders are also shown as failures,
though historical attempts cannot recover model error details retroactively.

This is a presentation/diagnostics fix, not a guaranteed solution to
OpenRouter's rate limits or unavailable free providers. The native Amiable
Council algorithm, all five UI profiles, and three-stage successful runs
remain unchanged.

## First run

Open Settings, add your **own OpenRouter API key**, then select a model profile. The application does not provide a cloud API key or unlimited free usage. OpenRouter and third-party model providers may have restrictions, logging policies, and charges depending on your settings.

## Beta notes and limitations

This is an **unofficial community integration**, not an official Karpathy, Amiable, or OpenRouter product. Python/React CI and Windows EXE startup checks passed; not all real-provider model/mode combinations have been tested. Existing user data from separately downloaded or BAT versions is **not automatically imported** when running the ZIP from a new folder. Keep backups of `.env`, `llm_council.yaml`, and `data/`.

**Licensing caveat:** The original `karpathy/llm-council` repository does not publish a clear license. Attribution is provided in `NOTICE.md`, but that does not resolve third-party redistribution rights. No MIT license or upstream endorsement is claimed for Karpathy's code.

SHA256 checksum is provided as a second release asset.
