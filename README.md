# aurora-archive

Raw snapshots of space-weather forecasts and real-time data that their publishers only
keep for days or weeks, saved twice a day exactly as published.

## Why
NOAA keeps its 3-day geomagnetic forecast for 75 days and its real-time solar wind for
only 24 hours. Anything not saved is gone. These as-issued records are Aurora's forecast
benchmark and the only record of what a live model actually sees, since the OMNI
dataset is reprocessed later. This repo saves the raw files: no parsing, no cleaning.

## What is archived
Every URL below was found from the provider's own product pages and confirmed against
the live endpoint on 2026-11-05.

| Directory | Product | URL | Linked from | Provider retention |
|---|---|---|---|---|
| `swpc-3day-geomag` | 3-Day Geomagnetic Forecast (Ap, Kp, storm probabilities), issued daily 22:05 UTC | https://services.swpc.noaa.gov/text/3-day-geomag-forecast.txt | [product page](https://www.spaceweather.gov/products/3-day-geomagnetic-forecast) | 75 days (product page; FTP `pub/forecasts/geomag_forecast/`) |
| `swpc-rsga` | Report and Forecast of Solar and Geophysical Activity (RSGA), issued daily 22:00 UTC | https://services.swpc.noaa.gov/text/sgarf.txt | [product page](https://www.spaceweather.gov/products/report-and-forecast-solar-and-geophysical-activity) | Last 75 reports (FTP `pub/forecasts/RSGA/README`) |
| `swpc-discussion` | Forecast Discussion, issued 00:30 and 12:30 UTC | https://services.swpc.noaa.gov/text/discussion.txt | [product page](https://www.spaceweather.gov/products/forecast-discussion) | Last 75 reports, about 37 days (FTP `pub/forecasts/discussion/README`) |
| `swpc-rtsw-wind` | Real-time solar wind plasma, 1-minute, SOLAR-1 (primary) plus ACE and IMAP | https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json | [solar wind page](https://www.spaceweather.gov/products/solar-wind), Data tab | Rolling 24 hours (measured) |
| `swpc-rtsw-mag` | Real-time interplanetary magnetic field, 1-minute, same spacecraft | https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json | [solar wind page](https://www.spaceweather.gov/products/solar-wind), Data tab | Rolling 24 hours (measured) |
| `swpc-planetary-k` | Preliminary 3-hourly planetary Kp (with `station_count`) | https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json | [planetary K-index page](https://www.spaceweather.gov/products/planetary-k-index) | Rolling 7 days (measured) |

Notes:
- **Solar wind source.** Since 2026-06-30 SWPC uses SOLAR-1 as its primary solar wind
  spacecraft, with ACE as backup, and has stopped ingesting DSCOVR
  ([notice](https://www.spaceweather.gov/news/solar-wind-data-and-display-changes)). The
  files hold rows from every spacecraft; the `active` field marks the one in use.
- **Why twice a day.** The solar wind files hold only 24 hours, so a once-a-day run that
  starts late would leave a permanent gap. Runs at 05:17 and 17:17 UTC overlap by about
  12 hours, and they also catch both daily forecast discussions.
- **"As first issued" Kp.** Preliminary Kp values are revised as more stations report.
  A twice-daily snapshot catches each value within about 12 hours of issue, not
  necessarily its very first version.
- **Not yet archived:** the GFZ Potsdam Kp forecast. Its server
  (`spaceweather.gfz.de`) refused connections when this was built, so its URL could not
  be confirmed. Tracked in [#2](https://github.com/hmin6/aurora-archive/issues/2).
- **Format changes.** NOAA announces product and format changes in its
  [news archive](https://www.spaceweather.gov/news-archive). `www.swpc.noaa.gov` now
  redirects to `www.spaceweather.gov`; the data files are served from
  `services.swpc.noaa.gov`.

## Layout
```
snapshots/<source>/<YYYY>/<MM>/<YYYY-MM-DD>T<HHMM>Z.<ext>
manifest.csv
```
The timestamp is the UTC fetch time. The two solar wind files (about 2.5 MB and 1.4 MB
each) are stored gzipped as `.json.gz`; everything else is stored byte for byte as
served. Snapshot files are marked `-text` in `.gitattributes`, so git never changes their
line endings.

`manifest.csv` gets one row per source per run, including failures:

| Column | Meaning |
|---|---|
| `fetched_at_utc` | When the request was made, e.g. `2026-11-06T05:17:42Z` |
| `source` | Directory name from the table above |
| `url` | URL fetched |
| `http_status` | HTTP status, or `0` if no response was received |
| `sha256` | SHA-256 of the raw response bytes (before gzip) |
| `size_bytes` | Size of the raw response bytes (before gzip) |
| `path` | Snapshot file, relative to the repo root |
| `error` | Empty on success; otherwise why the source failed |

To check a file against its row:
```
sha256sum snapshots/swpc-rsga/2026/11/2026-11-06T0517Z.txt
gunzip -c snapshots/swpc-rtsw-wind/2026/11/2026-11-06T0517Z.json.gz | sha256sum
```

## How it runs
`.github/workflows/snapshot.yml` runs `python snapshot.py` at 05:17 and 17:17 UTC (and on
manual dispatch), then commits the new files and manifest rows to main with the
workflow's `GITHUB_TOKEN`. If a source fails, the others are still saved, the failure is
written to the manifest, and the run is marked failed. The manifest is committed on every
run, so the repo never goes 60 days without a commit (when GitHub would disable the
schedule).

Expected growth is about 420 kB a day, measured from the gzipped solar wind files. It
will be re-measured after the first week (`git count-objects -vH`); if it's too fast,
the plan is to switch to smaller files with a weekly backstop.

## Development
```
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python snapshot.py --root <scratch dir>   # live fetch into a scratch directory
```

## Credits
- **NOAA Space Weather Prediction Center (SWPC).** Data courtesy of NOAA SWPC. NOAA/NWS
  information is in the public domain
  ([NWS disclaimer](https://www.weather.gov/disclaimer)); the NOAA material in
  `snapshots/` is not subject to copyright protection. The files are unmodified copies
  (the solar wind files are gzipped). This archive is not affiliated with or endorsed
  by NOAA or NWS, and these copies are not official government material.
