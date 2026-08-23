"""Run every test in this folder. No dependencies beyond the standard library.

    python tests/run.py            all suites
    python tests/run.py backup     only suites whose name contains "backup"
    python tests/run.py -v         show each suite's output
"""
import io
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def main():
    args = [a for a in sys.argv[1:]]
    verbose = "-v" in args
    picks = [a for a in args if not a.startswith("-")]

    suites = sorted(HERE.glob("test_*.py"))
    if picks:
        suites = [s for s in suites if any(p in s.stem for p in picks)]
    if not suites:
        print("no matching suites")
        return 1

    passed, failed = [], []
    for suite in suites:
        proc = subprocess.run([sys.executable, "-X", "utf8", str(suite)],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(HERE))
        ok = proc.returncode == 0
        (passed if ok else failed).append(suite.stem)
        print(f"  {'PASS' if ok else 'FAIL'}  {suite.stem}")
        if verbose or not ok:
            body = (proc.stdout or "") + (proc.stderr or "")
            for line in body.rstrip().split("\n"):
                print(f"        {line}")

    print(f"\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
