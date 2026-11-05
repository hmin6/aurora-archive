import csv
import gzip
import hashlib
import io
import socket
import urllib.error
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

import snapshot
from snapshot import FetchError, Source

TEXT = Source("swpc-test", "https://example.invalid/a.txt", "txt")
BIG_JSON = Source("swpc-big", "https://example.invalid/b.json", "json", gzip=True)
WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def read_manifest(root: Path) -> list[dict[str, str]]:
    with open(root / "manifest.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def path_str(source: Source, when: datetime) -> str:
    return snapshot.snapshot_path(source, when).as_posix()


# --- path building -----------------------------------------------------------


def test_path_zero_pads_month_day_hour_minute():
    assert path_str(TEXT, WHEN) == "snapshots/swpc-test/2026/01/2026-01-02T0304Z.txt"


def test_path_converts_to_utc_across_a_year_boundary():
    plus5 = timezone(timedelta(hours=5))
    local = datetime(2026, 1, 1, 1, 30, tzinfo=plus5)  # = 2025-12-31 20:30 UTC
    assert path_str(TEXT, local) == "snapshots/swpc-test/2025/12/2025-12-31T2030Z.txt"


def test_gzipped_source_gets_gz_suffix():
    assert path_str(BIG_JSON, WHEN).endswith("/2026-01-02T0304Z.json.gz")


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        snapshot.snapshot_path(TEXT, datetime(2026, 1, 2, 3, 4))


# --- manifest rows -----------------------------------------------------------


def test_manifest_row_for_success():
    body = b"line one\nline two\n"
    path = snapshot.snapshot_path(TEXT, WHEN)
    row = snapshot.manifest_row(TEXT, WHEN, status=200, body=body, path=path)
    assert row == {
        "fetched_at_utc": "2026-01-02T03:04:05Z",
        "source": "swpc-test",
        "url": "https://example.invalid/a.txt",
        "http_status": "200",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": str(len(body)),
        "path": "snapshots/swpc-test/2026/01/2026-01-02T0304Z.txt",
        "error": "",
    }
    assert tuple(row) == snapshot.MANIFEST_FIELDS


def test_manifest_row_for_failure_leaves_hash_size_and_path_empty():
    row = snapshot.manifest_row(TEXT, WHEN, status=503, error="HTTP 503")
    assert row["http_status"] == "503"
    assert row["sha256"] == row["size_bytes"] == row["path"] == ""
    assert row["error"] == "HTTP 503"


# --- a whole run, with fake fetchers -----------------------------------------


def fake_fetch(responses):
    """responses maps URL -> (status, body), or an exception to raise."""

    def fetch(url):
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


def test_one_failed_source_does_not_stop_the_others(tmp_path):
    ok_a = Source("ok-a", "https://example.invalid/ok-a.txt", "txt")
    down = Source("down", "https://example.invalid/down.txt", "txt")
    ok_b = Source("ok-b", "https://example.invalid/ok-b.txt", "txt")
    fetch = fake_fetch(
        {
            ok_a.url: (200, b"a\n"),
            down.url: FetchError("HTTP 503", 503),
            ok_b.url: (200, b"b\n"),
        }
    )

    code = snapshot.run(tmp_path, [ok_a, down, ok_b], fetch, lambda: WHEN)

    assert code == 1
    rows = read_manifest(tmp_path)
    assert [r["source"] for r in rows] == ["ok-a", "down", "ok-b"]
    assert rows[1]["http_status"] == "503"
    assert rows[1]["error"] == "HTTP 503"
    assert (tmp_path / rows[0]["path"]).read_bytes() == b"a\n"
    assert (tmp_path / rows[2]["path"]).read_bytes() == b"b\n"


def test_unexpected_exception_is_recorded_not_raised(tmp_path):
    fetch = fake_fetch({TEXT.url: ValueError("boom")})
    assert snapshot.run(tmp_path, [TEXT], fetch, lambda: WHEN) == 1
    assert read_manifest(tmp_path)[0]["error"] == "ValueError: boom"


@pytest.mark.parametrize(
    ("response", "error"),
    [((204, b""), "HTTP 204"), ((200, b""), "empty body")],
)
def test_non_200_and_empty_body_are_failures(tmp_path, response, error):
    assert snapshot.run(tmp_path, [TEXT], fake_fetch({TEXT.url: response}), lambda: WHEN) == 1
    row = read_manifest(tmp_path)[0]
    assert row["error"] == error
    assert row["path"] == ""
    assert not (tmp_path / "snapshots").exists()


def test_all_ok_returns_0_and_appends_without_repeating_header(tmp_path):
    fetch = fake_fetch({TEXT.url: (200, b"x\n")})
    assert snapshot.run(tmp_path, [TEXT], fetch, lambda: WHEN) == 0
    later = WHEN + timedelta(hours=12)
    assert snapshot.run(tmp_path, [TEXT], fetch, lambda: later) == 0

    raw = (tmp_path / "manifest.csv").read_bytes()
    assert raw.count(b"fetched_at_utc") == 1
    assert b"\r\n" not in raw  # LF only
    assert len(read_manifest(tmp_path)) == 2


def test_gzip_round_trip_matches_manifest_hash_and_is_deterministic(tmp_path):
    body = b'[{"time_tag": "2026-01-02T03:04:00", "bt": 4.1}]' * 100
    snapshot.run(tmp_path, [BIG_JSON], fake_fetch({BIG_JSON.url: (200, body)}), lambda: WHEN)

    row = read_manifest(tmp_path)[0]
    stored = (tmp_path / row["path"]).read_bytes()
    assert hashlib.sha256(gzip.decompress(stored)).hexdigest() == row["sha256"]
    assert row["size_bytes"] == str(len(body))  # raw size, not the gzipped size
    assert stored == snapshot.encode(BIG_JSON, body)  # same input, same bytes


def test_existing_snapshot_is_never_overwritten(tmp_path):
    dest = tmp_path / snapshot.snapshot_path(TEXT, WHEN)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"original\n")

    code = snapshot.run(tmp_path, [TEXT], fake_fetch({TEXT.url: (200, b"new\n")}), lambda: WHEN)

    assert code == 1
    assert dest.read_bytes() == b"original\n"
    row = read_manifest(tmp_path)[0]
    assert row["error"].startswith("file exists, not overwriting")
    assert row["path"] == ""


