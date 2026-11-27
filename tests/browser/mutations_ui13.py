"""UI-13: exact browser acceptance against temporary feature/profile regressions."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation


def main():
    for name, path, before, after, marker, rebuild in (
        ("free-controls-unlocked", "frontend/src/components/Settings.jsx",
         "checked={draft.rubric_enabled} onChange={(v) => update('rubric_enabled', v)} disabled={isFree}",
         "checked={draft.rubric_enabled} onChange={(v) => update('rubric_enabled', v)} disabled={false}",
         "UI13: Free restriction missing", True),
        ("native-controls-locked", "frontend/src/components/Settings.jsx",
         "checked={draft.rubric_enabled} onChange={(v) => update('rubric_enabled', v)} disabled={isFree}",
         "checked={draft.rubric_enabled} onChange={(v) => update('rubric_enabled', v)} disabled={true}",
         "UI13: native profile control locked", True),
        ("free-feature-not-reset", "frontend/src/components/Settings.jsx",
         "        rubric_enabled: false,",
         "        rubric_enabled: prev.rubric_enabled,",
         "UI13: feature draft value mismatch", True),
        ("wrong-profile-models", "backend/main.py",
         "pools[request.profile] if request.use_profile_models else request.models",
         "pools['quick'] if request.use_profile_models else request.models",
         "UI03: persisted model pool mismatch", False),
        ("dropped-native-feature", "backend/main.py",
         'evaluation.setdefault("rubric", {})["enabled"] = request.rubric_enabled',
         'evaluation.setdefault("rubric", {})["enabled"] = False',
         "UI13: persisted feature mismatch: rubric", False),
    ):
        verify_mutation(name, path, before, after, marker, rebuild=rebuild,
                        script="ui13.py", run_var="UI13_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui13.py"))], cwd=ROOT,
                   env={**os.environ, "UI13_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
