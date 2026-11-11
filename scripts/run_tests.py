"""Runs all test scripts (test_*.py) one by one and prints a summary. Exit code 0 only if all of them pass.

Usage:  python scripts/run_tests.py
The tests use the reference logs in data/ and the built-in emulator, never a real serial port or network.
The GUI tests open a hidden Tk window, so a desktop session is needed.
"""
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    tests = sorted(glob.glob(os.path.join(ROOT, "test_*.py")))
    if not tests:
        print("no test_*.py files found")
        return 1
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    failed = []
    t_all = time.time()
    for path in tests:
        name = os.path.basename(path)
        t0 = time.time()
        r = subprocess.run([sys.executable, path], cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'} {name} ({time.time() - t0:.1f} s)", flush=True)
        if not ok:
            failed.append(name)
            print((r.stdout + r.stderr)[-2000:])
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed in {time.time() - t_all:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
