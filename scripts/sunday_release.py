"""Fetch the Sunday data release and check that the agent sees it. No practice quota, no model call.

    uv run python scripts/sunday_release.py           # fetch once it is public, then check
    uv run python scripts/sunday_release.py --wait    # poll every 20 s until it is public
    uv run python scripts/sunday_release.py --check   # only check an already unpacked release

The release is unpacked next to the package (`<package>/../release-sunday`), where the kit's
`DataPackage` finds it for every worktree. The package itself is never overwritten: a fresh copy is
downloaded to a temporary folder and only compared. Agents load the data at start, so restart any
running agent after this."""

import argparse
import collections
import hashlib
import io
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.cli import load_env
from arena_kit.data import RELEASE, DataPackage


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else None


def unpack(blob, into):
    """Unpack a zip and return the folder that holds its facts.json."""
    zipfile.ZipFile(io.BytesIO(blob)).extractall(into)
    found = sorted(into.rglob("facts.json"), key=lambda p: len(p.parts))
    if not found:
        sys.exit(f"no facts.json in the download ({into})")
    return found[0].parent


def fetch(client, package, wait):
    while True:
        info = client.downloads().get(RELEASE) or {}
        if info.get("url"):
            break
        if not wait:
            sys.exit(f"release not public yet (opens {info.get('at')}); use --wait")
        print(f"{time.strftime('%H:%M:%S')} not public yet, opens {info.get('at')}", flush=True)
        time.sleep(20)
    target = package.parent / RELEASE
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = unpack(client.download(RELEASE), tmp / "release")
        if target.exists():
            shutil.move(str(target), str(target.with_name(f"{RELEASE}.old-{int(time.time())}")))
        shutil.copytree(source, target)
        fresh = unpack(client.download("package"), tmp / "package")
        for name in ("facts.json", "world.sqlite"):
            same = digest(fresh / name) == digest(package / name)
            print(f"package {name}: {'unchanged' if same else 'CHANGED - compare before replacing'}")
    print(f"release unpacked to {target}")


def check(package):
    pkg = DataPackage(package)
    if not pkg.release:
        sys.exit(f"DataPackage finds no release next to {package}")
    base = DataPackage(package, release=os.devnull)  # the package alone
    new = sorted(set(pkg.facts) - set(base.facts))
    print(f"release at {pkg.release}: {len(new)} new citable facts")
    for prefix, n in collections.Counter(ref.split(":")[0] for ref in new).most_common():
        print(f"  {prefix}: {n}")
    for ref in new[:5]:
        print(f"  e.g. {ref}: {str(pkg.lookup(ref))[:120]}")
    files = [p.relative_to(pkg.release) for p in sorted(pkg.release.rglob("*")) if p.is_file()]
    print(f"{len(files)} files: " + ", ".join(str(f) for f in files[:20]) + (" ..." if len(files) > 20 else ""))
    missing = [ref for ref in new if pkg.lookup(ref) is None]
    print("every new fact resolves through lookup()" if not missing else f"UNRESOLVED: {missing[:10]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--check", action="store_true", help="only check an unpacked release")
    args = parser.parse_args()
    load_env()
    package = Path(os.environ.get("ARENA_DATA", "../package")).expanduser().resolve()
    if not args.check:
        fetch(ArenaClient(os.environ["ARENA_URL"], os.environ["ARENA_API_KEY"]), package, args.wait)
    check(package)


if __name__ == "__main__":
    main()
