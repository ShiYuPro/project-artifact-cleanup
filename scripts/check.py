#!/usr/bin/env python3
"""Run every packaged behavioral test directory, without live service calls."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
directories = sorted({p.parent for p in root.rglob('test_*.py')
                      if not any(part in p.parts for part in ('node_modules', '.git'))})
failed = []
for directory in directories:
    print('\n' + str(directory.relative_to(root)), flush=True)
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
                             str(directory), '-p', 'test_*.py'], capture_output=True, text=True)
    if result.returncode:
        print(result.stdout)
        failed.append(str(directory.relative_to(root)))
    print(result.stderr[-3000:])
raise SystemExit(bool(failed))
