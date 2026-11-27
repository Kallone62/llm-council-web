"""Each negative control runs the identical UI-07 scenario and checks its failure marker."""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from mutations import ROOT, verify_mutation

MUTATIONS = (
    ("wrong-synthesis", "synthesis", "Stage3", "<ReactMarkdown>{finalResponse.response}</ReactMarkdown>", "<ReactMarkdown>{'Wrong synthesis'}</ReactMarkdown>", "UI07: final synthesis mismatch"),
    ("missing-peer-warning", "partial", "Stage2", "{notice && <p", "{false && notice && <p", "UI07: peer warning missing"),
    ("hidden-empty-review", "empty-review", "Stage2", "if (!current && !notice)", "if (!current)", "UI07: peer warning missing"),
    ("missing-verdict", "binary-dissent", "Stage3", "{!failed && verdict && (", "{false && verdict && (", "UI07: verdict missing"),
    ("wrong-confidence", "binary-dissent", "Stage3", "(verdict.confidence * 100).toFixed(0)", "(verdict.confidence * 10).toFixed(0)", "UI07: confidence mismatch"),
    ("missing-dissent", "binary-dissent", "Stage3", "{verdict.dissent && (", "{false && (", "UI07: dissent missing or wrong"),
    ("misleading-verdict", "partial", "Stage3", "Chairman decision (not fully peer-validated):", "Decision:", "UI07: verdict validation label mismatch"),
    ("false-final-success", "empty", "Stage3", "{!failed && (\n          <div className=\"final-text markdown-content\">", "{(true) && (\n          <div className=\"final-text markdown-content\">", "UI07: failed synthesis presented as success"),
    ("missing-error", "chairman-auth", "Stage3", "{failed && (", "{false && (", "UI07: chairman error missing"),
)


def main():
    for name, case, component, before, after, marker in MUTATIONS:
        with patch.dict(os.environ, {"UI07_CASE": case}):
            verify_mutation(name, f"frontend/src/components/{component}.jsx", before, after, marker,
                            rebuild=True, script="ui07.py", run_var="UI07_RUN")
    env = {**os.environ, "UI07_RUN": "restored"}
    env.pop("UI07_CASE", None)
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui07.py"))], cwd=ROOT,
                   env=env, check=True, timeout=120)


if __name__ == "__main__":
    main()
