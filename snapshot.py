"""Save short-retention space-weather products exactly as published.

Each run fetches every source in SOURCES, writes the raw bytes under
snapshots/<source>/<YYYY>/<MM>/<YYYY-MM-DD>T<HHMM>Z.<ext>, and appends one row per
source to manifest.csv. One failing source never stops the others: its failure is
recorded in the manifest and the script exits 1 at the end.

Usage: python snapshot.py [--root DIR]

Standard library only, so the scheduled workflow needs nothing but Python.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import http.client
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

USER_AGENT = "boreale-archive (+https://github.com/hmin6/boreale-archive)"
TIMEOUT_S = 30
ATTEMPTS = 3


@dataclass(frozen=True)
class Source:
    name: str  # directory name under snapshots/
    url: str
    ext: str  # extension of the file as published
    gzip: bool = False  # store gzipped (large JSON only)


# Every URL here was confirmed against the live endpoint; see the README table for
# where each one is linked from and how long the provider keeps it.
SOURCES: tuple[Source, ...] = (
    Source(
        "swpc-3day-geomag", "https://services.swpc.noaa.gov/text/3-day-geomag-forecast.txt", "txt"
    ),
    Source("swpc-rsga", "https://services.swpc.noaa.gov/text/sgarf.txt", "txt"),
    Source("swpc-discussion", "https://services.swpc.noaa.gov/text/discussion.txt", "txt"),
    Source(
        "swpc-rtsw-wind",
        "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json",
        "json",
        gzip=True,
    ),
    Source(
        "swpc-rtsw-mag",
        "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json",
        "json",
        gzip=True,
    ),
    Source(
        "swpc-planetary-k",
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
        "json",
    ),
)

MANIFEST_NAME = "manifest.csv"
MANIFEST_FIELDS = (
    "fetched_at_utc",
    "source",
    "url",
    "http_status",
    "sha256",
    "size_bytes",
    "path",
    "error",
)


class FetchError(Exception):
    """A source could not be fetched. `status` is the HTTP code, or 0 if there was none."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def snapshot_path(source: Source, fetched_at: datetime) -> Path:
    """Path of a snapshot, relative to the repo root, named by its UTC fetch time."""
    if fetched_at.tzinfo is None:
        # A naive datetime would be read as local time, which differs between my
        # laptop and the GitHub runner. Refuse it rather than guess.
        raise ValueError("fetched_at must be timezone-aware")
    t = fetched_at.astimezone(UTC)
    ext = f"{source.ext}.gz" if source.gzip else source.ext
    return Path("snapshots", source.name, f"{t:%Y}", f"{t:%m}", f"{t:%Y-%m-%dT%H%MZ}.{ext}")


def manifest_row(
    source: Source,
    fetched_at: datetime,
    *,
    status: int,
    body: bytes | None = None,
    path: Path | None = None,
    error: str = "",
) -> dict[str, str]:
    """One manifest row. The hash and size describe the raw bytes, before any gzip."""
    return {
        "fetched_at_utc": fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source.name,
        "url": source.url,
        "http_status": str(status),
        "sha256": hashlib.sha256(body).hexdigest() if body is not None else "",
        "size_bytes": str(len(body)) if body is not None else "",
        # as_posix() so the manifest reads the same whether written on Windows or Linux.
        "path": path.as_posix() if path is not None else "",
        "error": error,
    }


def fetch(
    url: str,
    *,
    opener: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = ATTEMPTS,
) -> tuple[int, bytes]:
    """GET a URL, retrying connection errors and 5xx. Returns (status, raw body).

    urllib doesn't ask for a compressed transfer, so the body is the file's bytes as
    the server publishes them.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error = FetchError("no attempts made")
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=TIMEOUT_S) as response:
                return response.status, response.read()
        # HTTPError is a subclass of URLError, so it has to be caught first.
        except urllib.error.HTTPError as e:
            if e.code < 500:
                # 4xx won't fix itself by retrying (e.g. the file moved).
                raise FetchError(f"HTTP {e.code}", e.code) from e
            last_error = FetchError(f"HTTP {e.code}", e.code)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            last_error = FetchError(f"{type(e).__name__}: {e}")
        if attempt < attempts:
            sleep(2**attempt)
    raise last_error


def encode(source: Source, body: bytes) -> bytes:
    """Bytes to store on disk. mtime=0 keeps the gzip header free of a timestamp, so
    the same input always produces the same file."""
    return gzip.compress(body, compresslevel=9, mtime=0) if source.gzip else body


def save_source(
    source: Source,
    root: Path,
    fetch_fn: Callable[[str], tuple[int, bytes]],
    now_fn: Callable[[], datetime],
) -> dict[str, str]:
    """Fetch and store one source. Always returns a manifest row; never raises."""
    fetched_at = now_fn()
    status = 0
    try:
        status, body = fetch_fn(source.url)
        if status != 200:
            raise FetchError(f"HTTP {status}", status)
        if not body:
            raise FetchError("empty body", status)
        rel = snapshot_path(source, fetched_at)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # "x" mode fails if the file exists: archived files are never overwritten.
        with open(dest, "xb") as f:
            f.write(encode(source, body))
        return manifest_row(source, fetched_at, status=status, body=body, path=rel)
    except FetchError as e:
        return manifest_row(source, fetched_at, status=e.status, error=str(e))
    except FileExistsError as e:
        return manifest_row(
            source, fetched_at, status=status, error=f"file exists, not overwriting: {e.filename}"
        )
    except Exception as e:  # noqa: BLE001 - any bug in one source must not stop the rest
        return manifest_row(source, fetched_at, status=status, error=f"{type(e).__name__}: {e}")


def append_manifest(root: Path, rows: Iterable[dict[str, str]]) -> None:
    path = root / MANIFEST_NAME
    write_header = not path.exists() or path.stat().st_size == 0
    # newline="" plus lineterminator="\n": the csv module writes CRLF by default,
    # and the repo keeps text files LF.
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def utc_now() -> datetime:
    return datetime.now(UTC)


def run(
    root: Path,
    sources: Iterable[Source] = SOURCES,
    fetch_fn: Callable[[str], tuple[int, bytes]] = fetch,
    now_fn: Callable[[], datetime] = utc_now,
) -> int:
    rows = [save_source(s, root, fetch_fn, now_fn) for s in sources]
    append_manifest(root, rows)

    ok = sum(1 for r in rows if not r["error"])
    for r in rows:
        outcome = r["path"] if not r["error"] else f"FAILED ({r['error']})"
        print(f"{r['source']:<20} {r['http_status']:>3}  {outcome}")
    print(f"{ok}/{len(rows)} sources saved")

    # The workflow uses this in its commit message.
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"summary={ok}/{len(rows)}\n")

    return 0 if ok == len(rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."), help="repo root (default: .)")
    args = parser.parse_args(argv)
    return run(args.root)


if __name__ == "__main__":
    sys.exit(main())
