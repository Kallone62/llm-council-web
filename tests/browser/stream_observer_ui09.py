from pathlib import Path

STREAM_CAPTURE = "ui09-stream.sse"


def observe_stream(app):
    """Record actual ASGI SSE bytes without altering response or disconnects."""
    async def observed(scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or not scope["path"].endswith("/message/stream")):
            await app(scope, receive, send)
            return
        capture = None

        async def observed_send(message):
            nonlocal capture
            if message["type"] == "http.response.start":
                content_type = dict(message.get("headers", [])).get(b"content-type", b"")
                if content_type.startswith(b"text/event-stream"):
                    capture = Path(STREAM_CAPTURE)
                    with capture.open("xb"):
                        pass
            elif message["type"] == "http.response.body" and capture is not None:
                with capture.open("ab") as handle:
                    handle.write(message.get("body", b""))
            await send(message)

        await app(scope, receive, observed_send)

    return observed
