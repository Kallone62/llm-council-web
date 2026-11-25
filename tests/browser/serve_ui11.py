"""UI-11: reuse native UI07 HTTP fixture with explicitly selected history inputs."""
import json
import os
import sys
from pathlib import Path

from serve import serve
from serve_ui07 import controlled_ui07_provider

PLAN = "ui11-provider-plan.json"


async def history_provider(transport, request):
    plan = json.loads(Path(PLAN).read_text(encoding="utf-8"))
    return await controlled_ui07_provider(transport, request, question=plan["query"], title=plan["title"])


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=history_provider)
