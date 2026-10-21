"""Offline audit revalidation. Original failure evidence remains in the report."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests/test_audit_regressions.py'], cwd=ROOT))
