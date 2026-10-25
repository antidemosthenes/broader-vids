from dataclasses import dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import re
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

from .providers import YOUTUBE_ID
from .storage import utc_now


BILIBILI_ID = re.compile(r"BV1[1-9A-HJ-NP-Za-km-z]{9}\Z")
LINE_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class Source:
    platform: str
    url: str
    compressed: bool
    limitations: tuple[str, ...]
    md5: str | None = None
    discovery_eligible: bool = False


SOURCES = {
    "chinaopen": Source("bilibili", "https://raw.githubusercontent.com/JewelChen2019/ChinaOpen/main/ChinaOpen-50K-url.txt", False,
        ("Rejected for recommendation discovery: uploads stop in September 2021; retained only as a test fixture.",
         "Curated research release; durations are 3–608 seconds and content filters exclude many substantive formats.",
         "Links without a part query refer to the default first part; other parts are not enumerated.",
         "Current availability, duration and playback access require refresh.")),
    "youniverse": Source("youtube", "https://zenodo.org/records/4650046/files/yt_metadata_en.jsonl.gz?download=1", True,
        ("Rejected for recommendation discovery: English-language metadata crawled in 2019; retained only as a test fixture.",
         "Channel selection favored more than 10,000 subscribers and more than ten videos.",
         "A bounded prefix preview is not representative of the full source."), "0514b2ee52ffaa2c9c27c539038feb60"),
}


class DatasetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {"zenodo.org", "raw.githubusercontent.com"}:
            raise ValueError("Dataset redirect leaves the declared HTTPS source hosts.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_catalog(store, source_name, max_bytes=8 * 1024 * 1024, preview_rows=2000):
    source = SOURCES[source_name]
    if not 1024 <= max_bytes <= 64 * 1024 * 1024 or not 100 <= preview_rows <= 100000:
        raise ValueError("Use a download cap of 1 KiB–64 MiB and a preview size of 100–100,000 rows.")
    directory = store.root / "catalogs" / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Deepvids/0.1 catalog experiment", "Accept-Encoding": "identity"}
    if source.compressed:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    request = urllib.request.Request(source.url, headers=headers)
    handle = tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False)
    temporary = Path(handle.name)
    try:
        with handle, urllib.request.build_opener(DatasetRedirect()).open(request, timeout=30) as response:
            status = response.status
            if status not in (200, 206):
                raise ValueError(f"Unexpected dataset HTTP status {status}.")
            content_range = response.headers.get("Content-Range", "")
            if status == 206 and not re.fullmatch(r"bytes 0-\d+/\d+", content_range):
                raise ValueError("Dataset range response does not start at byte zero.")
            length_header = response.headers.get("Content-Length")
            response_length = int(length_header) if length_header and length_header.isdigit() else None
            count = 0
            eof = False
            while count < max_bytes:
                chunk = response.read(min(65536, max_bytes - count))
                if not chunk:
                    eof = True
                    break
                handle.write(chunk)
                count += len(chunk)
            if response_length is not None and count >= response_length:
                eof = True
            source_length = int(content_range.rsplit("/", 1)[1]) if status == 206 else response_length
            complete = eof and (source_length is None or source_length == count)
            receipt = {"source_url": source.url, "final_url": response.geturl(), "fetched_at": utc_now(),
                       "http_status": status, "content_range": content_range, "downloaded_bytes": count,
                       "advertised_source_bytes": source_length, "resource_complete": complete,
                       "etag": response.headers.get("ETag")}
        if not source.compressed and not complete:
            raise ValueError("ChinaOpen URL list exceeded the download cap or was incomplete.")
        sha256, md5 = file_hashes(temporary)
        suffix = ".jsonl.gz" if source.compressed else ".tsv"
        target = directory / f"{source_name}-{sha256[:16]}{suffix}"
        temporary.replace(target)
        receipt.update(sha256=sha256, md5=md5)
        target.with_suffix(target.suffix + ".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        scope = "prefix_preview" if source.compressed else "source_snapshot"
        return import_catalog(store, source_name, target, scope=scope,
                              row_limit=preview_rows if source.compressed else None, receipt=receipt)
    finally:
        temporary.unlink(missing_ok=True)


def file_hashes(path):
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def parse_row(source_name, line):
    if source_name == "chinaopen":
        fields = line.strip().split()
        if len(fields) != 2 or not BILIBILI_ID.fullmatch(fields[0]):
            return None
        url = urllib.parse.urlsplit(fields[1])
        if url.scheme not in {"http", "https"} or url.hostname != "www.bilibili.com" or url.path.rstrip("/") != f"/video/{fields[0]}":
            return None
        query = urllib.parse.parse_qs(url.query)
        if set(query) - {"p"} or len(query.get("p", ["1"])) != 1:
            return None
        part = query.get("p", ["1"])[0]
        if not part.isdigit() or not 1 <= int(part) <= 10000:
            return None
        return {"video_id": fields[0], "part_index": int(part), "url": fields[1]}
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("display_id"), str) or not YOUTUBE_ID.fullmatch(value["display_id"]):
        return None
    result = {"video_id": value["display_id"], "part_index": 1,
              "url": "https://www.youtube.com/watch?v=" + value["display_id"]}
    for key in ("title", "description", "channel_id", "upload_date", "categories", "crawl_date"):
        if isinstance(value.get(key), str):
            result[key] = value[key]
    for key in ("duration", "view_count", "like_count", "dislike_count"):
        number = value.get(key)
        if type(number) in (int, float) and math.isfinite(number) and number >= 0:
            result[key] = number
    return result


