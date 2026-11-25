"""UI-10 fault injection: truncate only real ASGI SSE output, not engine data."""
import json
from pathlib import Path

MODE = "ui10-mode"
UPSTREAM = "ui10-upstream.sse"
DELIVERED = "ui10-delivered.sse"


def truncate_stream(app):
    async def wrapped(scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or not scope["path"].endswith("/message/stream")):
            await app(scope, receive, send)
            return
        mode = Path(MODE).read_text(encoding="utf-8")
        if mode not in ("intact", "eof", "truncated"):
            raise ValueError("UI10: unknown transport fault")
        cut = False

        async def boundary(message):
            nonlocal cut
            if message["type"] != "http.response.body":
                await send(message)
                return
            body = message.get("body", b"")
            with Path(UPSTREAM).open("ab") as handle:
                handle.write(body)
            if mode != "intact" and body and not cut:
                if body.startswith(b"data: ") and json.loads(body[6:])["type"] == "complete":
                    cut = True
                    if mode == "truncated":
                        # Actual prefix of the terminal frame, never invented SSE.
                        prefix = body[:len(b'data: {"type": "complete"')]
                        if not prefix.startswith(b"data: ") or prefix.endswith(b"\n\n"):
                            raise ValueError("UI10: invalid truncation boundary")
                        with Path(DELIVERED).open("ab") as handle:
                            handle.write(prefix)
                        await send({**message, "body": prefix})
                    return
            if cut and body:
                return
            with Path(DELIVERED).open("ab") as handle:
                handle.write(body)
            await send(message)

        await app(scope, receive, boundary)

    return wrapped
