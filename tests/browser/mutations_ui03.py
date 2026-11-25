"""The same UI-03 acceptance must reject real profile regressions."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation


def main():
    for name, path, before, after, expected, rebuild in (
        ("missing-profile-persistence", "backend/main.py",
         "        app_config.write_council_config_document(document)",
         "        # Mutation: skip profile persistence.", "UI03: persisted native tier mismatch", False),
        ("stale-effective-profile", "backend/main.py", "        reload_config()\n    except Exception as exc:",
         "        # Mutation: skip native reload.\n    except Exception as exc:", "UI03: effective profile mismatch", False),
        ("cleared-selected-conversation", "frontend/src/App.jsx",
         """    if (id !== currentConversationId) {
      setCurrentConversation(null);
      setCurrentConversationId(id);
    } else if (!currentConversation) {
      void loadConversation(id);
    }""",
         """    setCurrentConversation(null);
    setCurrentConversationId(id);""",
         "UI03: existing conversation missing after selection", True),
        ("stale-chat-profile", "frontend/src/components/Settings.jsx",
         "onProfileChange?.(data.profile);", "onProfileChange?.('free');",
         "UI03: Free notice not hidden", True),
    ):
        verify_mutation(name, path, before, after, expected, rebuild=rebuild,
                        script="ui03.py", run_var="UI03_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui03.py"))], cwd=ROOT,
                   env={**os.environ, "UI03_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
