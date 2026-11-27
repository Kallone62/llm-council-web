"""Passively capture actual DELETE ASGI responses, including bodyless 204s."""
import json
import os
import sys
from pathlib import Path

from serve import serve

CAPTURE = "ui12-delete-responses.jsonl"


def observe_delete(app):
    async def observed(scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "DELETE"
                or not scope["path"].startswith("/api/conversations/")):
            await app(scope, receive, send)
            return
        status = None
        body = bytearray()

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    with Path(CAPTURE).open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"path": scope["path"], "status": status,
                                                 "body_hex": body.hex()}) + "\n")
            await send(message)

        await app(scope, receive, observed_send)

    return observed


if __name__ == "__main__":
    if not os.environ.get("LLM_COUNCIL_UI_DATA_DIR") or not os.environ.get("LLM_COUNCIL_CONFIG"):
        raise RuntimeError("Run the isolated browser harness, not the server directly")
    serve(sys.argv[1], wrap_app=observe_delete)
