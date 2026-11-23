# SPDX-License-Identifier: Apache-2.0
"""How often release-drift reports a finding for an ordinary release.

    python3 benchmark/noise.py pick --pypi TOP.json --npm TOP.js --count 100 > benchmark/noise-pairs.json
    python3 benchmark/noise.py run [--pairs FILE] [--cache DIR] [--out FILE]

`pick` takes the most-downloaded packages and pins, for each, its latest stable release and
the stable release before it. `run` downloads both releases of every pair (PyPI sdist, npm
tarball), runs release-drift's diff on them and prints the share of pairs with a finding and
the findings by category. Nothing is installed or run; release-drift only reads the files.
Needs network access.
"""

import argparse
import collections
import json
import os
import re
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from release_drift.archive import ArtifactError, read_artifact  # noqa: E402
from release_drift.compare import diff, is_finding  # noqa: E402
from release_drift.fetch import FetchError, fetch  # noqa: E402

STABLE = re.compile(r"^\d+(\.\d+)*$")


def _json(url):
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def _last_two(versions):
    """The highest stable version and the one just below it, by version number (not by
    publish time, so a backport to an older line is not taken as the newest release)."""
    ordered = sorted((tuple(int(x) for x in v.split(".")), v) for v in versions if STABLE.match(v))
    return [v for _, v in ordered[-2:]] if len(ordered) >= 2 else None


def _pypi_pair(name):
    """The two highest stable releases that both have an sdist."""
    data = _json("https://pypi.org/pypi/%s/json" % name)
    return _last_two([v for v, files in data["releases"].items()
                      if any(f["packagetype"] == "sdist" and not f.get("yanked") for f in files)])


def _npm_pair(name):
    """The two highest stable versions."""
    data = _json("https://registry.npmjs.org/" + name.replace("/", "%2F"))
    return _last_two(list(data.get("versions", {})))


def pick(args):
    pypi = [r["project"] for r in json.load(open(args.pypi))["rows"]]
    npm = re.findall(r"'([^']+)'", open(args.npm).read())
    pairs = []
    for registry, names, get in (("pypi", pypi, _pypi_pair), ("npm", npm, _npm_pair)):
        taken = 0
        for name in names:
            if taken == args.count:
                break
            try:
                pair = get(name)
            except Exception as exc:  # noqa: BLE001 - a package that cannot be read is skipped
                print("skip %s %s: %s" % (registry, name, exc), file=sys.stderr)
                continue
            if pair:
                pairs.append({"registry": registry, "name": name, "old": pair[0], "new": pair[1]})
                taken += 1
    json.dump(pairs, sys.stdout, indent=2)
    sys.stdout.write("\n")


def run(args):
    pairs = json.load(open(args.pairs))
    cache = args.cache or tempfile.mkdtemp(prefix="release-drift-noise-")
    rows = []
    for p in pairs:
        paths = []
        for version in (p["old"], p["new"]):
            directory = os.path.join(cache, p["registry"], p["name"].replace("/", "__"), version)
            os.makedirs(directory, exist_ok=True)
            try:
                found = [os.path.join(directory, f) for f in sorted(os.listdir(directory))
                         if not f.startswith(".")]
                paths.append((found or fetch(p["registry"], p["name"], version, directory))[0])
            except (FetchError, OSError) as exc:
                paths.append(None)
                print("fetch failed %s %s %s: %s" % (p["registry"], p["name"], version, exc),
                      file=sys.stderr)
        if None in paths:
            rows.append(dict(p, error="fetch"))
            continue
        try:
            records = diff(read_artifact(paths[0]), read_artifact(paths[1]))
        except ArtifactError as exc:
            rows.append(dict(p, error=str(exc)))
            continue
        findings = [r for r in records if is_finding("diff", r)]
        rows.append(dict(p, findings=collections.Counter(r[0] for r in findings)))
    done = [r for r in rows if "findings" in r]
    flagged = [r for r in done if r["findings"]]
    out = {"pairs": len(rows), "compared": len(done), "with_finding": len(flagged),
           "by_category_pairs": dict(sorted(collections.Counter(
               c for r in flagged for c in r["findings"]).items())),
           "by_registry": {}, "errors": [dict((k, r[k]) for k in ("registry", "name", "error"))
                                         for r in rows if "error" in r],
           "rows": [dict((k, v) for k, v in r.items()) for r in done]}
    for registry in ("pypi", "npm"):
        mine = [r for r in done if r["registry"] == registry]
        out["by_registry"][registry] = {"compared": len(mine),
                                        "with_finding": sum(1 for r in mine if r["findings"])}
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
    summary = dict((k, out[k]) for k in ("pairs", "compared", "with_finding",
                                         "by_category_pairs", "by_registry"))
    print(json.dumps(summary, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--pypi", required=True)
    p.add_argument("--npm", required=True)
    p.add_argument("--count", type=int, default=100)
    r = sub.add_parser("run")
    r.add_argument("--pairs", default=os.path.join(HERE, "noise-pairs.json"))
    r.add_argument("--cache")
    r.add_argument("--out")
    args = parser.parse_args(argv)
    (pick if args.command == "pick" else run)(args)


if __name__ == "__main__":
    main()
