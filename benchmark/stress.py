# SPDX-License-Identifier: Apache-2.0
"""Whether release-drift handles real packages: crashes, refusals and slow comparisons.

    python3 benchmark/stress.py [--pairs FILE ...] [--cache DIR] [--slow S] [--timeout S] [--out FILE]

Takes every pair in the pinned noise samples (benchmark/noise-pairs*.json by default), downloads
both releases once into the cache, and runs `release-drift diff --json` on each pair in its own
process. Each run is classed by how it ended:

- ok: exit status 0 or 1, nothing but JSON on standard output
- refused: exit status 2 with a `release-drift: error:` message and no traceback
- crash: any other exit status, a Python traceback, or output that is not JSON
- timeout: still running after --timeout seconds

A run is also marked slow when it takes longer than --slow seconds. Nothing is installed or run
from the packages; release-drift only reads them. Needs network access for releases not yet in
the cache.
"""

import argparse
import collections
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from noise import artifacts  # noqa: E402


def classify(returncode, out, err):
    """The class of one finished run and the message that explains it."""
    message = err.strip().splitlines()[-1] if err.strip() else ""
    if "Traceback" in err:
        return "crash", message
    if returncode == 2 and err.startswith("release-drift: error:"):
        return "refused", message
    if returncode in (0, 1):
        try:
            json.loads(out)
        except ValueError:
            return "crash", "output is not JSON"
        return "ok", ""
    return "crash", message or "exit status %s" % returncode


def run_one(old, new, timeout):
    """Run one diff; return (class, exit status, seconds, message)."""
    start = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, "-m", "release_drift", "diff", "--json", old, new],
                              cwd=ROOT, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", None, round(time.monotonic() - start, 2), "over %ss" % timeout
    seconds = round(time.monotonic() - start, 2)
    kind, message = classify(proc.returncode, proc.stdout.decode("utf-8", "replace"),
                             proc.stderr.decode("utf-8", "replace"))
    return kind, proc.returncode, seconds, message


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pairs", nargs="+",
                        default=sorted(glob.glob(os.path.join(HERE, "noise-pairs*.json"))))
    parser.add_argument("--cache", required=True, help="download directory, reused between runs")
    parser.add_argument("--slow", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    rows = []
    for pairs_file in args.pairs:
        sample = os.path.basename(pairs_file)
        for p in json.load(open(pairs_file)):
            row = {"sample": sample, "registry": p["registry"], "name": p["name"],
                   "old": p["old"], "new": p["new"]}
            paths = artifacts(p, args.cache)
            if None in paths:
                row.update(result="fetch-failed")
            else:
                kind, code, seconds, message = run_one(paths[0], paths[1], args.timeout)
                row.update(result=kind, exit=code, seconds=seconds, message=message,
                           bytes=sum(os.path.getsize(x) for x in paths))
            rows.append(row)
            print("%-8s %-12s %s %s %s %s" % (row["result"], row.get("seconds", "-"), sample,
                                              p["registry"], p["name"], row.get("message", "")),
                  file=sys.stderr)
    timed = sorted((r for r in rows if "seconds" in r), key=lambda r: r["seconds"])
    summary = {
        "runs": len(rows),
        "by_result": dict(sorted(collections.Counter(r["result"] for r in rows).items())),
        "slow": [r for r in timed if r["seconds"] > args.slow],
        "slow_threshold_seconds": args.slow,
        "seconds_median": timed[len(timed) // 2]["seconds"] if timed else None,
        "seconds_max": timed[-1]["seconds"] if timed else None,
        "not_ok": [r for r in rows if r["result"] not in ("ok",)],
    }
    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"summary": summary, "rows": rows}, fh, indent=2, sort_keys=True)
            fh.write("\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if any(r["result"] in ("crash", "timeout") for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