def test_summary_is_written_for_the_workflow(tmp_path, monkeypatch):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    snapshot.run(tmp_path, [TEXT], fake_fetch({TEXT.url: (200, b"x\n")}), lambda: WHEN)
    assert out.read_text(encoding="utf-8") == "summary=1/1\n"


# --- fetch retries -----------------------------------------------------------


class FakeResponse(io.BytesIO):
    status = 200


def opener_from(outcomes):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    opener.calls = calls
    return opener


def http_error(code):
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, None)


def test_fetch_retries_connection_errors_then_succeeds():
    sleeps = []
    opener = opener_from([urllib.error.URLError("reset"), TimeoutError("slow"), b"data"])
    assert snapshot.fetch(TEXT.url, opener=opener, sleep=sleeps.append) == (200, b"data")
    assert len(opener.calls) == 3
    assert sleeps == [2, 4]


def test_fetch_does_not_retry_4xx():
    opener = opener_from([http_error(404)])
    with pytest.raises(FetchError) as excinfo:
        snapshot.fetch(TEXT.url, opener=opener, sleep=lambda s: None)
    assert excinfo.value.status == 404
    assert len(opener.calls) == 1


def test_fetch_gives_up_after_repeated_5xx():
    opener = opener_from([http_error(503)] * 3)
    with pytest.raises(FetchError) as excinfo:
        snapshot.fetch(TEXT.url, opener=opener, sleep=lambda s: None)
    assert excinfo.value.status == 503
    assert len(opener.calls) == 3


def test_fetch_sends_a_user_agent():
    seen = {}

    def opener(request, timeout):
        seen["ua"] = request.get_header("User-agent")
        return FakeResponse(b"x")

    snapshot.fetch(TEXT.url, opener=opener)
    assert seen["ua"] == snapshot.USER_AGENT


def test_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 9))