def import_catalog(store, source_name, path, scope="local_file", row_limit=None, receipt=None, max_seconds=3600):
    source = SOURCES[source_name]
    if scope not in {"local_file", "prefix_preview", "source_snapshot"}:
        raise ValueError("Unknown catalog scope.")
    if row_limit is not None and not 1 <= row_limit <= 1000000:
        raise ValueError("A row limit must be between 1 and 1,000,000.")
    if not 0 < max_seconds <= 3600:
        raise ValueError("The catalog import budget must be at most 3,600 seconds.")
    if row_limit is not None:
        scope = "prefix_preview"
    path = Path(path).resolve()
    sha256, md5 = file_hashes(path)
    if scope == "source_snapshot":
        if source.compressed:
            if md5 != source.md5:
                raise ValueError("The file checksum does not match the complete published YouNiverse metadata file.")
        elif not receipt or not receipt.get("resource_complete") or receipt.get("sha256") != sha256:
            raise ValueError("A complete ChinaOpen source snapshot requires a verified download receipt.")
    if scope == "prefix_preview" and row_limit is None:
        raise ValueError("Preview imports require an explicit row limit.")
    manifest = {"source": source_name, "source_url": source.url, "scope": scope, "file_path": str(path),
                "sha256": sha256, "md5": md5, "row_limit": row_limit, "receipt": receipt,
                "sampling_population": "Distinct linked video URLs in this imported catalog",
                "sampling_unit": "linked_video_part", "limitations": list(source.limitations),
                "rows_read": 0, "invalid_rows": 0, "blank_rows": 0, "duplicate_rows": 0,
                "reached_eof": False, "truncated_compressed_preview": False}
    catalog_id = uuid.uuid4().hex
    store.db.execute("INSERT INTO catalogs VALUES (?,?,?,?,?,?,?,?)", (catalog_id, source_name, source.platform,
                     scope, "importing", utc_now(), json.dumps(manifest), 0))
    store.db.commit()
    count = 0
    started = time.monotonic()
    try:
        opener = gzip.open if source.compressed else open
        with store.db, opener(path, "rt", encoding="utf-8-sig") as handle:
            while row_limit is None or manifest["rows_read"] < row_limit:
                if time.monotonic() - started >= max_seconds:
                    raise ValueError("Catalog import time budget exhausted; no partial catalog was published.")
                try:
                    line = handle.readline(LINE_LIMIT + 1)
                except EOFError:
                    if scope != "prefix_preview":
                        raise
                    manifest["truncated_compressed_preview"] = True
                    break
                if not line:
                    manifest["reached_eof"] = True
                    break
                if len(line) > LINE_LIMIT:
                    raise ValueError("Catalog row exceeded the 1 MiB text limit.")
                manifest["rows_read"] += 1
                if not line.strip():
                    manifest["blank_rows"] += 1
                    continue
                item = parse_row(source_name, line)
                if item is None:
                    manifest["invalid_rows"] += 1
                    continue
                cursor = store.db.execute("INSERT OR IGNORE INTO catalog_items VALUES (?,?,?,?,?)",
                    (catalog_id, count + 1, item["video_id"], item["part_index"], json.dumps(item, ensure_ascii=False, allow_nan=False)))
                if cursor.rowcount:
                    count += 1
                else:
                    manifest["duplicate_rows"] += 1
            if count == 0:
                raise ValueError("The catalog contained no valid linked video IDs.")
            manifest["import_seconds"] = time.monotonic() - started
            store.db.execute("UPDATE catalogs SET status='ready',manifest=?,item_count=? WHERE id=?",
                             (json.dumps(manifest), count, catalog_id))
    except Exception as error:
        manifest["error"] = str(error)
        store.db.execute("UPDATE catalogs SET status='failed',manifest=? WHERE id=?", (json.dumps(manifest), catalog_id))
        store.db.commit()
        raise
    return get_catalog(store, catalog_id)


