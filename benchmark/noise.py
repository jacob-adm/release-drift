# SPDX-License-Identifier: Apache-2.0
"""How often release-drift reports a finding for an ordinary release.

    python3 benchmark/noise.py pick --pypi TOP.json --npm TOP.js --count 100 > benchmark/noise-pairs.json
    python3 benchmark/noise.py pick ... [--step N] [--offset K] [--kind wheel] [--registry pypi]
    python3 benchmark/noise.py run [--pairs FILE] [--cache DIR] [--out FILE]

`pick` takes the most-downloaded packages (after skipping the first K with --offset) and pins,
for each, its latest stable release and the stable release N versions before it (--step, default
1). With --kind wheel, PyPI pairs are compared as wheels with the same file tag. `run` downloads both releases of every pair (PyPI sdist, npm
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
from release_drift.fetch import FetchError, _get, check_digest, fetch, pypi_files  # noqa: E402
from release_drift.fetch import MAX_DOWNLOAD  # noqa: E402

STABLE = re.compile(r"^\d+(\.\d+)*$")


def _json(url):
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def _last_two(versions, step=1):
    """The highest stable version and the one STEP versions below it, by version number (not
    by publish time, so a backport to an older line is not taken as the newest release)."""
    ordered = sorted((tuple(int(x) for x in v.split(".")), v) for v in versions if STABLE.match(v))
    return [ordered[-1 - step][1], ordered[-1][1]] if len(ordered) > step else None


def _wheel_tag(filename):
    """The part of a wheel's name after NAME-VERSION: python, ABI and platform tags."""
    return "-".join(filename[:-4].split("-")[2:])


def _pypi_pair(name, step=1, kind="sdist"):
    """Releases with an sdist, or with a wheel; for wheels the pair must share a file tag."""
    data = _json("https://pypi.org/pypi/%s/json" % name)
    wanted = "bdist_wheel" if kind == "wheel" else "sdist"
    usable = {v: [f["filename"] for f in files if f["packagetype"] == wanted and not f.get("yanked")]
              for v, files in data["releases"].items()}
    pair = _last_two([v for v, names in usable.items() if names], step)
    if pair and kind == "wheel":
        common = sorted(set(map(_wheel_tag, usable[pair[0]])) & set(map(_wheel_tag, usable[pair[1]])))
        if not common:
            return None
        pure = [t for t in common if t.endswith("none-any")]
        pair.append((pure or common)[0])
    return pair


def _npm_pair(name, step=1, kind=None):
    """The highest stable version and the one STEP versions below it."""
    data = _json("https://registry.npmjs.org/" + name.replace("/", "%2F"))
    return _last_two(list(data.get("versions", {})), step)


def pick(args):
    pypi = [r["project"] for r in json.load(open(args.pypi))["rows"]]
    npm = re.findall(r"'([^']+)'", open(args.npm).read())
    pairs = []
    for registry, names, get in (("pypi", pypi, _pypi_pair), ("npm", npm, _npm_pair)):
        if args.registry and registry != args.registry:
            continue
        taken = 0
        for name in names[args.offset:]:
            if taken == args.count:
                break
            try:
                pair = get(name, args.step, args.kind)
            except Exception as exc:  # noqa: BLE001 - a package that cannot be read is skipped
                print("skip %s %s: %s" % (registry, name, exc), file=sys.stderr)
                continue
            if pair:
                row = {"registry": registry, "name": name, "old": pair[0], "new": pair[1]}
                if len(pair) == 3:
                    row["wheel_tag"] = pair[2]
                pairs.append(row)
                taken += 1
    json.dump(pairs, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _fetch_wheel(name, version, tag, directory):
    """Download the one wheel of VERSION with file tag TAG, checking its digest."""
    for filename, url, algorithm, digest, _ in pypi_files(name, version):
        if filename.endswith(".whl") and _wheel_tag(filename) == tag:
            data = _get(url, MAX_DOWNLOAD)
            check_digest(data, algorithm, digest)
            target = os.path.join(directory, os.path.basename(filename))
            with open(target, "wb") as fh:
                fh.write(data)
            return target
    raise FetchError("%s %s has no wheel tagged %s" % (name, version, tag))


def artifacts(p, cache):
    """Paths of the old and new artifact of pair P, downloaded into CACHE once; None for a
    release that could not be fetched."""
    paths = []
    for version in (p["old"], p["new"]):
        tag = p.get("wheel_tag")
        directory = os.path.join(cache, p["registry"] + ("-wheel" if tag else ""),
                                 p["name"].replace("/", "__"), version)
        os.makedirs(directory, exist_ok=True)
        try:
            found = [os.path.join(directory, f) for f in sorted(os.listdir(directory))
                     if not f.startswith(".")]
            if not found and tag:
                found = [_fetch_wheel(p["name"], version, tag, directory)]
            paths.append((found or fetch(p["registry"], p["name"], version, directory))[0])
        except (FetchError, OSError) as exc:
            paths.append(None)
            print("fetch failed %s %s %s: %s" % (p["registry"], p["name"], version, exc),
                  file=sys.stderr)
    return paths


def run(args):
    pairs = json.load(open(args.pairs))
    cache = args.cache or tempfile.mkdtemp(prefix="release-drift-noise-")
    rows = []
    for p in pairs:
        paths = artifacts(p, cache)
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
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--kind", choices=("sdist", "wheel"), default="sdist")
    p.add_argument("--registry", choices=("pypi", "npm"))
    r = sub.add_parser("run")
    r.add_argument("--pairs", default=os.path.join(HERE, "noise-pairs.json"))
    r.add_argument("--cache")
    r.add_argument("--out")
    args = parser.parse_args(argv)
    (pick if args.command == "pick" else run)(args)


if __name__ == "__main__":
    main()
