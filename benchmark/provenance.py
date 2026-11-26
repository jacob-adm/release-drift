# SPDX-License-Identifier: Apache-2.0
"""How often `release-drift provenance` reports a record for an ordinary release.

    python3 benchmark/provenance.py [--pairs FILE] [--out FILE]

Reads the registry metadata of both releases of every pinned pair (default
benchmark/noise-pairs.json), compares them as `release-drift provenance` does, writes the
per-pair results (default benchmark/provenance-results.json) and prints the counts.
Needs network access; downloads metadata only.
"""

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from release_drift.fetch import FetchError  # noqa: E402
from release_drift.provenance import compare, read  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pairs", default=os.path.join(HERE, "noise-pairs.json"))
    p.add_argument("--out", default=os.path.join(HERE, "provenance-results.json"))
    args = p.parse_args()
    with open(args.pairs) as fh:
        pairs = json.load(fh)
    results, counts = [], collections.Counter()
    for pair in pairs:
        row = {k: pair[k] for k in ("registry", "name", "old", "new")}
        try:
            old = read(pair["registry"], pair["name"], pair["old"])
            new = read(pair["registry"], pair["name"], pair["new"])
        except FetchError as exc:
            row["error"] = str(exc)
            counts["error"] += 1
        else:
            row.update(old_workflow=old["workflow"], new_workflow=new["workflow"],
                       records=[list(r) for r in compare(old, new, pair["old"], pair["new"])])
            counts["%s compared" % pair["registry"]] += 1
            counts["%s new release attested" % pair["registry"]] += new["workflow"] is not None
            counts["%s with a record" % pair["registry"]] += bool(row["records"])
            for record in row["records"]:
                counts["%s %s" % (pair["registry"], record[1])] += 1
        results.append(row)
    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True)
        fh.write("\n")
    for key in sorted(counts):
        print("%s: %d" % (key, counts[key]))


if __name__ == "__main__":
    main()