def get_catalog(store, catalog_id):
    store.validate_id(catalog_id)
    row = store.db.execute("SELECT * FROM catalogs WHERE id=?", (catalog_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown catalog: {catalog_id}")
    result = dict(row)
    result["manifest"] = json.loads(result["manifest"])
    return result


def list_catalogs(store):
    return [get_catalog(store, row[0]) for row in store.db.execute("SELECT id FROM catalogs ORDER BY created_at")]


def sample_catalog(store, catalog_id, count=100, seed=0):
    catalog = get_catalog(store, catalog_id)
    if catalog["status"] != "ready":
        raise ValueError("Only fully imported local catalogs can be sampled.")
    if type(count) is not int or not 1 <= count <= min(catalog["item_count"], 10000):
        raise ValueError("Sample size must be positive, at most 10,000, and no larger than the catalog.")
    if type(seed) is not int or not -(1 << 63) <= seed < (1 << 63):
        raise ValueError("The seed must be a signed 64-bit integer.")
    ordinals = random.Random(seed).sample(range(1, catalog["item_count"] + 1), count)
    items = []
    for ordinal in ordinals:
        row = store.db.execute("SELECT payload FROM catalog_items WHERE catalog_id=? AND ordinal=?", (catalog_id, ordinal)).fetchone()
        if row is None:
            raise ValueError("Catalog ordinals are inconsistent; sampling was not recorded.")
        items.append(json.loads(row[0]))
    draw_id = uuid.uuid4().hex
    result = {"draw_id": draw_id, "catalog_id": catalog_id, "platform": catalog["platform"],
              "source": catalog["source"], "scope": catalog["scope"], "catalog_size": catalog["item_count"],
              "catalog_sha256": catalog["manifest"]["sha256"], "seed": seed, "sample_size": count,
              "sampling_population": catalog["manifest"]["sampling_population"],
              "sampling_unit": catalog["manifest"]["sampling_unit"], "availability_checked": False,
              "selection": "Uniform without replacement within the declared local catalog", "items": items}
    directory = store.root / "catalogs" / "draws"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{draw_id}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    store.db.execute("INSERT INTO catalog_draws VALUES (?,?,?,?,?,?)",
                     (draw_id, catalog_id, seed, count, utc_now(), json.dumps(ordinals)))
    store.db.commit()
    result["artifact_path"] = str(target)
    return result
