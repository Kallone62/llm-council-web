"""UI-11: prove persisted history, corrupt-neighbour and reload oracles fail."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation

STORAGE = "backend/storage.py"
APPEND = '    conversation["messages"].append(message)'
LOSE_HISTORY = '    conversation["messages"] = [message]'
REGRESSION = "tests/test_storage_integrity.py::test_successful_write_preserves_full_round_and_legacy_default_title"


def verify_storage_regression():
    path = ROOT / STORAGE
    original = path.read_bytes()
    source = original.decode("utf-8")
    assert source.count(APPEND) == 1
    command = [sys.executable, "-m", "pytest", "-q", REGRESSION]
    try:
        path.write_text(source.replace(APPEND, LOSE_HISTORY), encoding="utf-8")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        log = result.stdout + result.stderr
        print(log, flush=True)
        assert result.returncode == 1 and "1 failed" in log and REGRESSION in log
        assert 'assert storage.list_conversations()[0]["message_count"] == 2' in log
        assert "AssertionError" in log
        print("UI11 negative control detected: lost-history-storage-regression", flush=True)
    finally:
        path.write_bytes(original)
    assert path.read_bytes() == original
    subprocess.run(command, cwd=ROOT, check=True, timeout=60)


def main():
    for name, path, before, after, marker, rebuild in (
        ("lost-history", STORAGE, APPEND, LOSE_HISTORY,
         "UI11: persisted history mismatch", False),
        ("deleted-corrupt-neighbour", STORAGE,
         '                logger.warning("Skipping unreadable conversation %s (%s)", filename, type(exc).__name__)',
         '                os.unlink(path)\n                logger.warning("Skipping unreadable conversation %s (%s)", filename, type(exc).__name__)',
         "UI11: read changed or lost stored bytes", False),
        ("truncated-loaded-history", "frontend/src/App.jsx", "        setCurrentConversation(conv);",
         "        setCurrentConversation({...conv, messages: conv.messages.slice(-2)});",
         "UI11: historical questions missing or wrong", True),
        ("hidden-partial-warning", "frontend/src/components/Stage2.jsx", "{notice && <p", "{false && notice && <p",
         "UI11: historical partial warning missing", True),
        ("failed-as-success", "frontend/src/components/Stage3.jsx",
         '{!failed && (\n          <div className="final-text markdown-content">',
         '{true && (\n          <div className="final-text markdown-content">',
         "UI11: historical failure shown as success", True),
    ):
        verify_mutation(name, path, before, after, marker, rebuild=rebuild, script="ui11.py", run_var="UI11_RUN")
    verify_storage_regression()
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui11.py"))], cwd=ROOT,
                   env={**os.environ, "UI11_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
