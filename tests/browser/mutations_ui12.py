"""UI-12: mutation controls for deletion, UI state and storage isolation."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation

STORAGE = "backend/storage.py"
UNLINK = "        path.unlink()\n        return True"
NO_UNLINK = "        if not path.exists():\n            return False\n        return True"
WRONG_TARGET = ("        if path.exists():\n"
                "            path = next(p for p in Path(DATA_DIR).glob('*.json') if p != path)\n"
                "        path.unlink()\n        return True")
REGRESSION = "tests/test_backend_integration.py::test_delete_conversation_removes_only_selected_json"


def verify_backend_regression(name, replacement):
    path = ROOT / STORAGE
    original = path.read_bytes()
    source = original.decode("utf-8")
    assert source.count(UNLINK) == 1
    command = [sys.executable, "-m", "pytest", "-q", REGRESSION]
    try:
        path.write_text(source.replace(UNLINK, replacement), encoding="utf-8")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        log = result.stdout + result.stderr
        print(log, flush=True)
        assert result.returncode == 1 and "1 failed" in log and REGRESSION in log
        assert "assert not first_file.exists()" in log and "AssertionError" in log
        print(f"UI12 negative control detected: {name}-backend-regression", flush=True)
    finally:
        path.write_bytes(original)
    assert path.read_bytes() == original
    subprocess.run(command, cwd=ROOT, check=True, timeout=60)


def main():
    for name, path, before, after, marker, rebuild in (
        ("cancel-ignored", "frontend/src/App.jsx",
         '    if (!window.confirm(`Delete "${title}" permanently? This cannot be undone.`)) return;',
         '    window.confirm(`Delete "${title}" permanently? This cannot be undone.`);',
         "UI12: cancelled confirmation sent DELETE", True),
        ("hidden-delete-error", "frontend/src/App.jsx",
         "      setDeleteError(error.message || 'Could not delete conversation.');",
         "      setDeleteError('');",
         "UI12: deletion error not shown", True),
        ("no-unlink", STORAGE, UNLINK, NO_UNLINK,
         "UI12: target still on disk", False),
        ("wrong-target", STORAGE, UNLINK, WRONG_TARGET,
         "UI12: another conversation was deleted or changed", False),
        ("stale-sidebar", "frontend/src/App.jsx",
         "      setConversations((prev) => prev.filter((item) => item.id !== id));",
         "      setConversations((prev) => prev);",
         "UI12: deleted conversation still in sidebar", True),
        ("stale-active-chat", "frontend/src/App.jsx",
         "        activeConversationIdRef.current = null;\n        setCurrentConversationId(null);\n        setCurrentConversation(null);",
         "        activeConversationIdRef.current = null;\n        setCurrentConversationId(null);",
         "UI12: deleted active chat remained open", True),
    ):
        verify_mutation(name, path, before, after, marker, rebuild=rebuild, script="ui12.py", run_var="UI12_RUN")
    verify_backend_regression("no-unlink", NO_UNLINK)
    verify_backend_regression("wrong-target", WRONG_TARGET)
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui12.py"))], cwd=ROOT,
                   env={**os.environ, "UI12_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
