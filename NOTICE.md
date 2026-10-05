# Attribution and third-party notice

This repository is an **unofficial community integration** and is not an official release of either upstream project.

## Karpathy LLM Council

The web application, interaction model, and original LLM Council concept used as the UI/application base originate from:

- Andrej Karpathy — `karpathy/llm-council`
- https://github.com/karpathy/llm-council

The upstream repository did not include a root `LICENSE` file in the version reviewed for this integration. This repository therefore does **not** claim that Karpathy's code is MIT-licensed and does not attempt to relicense third-party code.

## Amiable LLM Council

The modern council engine comes from:

- amiable-dev — `amiable-dev/llm-council`
- https://github.com/amiable-dev/llm-council
- PyPI package: `llm-council-core==0.53.0`

Amiable publishes `llm-council-core` under the MIT License. This repository pins that exact package release instead of vendoring or automatically updating the engine source.

## Integration code

This repository adds the direct Karpathy-backend-to-Amiable-engine integration, compatibility handling, Settings UI, local configuration management, Windows launch/stop helpers, and related tests/documentation.

No affiliation with or endorsement by Andrej Karpathy, amiable-dev, OpenRouter, or the model providers is implied.
