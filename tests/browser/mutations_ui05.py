"""Require UI-05 to reject tab-selection and model/answer association regressions."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation


def main():
    for name, before, after, expected in (
        ("frozen-model-tab", "onClick={() => setActiveTab(index)}",
         "onClick={() => setActiveTab(0)}", "UI05: selected tab not active"),
        ("stale-model-identity", "{responses[activeTab].model}",
         "{responses[0].model}", "UI05: selected model identity mismatch"),
        ("stale-model-response", "{responses[activeTab].response}",
         "{responses[0].response}", "UI05: selected model response mismatch"),
    ):
        verify_mutation(name, "frontend/src/components/Stage1.jsx", before, after, expected,
                        rebuild=True, script="ui05.py", run_var="UI05_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui05.py"))], cwd=ROOT,
                   env={**os.environ, "UI05_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
