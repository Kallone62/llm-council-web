"""UI-09: prove the original browser oracle rejects production regressions."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation

MUTATIONS = (
    ("missing-error", "frontend/src/components/ChatInterface.jsx", "{msg.error && (", "{false && (", "UI09: meaningful error missing", True),
    ("stuck-loading", "frontend/src/App.jsx", "      setIsLoading(false);", "      setIsLoading(true);", "UI09: loading did not end", True),
    ("false-final", "frontend/src/App.jsx", "...lastMsg,\n            error: error.message", "...lastMsg,\n            stage3: {model: 'fake', response: 'False success'},\n            error: error.message", "UI09: false final displayed", True),
)


def main():
    for name, path, before, after, marker, rebuild in MUTATIONS:
        verify_mutation(name, path, before, after, marker, rebuild=rebuild, script="ui09.py", run_var="UI09_RUN")
    # Persist the rejected user turn before memory preparation, not after it.
    verify_mutation(
        "rejected-write", "backend/main.py",
        "                council_query, new_memory = await prepare_council_query(\n",
        "                storage.add_user_message(conversation_id, request.content)\n                council_query, new_memory = await prepare_council_query(\n",
        "UI09: rejected round changed disk", script="ui09.py", run_var="UI09_RUN",
    )
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui09.py"))], cwd=ROOT,
                   env={**os.environ, "UI09_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
