"""Prove UI-06 rejects reviewer, label mapping and aggregate display regressions."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation

MUTATIONS = (
    ("frozen-reviewer", "onClick={() => setActiveTab(index)}",
     "onClick={() => setActiveTab(0)}", "UI06: selected reviewer not active"),
    ("stale-review", "typeof current.ranking === 'string' ? current.ranking : ''",
     "typeof reviews[0].ranking === 'string' ? reviews[0].ranking : ''", "UI06: review prose mismatch"),
    ("anonymous-review", "deAnonymizeText(typeof current.ranking === 'string' ? current.ranking : '', labelToModel)",
     "typeof current.ranking === 'string' ? current.ranking : ''", "UI06: review prose mismatch"),
    ("wrong-extracted-label", "? modelShortName(labelToModel[label])",
     "? modelShortName(Object.values(labelToModel)[0])", "UI06: extracted ranking mismatch"),
    ("wrong-borda-score", "borda.toFixed(3)", "(borda + 0.1).toFixed(3)", "UI06: aggregate rank-score mismatch"),
    ("wrong-vote-count", "({votes} votes)", "({votes + 1} votes)", "UI06: aggregate rank-count mismatch"),
)


def main():
    for name, before, after, expected in MUTATIONS:
        verify_mutation(name, "frontend/src/components/Stage2.jsx", before, after, expected,
                        rebuild=True, script="ui06.py", run_var="UI06_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui06.py"))], cwd=ROOT,
                   env={**os.environ, "UI06_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
