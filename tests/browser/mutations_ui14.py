"""UI-14 must reject real active-run safety regressions and restore source bytes."""
import os
import subprocess
import sys
from pathlib import Path

from mutations import ROOT, verify_mutation


def main():
    for name, path, before, after, marker, rebuild in (
        ("active-profile-write", "backend/main.py",
         "return _update_when_idle(lambda: _save_settings(request))",
         "return _save_settings(request)", "UI14: active profile update accepted", False),
        ("active-key-write", "backend/main.py",
         "return _update_when_idle(lambda: _save_openrouter_key(request))",
         "return _save_openrouter_key(request)", "UI14: active key update accepted", False),
        ("hidden-conflict", "frontend/src/components/Settings.jsx",
         "    } catch (err) {\n      setError(err.message);\n    } finally {\n      setSaving(false);",
         "    } catch (err) {\n      setError('');\n    } finally {\n      setSaving(false);",
         "UI14: settings conflict hidden", True),
        ("reservation-leak", "backend/main.py",
         "_active_council_runs -= 1", "_active_council_runs -= 0",
         "UI14: idle profile update rejected", False),
        ("paid-title-in-free-round", "backend/main.py",
         "if free_mode:\n                        storage.update_conversation_title(",
         "if False:\n                        storage.update_conversation_title(",
         "UI14: unsafe provider traffic", False),
    ):
        verify_mutation(name, path, before, after, marker, rebuild=rebuild,
                        script="ui14.py", run_var="UI14_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui14.py"))], cwd=ROOT,
                   env={**os.environ, "UI14_RUN": "restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
