"""Run the exact UI-01 acceptance against two temporary production mutations."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build():
    subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend",
                   env={**os.environ, "LLM_COUNCIL_DESKTOP_BUILD": "1"}, check=True)


def verify_mutation(name, file, before, after, expected, rebuild=False):
    path = ROOT / file
    original = path.read_bytes()
    source = original.decode("utf-8")
    assert source.count(before) == 1, f"Mutation anchor changed: {name}"
    try:
        path.write_text(source.replace(before, after), encoding="utf-8")
        if rebuild:
            build()
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("ui01.py"))],
                                cwd=ROOT, env={**os.environ, "UI01_RUN": name},
                                capture_output=True, text=True, timeout=120)
        print(result.stdout + result.stderr, flush=True)
        assert result.returncode != 0 and expected in result.stderr, f"Mutation not detected at intended assertion: {name}"
        print(f"UI01 negative control detected: {name}", flush=True)
    finally:
        path.write_bytes(original)
        if rebuild:
            build()
    assert path.read_bytes() == original


def main():
    verify_mutation(
        "missing-sidebar", "frontend/src/App.jsx",
        """setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);""",
        "setConversations(conversations);",
        "UI01: new sidebar conversation missing", rebuild=True,
    )
    verify_mutation(
        "missing-persistence", "backend/storage.py",
        "    save_conversation(conversation)\n\n    return conversation",
        "    # Mutation: return an unsaved conversation.\n\n    return conversation",
        "UI01: new conversation was not persisted",
    )
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui01.py"))],
                   cwd=ROOT, env={**os.environ, "UI01_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
