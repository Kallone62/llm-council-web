"""UI-10: real native Council, controlled HTTP provider and SSE transport fault."""
import os
import sys

from serve import serve
from serve_ui04 import controlled_provider
from stream_cut_ui10 import truncate_stream


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], async_transport=controlled_provider, wrap_app=truncate_stream)
