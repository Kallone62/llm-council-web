"""Require UI-04 to reject real live-progress, storage, loading and display regressions."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation


def main():
    for name, path, before, after, expected, rebuild in (
        ("missing-live-progress", "frontend/src/App.jsx",
         "lastMsg.engineProgress = [...(lastMsg.engineProgress || []), event.data];",
         "lastMsg.engineProgress = [];", "UI04: live stage1 progress missing", True),
        ("missing-round-persistence", "backend/main.py",
         """                storage.add_assistant_message(
                    conversation_id, stage1, stage2, stage3, metadata
                )""",
         "                # Mutation: omit persisted assistant result.", "UI04: persisted result mismatch", False),
        ("stuck-loading", "frontend/src/App.jsx",
         "      setIsLoading(false);", "      setIsLoading(true);", "UI04: loading did not end", True),
        ("missing-final-answer", "frontend/src/components/Stage3.jsx",
         "<ReactMarkdown>{finalResponse.response}</ReactMarkdown>",
         "<ReactMarkdown>{''}</ReactMarkdown>", "UI04: final answer missing", True),
    ):
        verify_mutation(name, path, before, after, expected, rebuild=rebuild,
                        script="ui04.py", run_var="UI04_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui04.py"))], cwd=ROOT,
                   env={**os.environ, "UI04_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
