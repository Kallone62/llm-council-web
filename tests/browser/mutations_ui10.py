"""UI-10: mutation controls for the real terminal contract and browser state."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation

BACKEND = "backend/main.py"
COMPLETE = '                await queue.put({"type": "complete", "metadata": metadata})\n'
NO_COMPLETE = "                # Mutation: omit terminal complete.\n"
REGRESSION = "tests/test_backend_integration.py::test_sse_is_only_an_envelope_around_full_engine_result"


def verify_backend_terminal():
    path = ROOT / BACKEND
    original = path.read_bytes()
    source = original.decode("utf-8")
    assert source.count(COMPLETE) == 1
    command = [sys.executable, "-m", "pytest", "-q", REGRESSION]
    try:
        path.write_text(source.replace(COMPLETE, NO_COMPLETE), encoding="utf-8")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        log = result.stdout + result.stderr
        print(log, flush=True)
        assert result.returncode == 1 and "1 failed" in log and REGRESSION in log
        assert 'assert [event["type"] for event in events] == [' in log
        assert "AssertionError" in log and "title_complete" in log
        print("UI10 negative control detected: missing-complete-backend-regression", flush=True)
    finally:
        path.write_bytes(original)
    assert path.read_bytes() == original
    subprocess.run(command, cwd=ROOT, check=True, timeout=60)


def main():
    for name, path, before, after, marker, rebuild in (
        ("missing-complete", BACKEND, COMPLETE, NO_COMPLETE,
         "UI10: intact stream did not complete", False),
        ("premature-final", "frontend/src/api.js",
         "if (type === 'stage3_complete') finalResult = event;",
         "if (type === 'stage3_complete') { finalResult = event; onEvent(type, event); }",
         "UI10: final shown before terminal", True),
        ("stuck-loading", "frontend/src/App.jsx", "      setIsLoading(false);", "      setIsLoading(true);",
         "UI10: loading did not end", True),
        ("false-reload", "frontend/src/App.jsx",
         "if (completed && activeConversationIdRef.current === sendConversationId)",
         "if (activeConversationIdRef.current === sendConversationId)",
         "UI10: invalid stream caused success reload", True),
    ):
        verify_mutation(name, path, before, after, marker, rebuild=rebuild, script="ui10.py", run_var="UI10_RUN")
    verify_backend_terminal()
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui10.py"))], cwd=ROOT,
                   env={**os.environ, "UI10_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
