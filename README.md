# LLM Council Web

**Karpathy's LLM Council experience, modernized with Amiable's current council engine.**

A local-first web app for people who want to try and use the LLM Council concept without needing a proprietary council subscription, a CLI workflow, or application-development experience.

> **Unofficial community integration.** This project is not an official release from Andrej Karpathy or amiable-dev. It combines the original Karpathy web experience with the pinned `amiable-dev/llm-council` engine package (`llm-council-core==0.53.0`).

## Why this exists

There are two excellent open-source projects with different strengths:

- **[karpathy/llm-council](https://github.com/karpathy/llm-council)** made the LLM Council idea easy to understand through a simple ChatGPT-like local web app.
- **[amiable-dev/llm-council](https://github.com/amiable-dev/llm-council)** evolved the council engine with richer deliberation, ranking, verdict, evaluation, cost and reliability features, but is primarily packaged for developer-oriented workflows such as Python, CLI, MCP and HTTP APIs.

This project bridges those two worlds.

It is aimed at people who:

- want to experiment with the **LLM Council** concept without learning a CLI or writing code;
- like the simplicity of Karpathy's original interface but want a more modern decision engine;
- do not want to depend on a paid proprietary "AI council" subscription;
- want to bring their own OpenRouter key and choose the models that make up the council;
- want to see independent answers, peer review, ranking, disagreement and a final synthesized decision in one interface.

**Important:** the app itself is local and open-source, but selected cloud models may charge API usage through OpenRouter. It is not accurate to describe every run as free.

## What happens when you ask a question

1. **Stage 1 — Independent opinions**  
   Multiple configured models answer the question independently.
2. **Stage 2 — Peer review and ranking**  
   Amiable's engine anonymizes responses, has council members review them, and aggregates rankings using its modern ranking/Borda pipeline.
3. **Stage 3 — Chairman / verdict**  
   A Chairman model synthesizes the result or produces a structured Jury Mode verdict.

The app keeps Amiable's richer metadata instead of reducing it to the original schema, including aggregate rankings, Borda data, usage/cost metadata, verdicts, dissent and enabled evaluation results.

## Highlights

- Familiar **Karpathy-style web interface**
- Direct use of Amiable's `run_full_council()` engine — no duplicate council algorithm
- **Free / Quick / Balanced / High / Reasoning** model profiles
- Custom council model selection and custom OpenRouter model IDs
- **Auto or manual Chairman** selection
- Consensus / debate behavior controls
- Self-vote exclusion and style normalization
- Optional rubric scoring, bias audit and safety gate
- Synthesis / Binary Decision / Tie-breaker verdict modes
- Optional dissent visibility
- Local conversation storage
- OpenRouter key management from Settings
- **One-click Windows launcher** and optional desktop shortcut
- Pinned `llm-council-core==0.53.0`; no automatic upstream updater

## Application icon

The original user-provided vector logo is the **single source of truth** at
[`frontend/public/llm-council.svg`](frontend/public/llm-council.svg).
It appears as the browser favicon and in the original Karpathy-style sidebar.
The Windows packaging workflow rasterizes that SVG without redesigning it and
uses its output for a multi-resolution EXE/desktop shortcut `.ico` and
the system tray icon. Windows assets are automatically created during CI;
there is no separate logo to maintain.

## Windows desktop beta (recommended for non-developers)

**Windows beta release:** [v1.1.0-beta.4](https://github.com/Kallone62/llm-council-web/releases/tag/v1.1.0-beta.4) — download the Windows ZIP under **Assets**. See [Release notes](RELEASE_NOTES_v1.1.0-beta.4.md) for supported features, known limitations and third-party licensing caveats.

Windows x64 users can download `LLMCouncilWeb-Windows-x64.zip`, **extract the
entire folder**, and double-click `LLMCouncilWeb.exe`. Keep the accompanying
`_internal` folder and YAML next to the executable. The release bundles its
own Python runtime, the Amiable engine and the production React UI. **No Git,
Node.js, uv, Python or BAT scripts are needed to run the desktop release.**
It opens the local browser at `http://localhost:8001/app/` **without
showing a CMD or terminal window**. While running, the app shows an icon in
the **Windows notification area (system tray)**, sometimes behind the ^ arrow.
Right-click it to choose **Open LLM Council** or **Quit LLM Council**.
Closing the browser tab does not stop the local server: use **Quit LLM Council**
in the tray to shut it down. If the app fails to start, a Windows message box
reports the error and logs are written under
`%LOCALAPPDATA%\\LLMCouncilWeb\\logs`.

The initial ZIP and one-command PowerShell installer are still separate ways
to distribute the same GUI EXE; the PowerShell install window appears only
during installation.

For an optional one-command installation, paste the following into Windows PowerShell to download the
version-pinned GitHub Release ZIP, verify its SHA256 digest, install it to your
per-user AppData folder, create a desktop shortcut, and launch it:

```powershell
irm https://raw.githubusercontent.com/Kallone62/llm-council-web/v1.1.0-beta.4/scripts/install.ps1 | iex
```

**Security:** Piping a downloaded script into `iex` executes its contents.
Before running it, inspect the exact [installer source](scripts/install.ps1)
on GitHub. The install script downloads only from the tagged GitHub Release,
checks the matching SHA256 asset, and does not request administrator privileges.
Windows SmartScreen may warn about the currently **unsigned** community beta.

The installer **preserves existing .env, llm_council.yaml and data/** from its
own previous desktop installation. It deliberately does **not** overwrite or
import files from a separate Git-cloned/.bat-based developer checkout; those
remain separate unless manually migrated. The local backend binds to
`127.0.0.1` only; it is not a public hosted service.

The EXE is a packaging layer, not a rewrite of Karpathy's frontend or Amiable's
engine. You still need your own OpenRouter API key (set it in **Settings**), and
model requests may incur OpenRouter charges. Start with the **Free** profile
if you want to try zero-priced OpenRouter endpoints within rate limits. The automated Windows smoke test does not make paid
model calls. This is a **beta**, not a claim that all provider/model combinations
have been exhaustively tested. See [NOTICE.md](NOTICE.md) for third-party
attribution and licensing limitations.

## Developer checkout (existing method)

## One-click Windows start

### First run

Install these once:

- [Node.js LTS](https://nodejs.org/)
- [`uv`](https://docs.astral.sh/uv/)

Then double-click:

```text
START_LLM_COUNCIL.bat
```

The launcher will automatically:

1. create a local `.env` if needed;
2. run `uv sync` if the Python environment is missing;
3. run `npm install` if frontend dependencies are missing;
4. start the backend on `http://localhost:8001`;
5. start the frontend on `http://localhost:5173`;
6. open the app in your browser.

On first use, open **Settings** and save your OpenRouter API key. The first-install default is **Free**; no payment is needed for available free model requests within OpenRouter's rate limits.

To stop the app:

```text
STOP_LLM_COUNCIL.bat
```

To create a desktop shortcut once:

```text
CREATE_DESKTOP_SHORTCUT.bat
```

## Manual start

```bash
uv sync
cd frontend
npm install
cd ..
```

Backend:

```bash
uv run python -m backend.main
```

Frontend in a second terminal:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`.

## Free profile (zero-priced OpenRouter endpoints)

New installations start with the **Free** preset, without installing Ollama.
The app still **requires the user to create and paste their own OpenRouter API
key in Settings**. OpenRouter free-model endpoints have no prompt/completion
token charge at the time of this release, but use limited quotas and can be
rate-limited, unavailable, or withdrawn. Do not promise unlimited free usage.
Provider privacy policies differ; avoid sending sensitive data to free models.

Free is a **fifth, independent application-level profile**, not an alias
of Quick. Amiable's pinned engine validates only four native tiers, so the
Free model pool and Chairman are kept separate from those native pools.
Selecting Free **does not change the current native tier** (which the engine
still uses for timeouts); selecting Quick restores Amiable's original Quick
model pool. No upstream engine code is patched. The exact preset is:
- Council: `google/gemma-4-31b-it:free`,
  `qwen/qwen3.8-27b:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`
- Chairman and conversation-memory summarizer:
  `google/gemma-4-31b-it:free`
- Conversation titles: deterministic local text (no extra LLM call)
- Native style normalization and optional evaluation disabled; model and
  Chairman are fixed. OpenRouter gateway failover is disabled to avoid
  rerouting free requests to a possibly paid provider.

Requests stop if effective engine configuration overrides would break the
Free preset, rather than silently falling back to a paid model. Existing
saved setups are not migrated automatically; selecting Free in Settings
changes the chosen profile. Changing back to paid profiles restores the
corresponding native Amiable model pool.

**Free Chairman provider-error recovery (beta.4):** if the primary Free Chairman
returns a structured OpenRouter `rate_limited` error or a provider 404/5xx,
the app retries **only Stage 3** using the other fixed `:free` models (at most
two extra attempts).
Stage 1 and peer-review are not repeated and Quick/other paid profiles remain
unchanged. An exhausted account-wide quota or a temporarily unavailable free
provider can still prevent a final synthesis; the failure is displayed clearly,
with the completed earlier stages kept in conversation history. No automatic
paid-model fallback or guarantee of uninterrupted free service.

A full Council run makes multiple requests, so free daily quotas can be
used quickly. For currently available models and limits see
https://openrouter.ai/collections/free-models and
https://openrouter.ai/pricing .

## Follow-up conversations

After the first Council result, the message composer stays available in the
same chat. Follow-up questions start a new full Amiable Council round.

To preserve the meaning of previous decisions **without chopping answers off
mid-sentence**, conversation context is prepared as follows:

- Short histories: pass full previous user questions and Chairman final answers
  unchanged (no summarization call).
- Long histories (more than 6,000 characters): use the existing Amiable
  OpenRouter `query_model()` client with `google/gemini-2.5-flash` to generate
  a compact decision-oriented memory (maximum 4,000 characters).
- Store the AI summary in the same conversation JSON under `context_memory`
  with the number of completed rounds it covers, then update it incrementally
  only when the context grows too large again.
- Do not resubmit raw Stage 1/2 peer reviews, and do not rewrite or truncate
  the full saved conversation history.
- If the memory model fails or responds with unusable/oversized text, return
  an error **before** starting another expensive Council round rather than
  silently deleting relevant context.

An AI summary is a *lossy, fallible interpretation*, not guaranteed perfect
recall. For important decisions, refer to the full earlier answers retained in
the local JSON. Summarization incurs a separate OpenRouter model call when needed. In Free
mode it uses the free Chairman endpoint; in other profiles it may have a
charge, in addition to any new three-stage Council run. OpenRouter
cost metadata from the full Council run does not automatically include the
separate memory-summarization charge.

## Settings

The Settings screen writes the active Amiable config at:

```text
llm_council.yaml
```

Normal settings changes hot-reload the engine, so a server restart is usually unnecessary.

Secrets stay separate:

```text
.env               -> API keys and local secrets (gitignored)
llm_council.yaml   -> models, chairman, council/evaluation/cache settings
```

The UI intentionally exposes the settings that are most useful to non-developer users. Advanced Amiable options can still be edited directly in `llm_council.yaml`.

### High profile models (when selected)

- `openai/gpt-5.6-sol`
- `anthropic/claude-opus-5`
- `deepseek/deepseek-v4-pro-0813`
- `z-ai/glm-5.3`

With Chairman set to **Auto**, the High profile resolves to Amiable's profile Chairman for the pinned engine release.

## Per-question decision modes

The controls beneath the message box are deliberately separate from global Settings:

- **Synthesis** — free-form council synthesis
- **Binary decision** — structured approved/rejected-style decision with confidence and rationale
- **Tie-breaker** — Chairman resolves a deadlocked choice
- **Include dissent** — preserves minority reasoning when available

These are passed into Amiable's decision engine rather than reimplemented in the web layer.

## Architecture

```text
Karpathy-style React UI
        │
        │ REST / SSE compatibility envelope
        ▼
backend/main.py
        │
        │ direct run_full_council(...)
        ▼
Amiable llm-council-core
        │
        ▼
OpenRouter / selected models
```

There is no separate council adapter service and no second implementation of the decision algorithm.

## Why multi-model deliberation can be useful

This project does **not** claim that a council is always correct or that multiple models eliminate hallucinations. Multiple models can share the same blind spot. The useful idea is that independent generation, peer review, ranking and explicit dissent can expose disagreement and reduce dependence on a single model response.

Related research directions include:

- **Du et al. (2023), _Improving Factuality and Reasoning in Language Models through Multiagent Debate_** — explores multiple model instances proposing and debating answers.  
  https://arxiv.org/abs/2305.14325
- **Wang et al. (2022), _Self-Consistency Improves Chain of Thought Reasoning in Language Models_** — shows benefits from aggregating diverse reasoning paths rather than relying on a single generation.  
  https://arxiv.org/abs/2203.11171
- **Wang et al. (2023), _Large Language Models are not Fair Evaluators_** — documents position/evaluation bias in LLM-as-a-judge setups, motivating care around ranking and evaluation design.  
  https://arxiv.org/abs/2305.17926

These papers are background context, not evidence that this specific integration outperforms every single-model workflow.

## Upstream and attribution

This integration builds on:

- **Andrej Karpathy — `karpathy/llm-council`**  
  https://github.com/karpathy/llm-council
- **amiable-dev — `amiable-dev/llm-council`**  
  https://github.com/amiable-dev/llm-council

The Amiable engine is installed as the exact PyPI dependency `llm-council-core==0.53.0`, which is published under the MIT License. See [`NOTICE.md`](NOTICE.md) for attribution details.

## Version and update policy

This repository intentionally pins an **exact engine release** rather than automatically tracking upstream changes.

```text
App:    v1.1.0-beta.4
Engine: llm-council-core==0.53.0
```

If the app proves useful and an upstream update becomes worthwhile, the preferred process is to integrate it manually, retest the web compatibility layer, and publish a new release.

## Security

- The backend listens only on `127.0.0.1:8001`; it is not a remotely hosted or authenticated API. Do not expose it through a port-forward, public reverse proxy, or `0.0.0.0` binding without adding access controls.
- `.env` is gitignored.
- The existing OpenRouter API key is never returned to the browser.
- The Settings UI only reports whether a key is configured.
- Do not commit API keys or paste them into issues/screenshots.

## Project status

This is intentionally a small community integration rather than a new framework. The priority is a simple, stable way to use the modern council engine through a familiar local web UI.
