# LLM Council Web v1.1.0 Beta 3 — Windows x64

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

## Fixed in Beta 3 — Free Chairman rate limits

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

## First run

Open Settings, add your **own OpenRouter API key**, then select a model profile. The application does not provide a cloud API key or unlimited free usage. OpenRouter and third-party model providers may have restrictions, logging policies, and charges depending on your settings.

## Beta notes and limitations

This is an **unofficial community integration**, not an official Karpathy, Amiable, or OpenRouter product. Python/React CI and Windows EXE startup checks passed; not all real-provider model/mode combinations have been tested. Existing user data from separately downloaded or BAT versions is **not automatically imported** when running the ZIP from a new folder. Keep backups of `.env`, `llm_council.yaml`, and `data/`.

**Licensing caveat:** The original `karpathy/llm-council` repository does not publish a clear license. Attribution is provided in `NOTICE.md`, but that does not resolve third-party redistribution rights. No MIT license or upstream endorsement is claimed for Karpathy's code.

SHA256 checksum is provided as a second release asset.
