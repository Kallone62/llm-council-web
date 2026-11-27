"""Prove UI-08 rejects missing, foreign, failed and modified history."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation

MUTATIONS = (
    ("missing-context", "backend/conversation_context.py", "    if not history:\n        return question", "    if True:\n        return question", "UI08: effective context mismatch (a-failed)"),
    ("foreign-context", "backend/main.py", "                    conversation, request.content, progress_callback=memory_progress,", "                    (storage.get_conversation(next(r['id'] for r in storage.list_conversations() if r['id'] != conversation_id)) if conversation['messages'] else conversation), request.content, progress_callback=memory_progress,", "UI08: effective context mismatch (a-failed)"),
    ("failed-context", "backend/conversation_context.py", "                and not stage3.get(\"error_status\")\n", "", "UI08: effective context mismatch (a-follow)"),
    ("rewritten-history", "backend/storage.py", '    conversation["messages"].append(message)', '    if len(conversation["messages"]) > 1:\n        conversation["messages"][0]["content"] = "Modified prior question"\n    conversation["messages"].append(message)', "UI08: prior raw history changed"),
)


def main():
    for name, path, before, after, marker in MUTATIONS:
        verify_mutation(name, path, before, after, marker, script="ui08.py", run_var="UI08_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui08.py"))], cwd=ROOT,
                   env={**os.environ, "UI08_RUN": "restored"}, check=True, timeout=120)


    # Successful rolling memory and reuse have their own browser oracle.
    memory_mutations = (
        ("wrong-memory-progress", "frontend/src/components/ChatInterface.jsx",
         "    : item.state === 'completed' ? 'completed'",
         "    : item.state === 'completed' ? (item.stage === 'memory' ? 'failed' : 'completed')",
         "UI08-memory: completed summary not visible", True),
        ("lost-summary", "backend/storage.py", '    conversation["context_memory"] = memory',
         "    pass  # Mutation: drop the new summary", "UI08-memory: persisted summary mismatch", False),
        ("resummarize-cached-history", "backend/conversation_context.py",
         "    if len(history) <= MAX_HISTORY_CHARS:", "    if len(history) <= MAX_HISTORY_CHARS and not memory:",
         "UI08-memory: cached summary invoked provider", False),
    )
    for name, path, before, after, marker, rebuild in memory_mutations:
        verify_mutation(name, path, before, after, marker, rebuild=rebuild,
                        script="ui08_memory.py", run_var="UI08_MEMORY_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui08_memory.py"))], cwd=ROOT,
                   env={**os.environ, "UI08_MEMORY_RUN": "memory-restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
