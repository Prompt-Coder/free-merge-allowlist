"""
Bulk backfill for the free-merge allowlist.

Hashes every merge-relevant file (.ymap .ybn .ydr .ydd .ytyp) found in the
configured sources - release zips, resource folders and the customer mapdata
git repositories - and appends the hashes that are not yet published for the
publisher. Append-only, publisher-wide dedup, same file format as
tools/publish.ps1 (one lowercase hex SHA-256 per line, "#" comments allowed).

Large groups are chunked into several files so each stays small enough for the
API loader (one HTTP fetch per listed file, 15 s timeout each).

Usage:
    python tools/backfill.py --publisher prompt --config tools/backfill-prompt.json
Config: {"groups": {"<group-name>": {"zips": [dir-or-file, ...], "dirs": [...],
         "git_bare": [...], "include": ["glob", ...], "exclude": ["glob", ...]}}}
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import subprocess
import zipfile

MERGE_EXTENSIONS = {".ymap", ".ybn", ".ydr", ".ydd", ".ytyp"}
CHUNK_LINES = 50000
INDEX_HEADER = "# free-merge allowlist index - one hash file per line"
# The base-game file inventory the Vertex Hub app itself uses (basenames).
DEFAULT_NAMES_URL = "https://cdn.vertex-hub.com/gta5-resource-list.json"

# Name filter, set by main(): only files whose basename is a vanilla game file
# (the merger only ever merges those) plus the always-include globs
# (lodlights ymaps of any name - the lodlights manager takes them all).
VANILLA_NAMES = None
ALWAYS_INCLUDE = []
# Any path segment matching one of these globs is skipped entirely. Mapdata is a
# merged product of its own and is deliberately not listed here.
SKIP_PATH_GLOBS = []


def is_merge_file(name):
    if os.path.splitext(name)[1].lower() not in MERGE_EXTENSIONS:
        return False
    segments = name.replace("\\", "/").lower().split("/")
    if any(fnmatch.fnmatch(segment, pattern.lower()) for segment in segments for pattern in SKIP_PATH_GLOBS):
        return False
    base = segments[-1]
    if any(fnmatch.fnmatch(base, pattern.lower()) for pattern in ALWAYS_INCLUDE):
        return True
    return VANILLA_NAMES is None or base in VANILLA_NAMES


def load_names(source):
    """A JSON list (or one name per line) of basenames, from a path or URL."""
    import urllib.request
    if source.startswith("http://") or source.startswith("https://"):
        # The CDN rejects the default python user agent with 403.
        request = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0 free-merge-allowlist/backfill"})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    else:
        raw = open(source, encoding="utf-8").read()
    try:
        names = json.loads(raw)
    except json.JSONDecodeError:
        names = raw.splitlines()
    return {str(n).strip().lower() for n in names if str(n).strip()}


def sha256_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1 << 20), b""):
        digest.update(block)
    return digest.hexdigest()


def entry_allowed(entry_name, entry_include):
    """A zip entry qualifies when any folder segment of its path matches one
    of the entry_include globs (e.g. "*prompt_*" picks our resources out of a
    bundle that also carries third-party ones). No patterns = everything."""
    if not entry_include:
        return True
    segments = entry_name.replace("\\", "/").lower().split("/")[:-1]
    return any(fnmatch.fnmatch(segment, pattern.lower()) for segment in segments for pattern in entry_include)


def hash_zip(path, out, entry_include=None):
    n = 0
    # A zip that is itself named like a resource we want (prompt_x.zip) is
    # taken whole; the entry filter is for bundles that mix publishers.
    if entry_include and matches(os.path.basename(path), ["*" + pattern + "*" for pattern in entry_include], []):
        entry_include = None
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or not is_merge_file(info.filename):
                continue
            if not entry_allowed(info.filename, entry_include):
                continue
            with archive.open(info) as stream:
                out.add(sha256_stream(stream))
            n += 1
    return n


def hash_dir(path, out):
    n = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            if not is_merge_file(os.path.join(root, name)):
                continue
            with open(os.path.join(root, name), "rb") as stream:
                out.add(sha256_stream(stream))
            n += 1
    return n


def hash_git_bare(repo, out):
    """Every blob with a merge extension reachable from any branch head."""
    heads = subprocess.run(
        ["git", "-C", repo, "for-each-ref", "--format=%(objectname)", "refs/heads"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    oids = set()
    for i, head in enumerate(heads):
        tree = subprocess.run(
            ["git", "-C", repo, "ls-tree", "-r", "-z", head],
            capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
        for entry in tree.split("\0"):
            if not entry:
                continue
            meta, _tab, name = entry.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and is_merge_file(name):
                oids.add(parts[2])
        if (i + 1) % 1000 == 0:
            print(f"    {i + 1}/{len(heads)} heads scanned, {len(oids)} unique blobs", flush=True)
    print(f"    {len(heads)} heads, {len(oids)} unique merge-file blobs; hashing...", flush=True)
    proc = subprocess.Popen(
        ["git", "-C", repo, "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    n = 0
    for oid in sorted(oids):
        proc.stdin.write((oid + "\n").encode())
        proc.stdin.flush()
        header = proc.stdout.readline().decode().split()
        if len(header) != 3:
            raise RuntimeError(f"unexpected cat-file header for {oid}: {header}")
        size = int(header[2])
        data = proc.stdout.read(size)
        proc.stdout.read(1)  # trailing newline
        out.add(hashlib.sha256(data).hexdigest())
        n += 1
        if n % 20000 == 0:
            print(f"    {n}/{len(oids)} blobs hashed", flush=True)
    proc.stdin.close()
    proc.wait()
    return n


def matches(name, include, exclude):
    lname = name.lower()
    if include and not any(fnmatch.fnmatch(lname, pattern.lower()) for pattern in include):
        return False
    return not any(fnmatch.fnmatch(lname, pattern.lower()) for pattern in exclude)


def expand(entries, include, exclude, want_zip):
    """Directory entries -> concrete zip files / folders matching the filters."""
    result = []
    for entry in entries:
        if os.path.isfile(entry):
            result.append(entry)
            continue
        if not os.path.isdir(entry):
            print(f"  (missing) {entry}")
            continue
        for child in sorted(os.scandir(entry), key=lambda c: c.name.lower()):
            if want_zip and child.is_file() and child.name.lower().endswith(".zip") and matches(child.name, include, exclude):
                result.append(child.path)
            if not want_zip and child.is_dir() and matches(child.name, include, exclude):
                result.append(child.path)
    return result


def read_existing(publisher_dir):
    existing = set()
    if not os.path.isdir(publisher_dir):
        return existing
    for name in os.listdir(publisher_dir):
        if not name.endswith(".txt"):
            continue
        with open(os.path.join(publisher_dir, name), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing.add(line)
    return existing


def write_group(repo_root, publisher, group, hashes, stamp):
    """Append new hashes for a group, chunked; returns index entries written."""
    publisher_dir = os.path.join(repo_root, "publishers", publisher)
    os.makedirs(publisher_dir, exist_ok=True)
    entries = []
    ordered = sorted(hashes)
    chunks = [ordered[i:i + CHUNK_LINES] for i in range(0, len(ordered), CHUNK_LINES)]
    for number, chunk in enumerate(chunks, start=1):
        name = group if len(chunks) == 1 else f"{group}-{number:02d}"
        target = os.path.join(publisher_dir, name + ".txt")
        block = [f"# {name} {stamp} (+{len(chunk)})"] + chunk
        prefix = "\n" if os.path.exists(target) else ""
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix + "\n".join(block) + "\n")
        entries.append(f"publishers/{publisher}/{name}.txt")
    return entries


def update_index(repo_root, entries):
    index_file = os.path.join(repo_root, "index.txt")
    lines = []
    if os.path.exists(index_file):
        with open(index_file, encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle]
    if not lines:
        lines = [INDEX_HEADER]
    for entry in entries:
        if entry not in lines:
            lines.append(entry)
    with open(index_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--names", default=DEFAULT_NAMES_URL,
                        help="vanilla basename list (path or URL); 'none' disables the filter")
    parser.add_argument("--always", action="append", default=["*lodlights*.ymap"],
                        help="basename glob always included regardless of the vanilla list (repeatable)")
    parser.add_argument("--skip", action="append", default=["*mapdata*"],
                        help="path-segment glob to skip entirely (repeatable)")
    args = parser.parse_args()

    global VANILLA_NAMES, ALWAYS_INCLUDE, SKIP_PATH_GLOBS
    ALWAYS_INCLUDE = args.always
    SKIP_PATH_GLOBS = args.skip
    print(f"skipping path segments: {SKIP_PATH_GLOBS}")
    if args.names.lower() != "none":
        VANILLA_NAMES = load_names(args.names)
        print(f"vanilla name filter: {len(VANILLA_NAMES)} names from {args.names}; always: {ALWAYS_INCLUDE}")
    else:
        print("vanilla name filter: OFF")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)

    existing = read_existing(os.path.join(repo_root, "publishers", args.publisher))
    print(f"already published for {args.publisher}: {len(existing)} hashes")
    stamp = datetime.date.today().isoformat()
    all_entries = []
    grand_new = 0

    for group, spec in config["groups"].items():
        include = spec.get("include", [])
        exclude = spec.get("exclude", [])
        collected = set()
        files = 0
        print(f"[{group}]")
        entry_include = spec.get("entry_include", [])
        for source in expand(spec.get("zips", []), include, exclude, want_zip=True):
            count = hash_zip(source, collected, entry_include)
            files += count
            print(f"  zip {count:5d}  {source}")
        for source in expand(spec.get("dirs", []), include, exclude, want_zip=False):
            count = hash_dir(source, collected)
            files += count
            print(f"  dir {count:5d}  {source}")
        for repo in spec.get("git_bare", []):
            print(f"  git        {repo}")
            files += hash_git_bare(repo, collected)
        new = collected - existing
        print(f"  => {files} files, {len(collected)} unique hashes, {len(new)} new")
        if new and not args.dry_run:
            all_entries += write_group(repo_root, args.publisher, group, new, stamp)
            existing |= new
        grand_new += len(new)

    if all_entries and not args.dry_run:
        update_index(repo_root, all_entries)
    print(f"done: {grand_new} new hashes appended; total for {args.publisher}: {len(existing)}")


if __name__ == "__main__":
    main()
