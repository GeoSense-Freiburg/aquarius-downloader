# AQUARIUS data downloader

Browse stations and measurements in an AQUARIUS Time-Series **Publish v2** server, select the series and time range you need, and download CSV files with units and quality metadata.

Built for EcoSense workflows at GeoSense Freiburg, but configurable for other compatible AQUARIUS servers. Requires Python **3.10 or newer**; no third-party packages are needed.

## Quick start

Clone the repository and open a terminal in its directory:

```sh
git clone https://github.com/GeoSense-Freiburg/aquarius-downloader.git
cd aquarius-downloader
python aquarius.py wizard
```

Enter your server URL and username. The password prompt is hidden. The wizard lets you:

1. Filter and select stations.
2. Browse measurements, units, descriptions and available date ranges.
3. Select one or more series by their displayed numbers.
4. Enter the start and end dates and a new output directory.
5. Review the selection and start the download.

Obtain the server address and your own credentials from your data administrator. Access may require the university network or VPN. If your server only supports HTTP, use `python aquarius.py --allow-http wizard` on the trusted university network/VPN. HTTP does not encrypt the password or data in transit.

## Repeatable downloads

The global `--server`, `--username` and `--allow-http` options go **before** the command. Connection settings can also come from `AQUARIUS_SERVER`, `AQUARIUS_USERNAME` and `AQUARIUS_PASSWORD` environment variables. Prefer the hidden password prompt for interactive use; never put passwords in shell arguments, notebooks or committed files.

### Browse the catalog

```sh
python aquarius.py --server https://your-aquarius-server --username YOUR_USER catalog --location Ecosense --output catalog.json
```

Add `--search AirTemp` to filter time-series identifiers, parameters, labels, descriptions and comments. A blank filter returns all accessible matches. The catalog JSON retains full station and series metadata, including sensor descriptions, date extents and configured thresholds where provided by the server.

### Select exact series and dates

Copy the `UniqueId` values from the catalog output:

```sh
python aquarius.py --server https://your-aquarius-server --username YOUR_USER download --catalog catalog.json --id SERIES_ID_1 --id SERIES_ID_2 --start 2026-01-01 --end 2026-08-01 --output downloads/weather-2026-Jan-Jul
```

The start is **inclusive**, and the end is **exclusive**. The example includes all of January through July 2026 in UTC. Date-only arguments mean midnight UTC. Offset-aware timestamps are supported, e.g. `2026-01-01T00:00:00+01:00`; timestamps without an offset are rejected. To cover local calendar months in Germany, supply the actual offsets at each boundary, e.g. January 1 at `+01:00` and August 1 at `+02:00`.

The downloader queries one series at a time in monthly windows. It filters the API's inclusive end boundary to prevent duplicated observations at chunk boundaries. Transient read errors are retried up to three attempts. Downloaded timestamps and values retain their source representation.

## Output

Each series gets its own CSV, named using its identifier and unique ID. Files use UTF-8, commas and decimal points, and can be opened in Excel, R or Python. Columns:

| Column | Meaning |
| --- | --- |
| Identifier, Unit | Original AQUARIUS series identifier and unit |
| Timestamp | Original timestamp including UTC offset |
| Value | Numeric value; empty when the API returns a gap marker |
| GradeCode, GradeName | AQUARIUS grade metadata |
| Method | Method reported by the server |
| ApprovalLevel, ApprovalName | Approval metadata, such as Working |
| Qualifiers | JSON list of qualifiers returned by the endpoint |

Additional files:

- `selected_series.json`: full metadata for the selected measurements.
- `coverage.csv`: row counts, numeric counts, duplicate timestamps, first/last observations and extrema.
- `manifest.json`: query boundaries, retrieval time, endpoint, completion state, monthly numeric counts and approval counts.

No interpolation, averaging, gap filling, unit conversion or additional quality filtering is applied. Empty months remain empty. A series marked **Working** is not necessarily final or validated. Catalog start/end dates do not guarantee uninterrupted data. The CSV contains values returned by `GetTimeSeriesData`; it is not an untouched logger-data archive or an export of all correction histories. Inspect source metadata and consult the data manager when choosing series.

The output directory must be empty. A failed run leaves a manifest marked `incomplete`, completed CSVs and a `.partial` file for the interrupted series. Keep these for diagnosis and retry into a new folder; automatic resume is not implemented. Do not treat incomplete exports as complete datasets.

## Choosing EcoSense weather measurements

For ambient conditions, inspect these station/height combinations in the current catalog:

| Purpose | Station | Series to inspect |
| --- | --- | --- |
| Above-canopy weather | `Ecosense_MainTower` | `AirTemp`, `RelHumidity`, `WindVel`, `WindGustVel`, corrected `WindDir` at +47 m |
| Vertical temperature and humidity profiles | Main, Beech and DouglasFir towers | `AirTemp` and `RelHumidity` at +2, +18 and +27 m |
| Candidate reference weather station | `Ecosense_Meteo_Parking` | Temperature/humidity at +200 cm; inspect station metadata before assuming reference comparability |
| Solar radiation | Main tower | Incoming shortwave radiation and incoming PAR at +46 m; SunTracker channels where available |

Check the labels every time: historical backup channels may now be labelled “don't use”. Cuvette, instrument-internal and soil temperature measurements are not interchangeable with ambient air measurements. Net radiation, shortwave radiation and PAR are different physical quantities with different units. Canopy position depends on the local stand; a nominal measurement height alone does not establish whether it is within or above the canopy.

## Development and validation

```sh
python -m unittest discover -s tests -v
python aquarius.py --help
```

Tests use a simulated server response and cover UTC conversion, monthly boundaries, missing data, quality fields, duplicate detection and interrupted downloads. No credentials are needed for tests. The initial implementation was also checked against the EcoSense AQUARIUS Publish v2 server.

## Data access and licensing

The MIT license covers this downloader's code. It does **not** grant access to or redistribution rights for measurements. Respect the data owner's access rules, licenses and citation requirements. Credentials, catalogs, notebooks and downloaded data are excluded from Git by default. This repository contains no EcoSense observation data or account credentials.
