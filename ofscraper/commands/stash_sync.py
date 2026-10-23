"""Copy downloaded media's OnlyFans details into Stash; preview unless --apply.

Reads model databases (user_data.db) offline and talks only to Stash's GraphQL
API. Each downloaded file is looked up in Stash by path; empty fields are filled
in and the creator, studio and tags are added. Nothing set in Stash is
overwritten or removed, so running it again changes nothing.
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import closing
from pathlib import Path

DEFAULT_URL = "http://localhost:9999/graphql"
# Tables holding a post's text, in order of preference
TEXT_TABLES = ("posts", "messages", "stories", "others", "products")
# OnlyFans media types Stash indexes, and the query that finds them
KINDS = {"Videos": "scene", "Images": "image"}


class StashError(Exception):
    pass


class StashClient:
    def __init__(self, url=DEFAULT_URL, api_key=None, timeout=60):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def call(self, query, variables=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        request = urllib.request.Request(self.url, body, headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as e:
            raise StashError(f"{self.url}: HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise StashError(f"can't reach Stash at {self.url}: {e.reason}") from e
        if data.get("errors"):
            raise StashError("; ".join(err.get("message", str(err)) for err in data["errors"]))
        return data["data"]


# --- reading model databases ---------------------------------------------


def find_databases(paths):
    """Expand directories (e.g. a profile's .data) into their user_data.db files."""
    found = []
    for p in map(Path, paths):
        if p.is_dir():
            found.extend(sorted(p.rglob("user_data.db")))
        elif p.is_file():
            found.append(p)
        else:
            raise FileNotFoundError(p)
    return found


def clean_text(text):
    """OnlyFans captions are HTML; return plain text with line breaks kept."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def make_title(text, limit=100):
    first = next((line for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def read_model(database):
    """Return the model's username and its downloaded media, grouped by file path."""
    uri = Path(database).resolve().as_uri()
    with closing(sqlite3.connect(f"{uri}?mode=ro", uri=True)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        username = None
        if "profiles" in tables:
            row = conn.execute("SELECT username FROM profiles ORDER BY id DESC LIMIT 1").fetchone()
            username = row[0] if row else None

        texts = {}
        for table in reversed(TEXT_TABLES):  # earlier tables win
            if table in tables:
                for post_id, text in conn.execute(f"SELECT post_id, text FROM {table}"):
                    if text or str(post_id) not in texts:
                        texts[str(post_id)] = (table, text)

        date_col = "posted_at" if "posted_at" in columns(conn, "medias") else "created_at"
        rows = conn.execute(
            f"SELECT media_id, post_id, directory, filename, api_type, media_type, "
            f"COALESCE({date_col}, created_at) FROM medias "
            "WHERE downloaded = 1 AND filename IS NOT NULL AND filename != ''"
        ).fetchall()

    files = {}
    for media_id, post_id, directory, filename, api_type, media_type, posted in rows:
        path = os.path.join(directory or "", filename)
        entry = files.setdefault(
            path, {"path": path, "media_type": media_type, "api_types": set(), "posts": []}
        )
        if api_type:
            entry["api_types"].add(api_type)
        entry["posts"].append((posted or "", str(post_id) if post_id is not None else None))

    for entry in files.values():
        entry["meta"] = describe(entry, texts, username)
    return username, list(files.values())


def describe(entry, texts, username):
    """The Stash fields this file should have, from its earliest post with text."""
    posts = sorted(entry["posts"])
    posted, post_id = posts[0]
    table, text = None, ""
    for _, pid in posts:
        table, raw = texts.get(pid, (None, None))
        if raw:
            post_id, text = pid, clean_text(raw)
            break
    # Profile pictures and headers store a hash where the post ID would be
    code = post_id if post_id and post_id.isdigit() else None
    meta = {"code": code, "details": text or None, "title": make_title(text) or None}
    if re.match(r"\d{4}-\d{2}-\d{2}", posted or ""):
        meta["date"] = posted[:10]
    if table == "posts" and username and code:
        meta["url"] = f"https://onlyfans.com/{post_id}/{username}"
    return meta


# --- Stash -------------------------------------------------------------------

FIELDS = "id title details date code urls organized studio { id } performers { id } tags { id }"
FIND = {
    "scene": f"query($path: String!) {{ findScenes(scene_filter: {{path: {{value: $path, modifier: EQUALS}}}}, filter: {{per_page: -1}}) {{ scenes {{ {FIELDS} files {{ path }} }} }} }}",
    "image": f"query($path: String!) {{ findImages(image_filter: {{path: {{value: $path, modifier: EQUALS}}}}, filter: {{per_page: -1}}) {{ images {{ {FIELDS} visual_files {{ ... on ImageFile {{ path }} ... on VideoFile {{ path }} }} }} }} }}",
}
UPDATE = {
    "scene": "mutation($input: SceneUpdateInput!) { sceneUpdate(input: $input) { id } }",
    "image": "mutation($input: ImageUpdateInput!) { imageUpdate(input: $input) { id } }",
}


def find_item(client, kind, path):
    data = client.call(FIND[kind], {"path": path})
    items = data["findScenes"]["scenes"] if kind == "scene" else data["findImages"]["images"]
    for item in items:
        files = item.get("files") or item.get("visual_files") or []
        if any(f.get("path") == path for f in files):
            return item
    return None


def find_or_create(client, entity, name, apply, extra=None):
    """Return the ID of the studio, tag or performer with this exact name.

    In preview mode a missing one isn't created and None is returned.
    """
    plural = {"studio": "Studios", "tag": "Tags", "performer": "Performers"}[entity]
    query = (
        f"query($name: String!) {{ find{plural}({entity}_filter: {{name: {{value: $name, modifier: EQUALS}}}}) "
        f"{{ {plural.lower()} {{ id name }} }} }}"
    )
    for found in client.call(query, {"name": name})[f"find{plural}"][plural.lower()]:
        if found["name"] == name:
            return found["id"]
    if not apply:
        return None
    kind = entity[0].upper() + entity[1:]
    mutation = f"mutation($input: {kind}CreateInput!) {{ {entity}Create(input: $input) {{ id }} }}"
    return client.call(mutation, {"input": {"name": name, **(extra or {})}})[f"{entity}Create"]["id"]


def plan(item, meta, performer_id, studio_id, tag_ids, organized=False):
    """Changes to make to a Stash item: empty fields filled, IDs added.

    An ID of None (not created yet in preview mode) still counts as a change.
    """
    changes = {}
    for field in ("title", "details", "date", "code"):
        if meta.get(field) and not item.get(field):
            changes[field] = meta[field]
    urls = item.get("urls") or []
    if meta.get("url") and meta["url"] not in urls:
        changes["urls"] = urls + [meta["url"]]
    if not item.get("studio"):
        changes["studio_id"] = studio_id
    have = [p["id"] for p in item.get("performers") or []]
    if performer_id not in have:
        changes["performer_ids"] = have + [performer_id]
    have = [t["id"] for t in item.get("tags") or []]
    new = [t for t in tag_ids if t not in have]
    if new:
        changes["tag_ids"] = have + new
    if organized and not item.get("organized"):
        changes["organized"] = True
    return changes


def map_path(path, mappings):
    for local, remote in mappings:
        if path == local or path.startswith(local.rstrip("\\/") + os.sep) or path.startswith(local.rstrip("\\/") + "/"):
            mapped = remote.rstrip("\\/") + path[len(local.rstrip("\\/")):]
            return mapped.replace("\\", "/") if "/" in remote and "\\" not in remote else mapped
    return path


def scan(client, paths, timeout=3600, poll=2.0):
    """Ask Stash to scan the folders and wait for the job to finish."""
    job = client.call(
        "mutation($input: ScanMetadataInput!) { metadataScan(input: $input) }", {"input": {"paths": paths}}
    )["metadataScan"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.call("query($id: ID!) { findJob(input: {id: $id}) { status error } }", {"id": job})["findJob"]
        # A finished job can drop out of the queue before it's polled
        if status is None or status["status"] == "FINISHED":
            return
        if status["status"] in ("FAILED", "CANCELLED"):
            raise StashError(f"Stash scan {status['status'].lower()}: {status.get('error') or ''}".strip())
        time.sleep(poll)
    raise StashError(f"Stash scan didn't finish within {timeout}s")


def sync(
    databases,
    client,
    apply=False,
    studio="OnlyFans",
    tag_prefix="OnlyFans ",
    tags=True,
    organized=False,
    mappings=(),
    do_scan=False,
    sample=10,
):
    models = []
    for database in find_databases(databases):
        username, files = read_model(database)
        models.append((database, username, files))

    if do_scan and apply:
        folders = sorted(
            {map_path(os.path.commonpath([os.path.dirname(f["path"]) for f in files]), mappings) for _, _, files in models if files}
        )
        if folders:
            scan(client, folders)

    studio_id = find_or_create(client, "studio", studio, apply, {"urls": ["https://onlyfans.com"]})
    tag_cache = {}
    report = {"apply": apply, "models": [], "totals": defaultdict(int)}
    for database, username, files in models:
        name = username or Path(database).parent.name
        extra = {"urls": [f"https://onlyfans.com/{username}"]} if username else {}
        performer_id = find_or_create(client, "performer", name, apply, extra)
        counts = defaultdict(int)
        not_found = []
        for entry in files:
            kind = KINDS.get(entry["media_type"])
            if kind is None:
                counts["unsupported_type"] += 1
                continue
            stash_path = map_path(entry["path"], mappings)
            item = find_item(client, kind, stash_path)
            if item is None:
                counts["not_in_stash"] += 1
                if len(not_found) < sample:
                    not_found.append(stash_path)
                continue
            tag_ids = []
            if tags:
                for api_type in sorted(entry["api_types"]):
                    tag = f"{tag_prefix}{api_type}"
                    if tag not in tag_cache or tag_cache[tag] is None:
                        tag_cache[tag] = find_or_create(client, "tag", tag, apply)
                    tag_ids.append(tag_cache[tag])
            changes = plan(item, entry["meta"], performer_id, studio_id, tag_ids, organized)
            if not changes:
                counts["up_to_date"] += 1
                continue
            counts["updated" if apply else "to_update"] += 1
            if apply:
                client.call(UPDATE[kind], {"input": {"id": item["id"], **changes}})
        model = {
            "database": str(database),
            "performer": name,
            "performer_exists": performer_id is not None,
            "files": len(files),
            **counts,
        }
        if not_found:
            model["not_in_stash_sample"] = not_found
        report["models"].append(model)
        for key, value in counts.items():
            report["totals"][key] += value
        report["totals"]["files"] += len(files)
    report["totals"] = dict(report["totals"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="user_data.db files, or folders to search for them (e.g. <profile>/.data)")
    parser.add_argument("--apply", action="store_true", help="write to Stash; without it, only report")
    parser.add_argument("--url", default=os.environ.get("STASH_URL", DEFAULT_URL), help=f"Stash GraphQL URL (default {DEFAULT_URL}, or $STASH_URL)")
    parser.add_argument("--api-key", default=os.environ.get("STASH_API_KEY"), help="Stash API key, if Stash has a password (default $STASH_API_KEY)")
    parser.add_argument("--studio", default="OnlyFans", help="studio to set (default OnlyFans)")
    parser.add_argument("--tag-prefix", default="OnlyFans ", help="prefix for the source tags, e.g. 'OnlyFans Paid' (default 'OnlyFans ')")
    parser.add_argument("--no-tags", action="store_true", help="don't add source tags")
    parser.add_argument("--organized", action="store_true", help="also mark items as organized")
    parser.add_argument("--path-map", action="append", default=[], metavar="LOCAL=STASH", help="rewrite a path prefix when Stash sees the files elsewhere (e.g. in Docker); repeatable")
    parser.add_argument("--scan", action="store_true", help="with --apply, have Stash scan the download folders first and wait for it")
    args = parser.parse_args(argv)

    mappings = []
    for item in args.path_map:
        if "=" not in item:
            parser.error(f"--path-map needs LOCAL=STASH, got {item!r}")
        mappings.append(tuple(item.split("=", 1)))

    try:
        report = sync(
            args.paths,
            StashClient(args.url, args.api_key),
            apply=args.apply,
            studio=args.studio,
            tag_prefix=args.tag_prefix,
            tags=not args.no_tags,
            organized=args.organized,
            mappings=mappings,
            do_scan=args.scan,
        )
    except (StashError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
