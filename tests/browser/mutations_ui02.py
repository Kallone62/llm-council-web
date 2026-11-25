"""Prove the unchanged UI-02 acceptance detects credential regressions."""
import os
import secrets
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from mutations import ROOT, verify_mutation
from ui02 import AcceptanceError, publish_evidence


def verify_artifact_guard():
    for compressed in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            key = "sk-or-v1-" + secrets.token_hex(32)
            if compressed:
                with zipfile.ZipFile(source / "trace.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("network.json", key)
            else:
                (source / "backend.log").write_text(key, encoding="utf-8")
            try:
                publish_evidence(source, [key])
            except AcceptanceError as error:
                assert str(error) == ("UI02: key leaked in trace" if compressed else "UI02: key leaked in evidence")
            else:
                raise AssertionError("UI02: artifact guard accepted secret")
            print(f"UI02 artifact leak rejected: {'compressed trace' if compressed else 'plain log'}", flush=True)


def main():
    for name, path, before, after, expected, rebuild in (
        ("missing-key-persistence", "backend/config.py",
         '    ENV_PATH.write_text("\\n".join(output).rstrip() + "\\n", encoding="utf-8")',
         "    # Mutation: skip persisted key write.", "UI02: persisted key mismatch", False),
        ("key-response-leak", "backend/main.py", '    return {"configured": _api_key_configured()}',
         '    return {"configured": _api_key_configured(), "api_key": value}',
         "UI02: key leaked in API response", False),
        ("stale-sidebar-key-status", "frontend/src/components/Settings.jsx",
         "onApiKeyStatusChange?.(result.configured);", "onApiKeyStatusChange?.(false);",
         "UI02: sidebar key status not updated", True),
    ):
        verify_mutation(name, path, before, after, expected, rebuild=rebuild,
                        script="ui02.py", run_var="UI02_RUN")
    verify_artifact_guard()
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui02.py"))], cwd=ROOT,
                   env={**os.environ, "UI02_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
