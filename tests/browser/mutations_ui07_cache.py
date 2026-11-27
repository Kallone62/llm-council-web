"""Prove native-cache browser tests detect real source regressions and restore."""
import os
import subprocess
import sys
from pathlib import Path

from llm_council import cache
from mutations import ROOT, verify_mutation

NATIVE_CACHE = Path(cache.__file__).resolve()
MUTATIONS = (
    ("cache-write-disabled", NATIVE_CACHE,
     '    if not _effective("CACHE_ENABLED"):\n        return\n',
     '    if True:\n        return\n',
     "UI07-cache: cache file missing", False),
    ("cache-dissent-collision", NATIVE_CACHE,
     '        "include_dissent": include_dissent,',
     '        "include_dissent": False,',
     "UI07-cache: fresh round unexpectedly cached", False),
    ("cache-ignores-ttl", NATIVE_CACHE,
     '            if time.time() - cached_at > ttl:',
     '            if False:',
     "UI07-cache: fresh round unexpectedly cached", False),
    ("cache-notice-hidden", "frontend/src/components/ChatInterface.jsx",
     "{msg.metadata?.cache_hit === true && (",
     "{false && (",
     "UI07-cache: reuse notice missing", True),
)


def main():
    for name, path, before, after, marker, rebuild in MUTATIONS:
        verify_mutation(name, path, before, after, marker, rebuild=rebuild,
                        script="ui07_cache.py", run_var="UI07_CACHE_RUN")
    subprocess.run([sys.executable, str(Path(__file__).with_name("ui07_cache.py"))], cwd=ROOT,
                   env={**os.environ, "UI07_CACHE_RUN": "cache-restored"}, check=True, timeout=120)


if __name__ == "__main__":
    main()
