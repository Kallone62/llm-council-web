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
- **Quick / Balanced / High / Reasoning** model profiles
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
3. run `npm ci` if frontend dependencies are missing;
4. start the backend on `http://localhost:8001`;
5. start the frontend on `http://localhost:5173`;
6. open the app in your browser.

On first use, open **Settings** and save your OpenRouter API key.

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
npm ci
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

### Default High profile in this release

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
App:    v1.0.0
Engine: llm-council-core==0.53.0
```

If the app proves useful and an upstream update becomes worthwhile, the preferred process is to integrate it manually, retest the web compatibility layer, and publish a new release.

## Security

- `.env` is gitignored.
- The existing OpenRouter API key is never returned to the browser.
- The Settings UI only reports whether a key is configured.
- Do not commit API keys or paste them into issues/screenshots.

## Project status

This is intentionally a small community integration rather than a new framework. The priority is a simple, stable way to use the modern council engine through a familiar local web UI.
