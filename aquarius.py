"""Browse AQUARIUS Publish v2 and export selected time series. Python 3.10+."""
import argparse
import collections
import csv
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class AquariusError(Exception):
    pass


class Client:
    def __init__(self, server, username, password, allow_http=False):
        parsed = urlsplit(server)
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise AquariusError('Provide a server URL without credentials, query or fragment.')
        if parsed.scheme == 'http' and not allow_http:
            raise AquariusError('This server uses HTTP. Use a trusted university network/VPN and --allow-http if required.')
        self.base = server.rstrip('/') + '/AQUARIUS/Publish/v2'
        self.token = None
        payload = json.dumps({'Username': username, 'EncryptedPassword': password}).encode()
        self.token = self._request('/session', payload, 'POST').decode().strip('"')

    def _request(self, route, payload=None, method='GET'):
        headers = {'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if self.token:
            headers['X-Authentication-Token'] = self.token
        for attempt in range(3):
            try:
                with urlopen(Request(self.base + route, data=payload, headers=headers, method=method), timeout=180) as r:
                    return r.read()
            except HTTPError as e:
                if method == 'GET' and e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise AquariusError(f'AQUARIUS returned HTTP {e.code}; check access, credentials and selected series.') from None
            except (URLError, TimeoutError):
                if method == 'GET' and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise AquariusError('Cannot reach AQUARIUS; check server address and university network/VPN.') from None

    def get(self, route, **params):
        return json.loads(self._request(route + ('?' + urlencode(params) if params else '')))

    def close(self):
        if self.token:
            try:
                self._request('/session', method='DELETE')
            except AquariusError:
                pass
            self.token = None


def parse_time(value):
    """Date-only inputs mean midnight UTC; timestamps must have an offset."""
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        value += 'T00:00:00+00:00'
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp must include Z or a UTC offset.')
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def windows(start, end):
    while start < end:
        boundary = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        stop = min(boundary, end)
        yield start, stop
        start = stop


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def catalog(client, location_filter='', search=''):
    locations = client.get('/GetLocationDescriptionList')['LocationDescriptions']
    locations = [x for x in locations if location_filter.casefold() in (x['Identifier'] + ' ' + x.get('Name', '')).casefold()]
    series = []
    for loc in locations:
        found = client.get('/GetTimeSeriesDescriptionList', LocationIdentifier=loc['Identifier'])['TimeSeriesDescriptions']
        for s in found:
            haystack = ' '.join(str(s.get(k, '')) for k in ('Identifier', 'Parameter', 'Label', 'Description', 'Comment'))
            if search.casefold() in haystack.casefold():
                series.append(s)
    return {'locations': locations, 'series': series}


FIELDS = ['Identifier', 'Unit', 'Timestamp', 'Value', 'GradeCode', 'GradeName', 'Method', 'ApprovalLevel', 'ApprovalName', 'Qualifiers']


def export_series(client, series, start, end, directory):
    """Bounded monthly queries; preserve values and quality fields, without filling gaps."""
    name = re.sub(r'[^A-Za-z0-9_.+-]', '_', series['Identifier'])[:150] + '_' + series['UniqueId'] + '.csv'
    dest = directory / name
    if dest.exists():
        raise AquariusError(f'Output already exists: {name}. Choose a new output directory.')
    partial = dest.with_suffix('.csv.partial')
    count = valid = duplicates = 0
    first = last = None
    minimum = maximum = None
    monthly = collections.Counter()
    approval = collections.Counter()
    seen = set()
    with partial.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        for a, b in windows(start, end):
            response = client.get('/GetTimeSeriesData', TimeSeriesUniqueIds=series['UniqueId'], QueryFrom=iso(a), QueryTo=iso(b), IncludeGapMarkers='true')
            points = response.get('Points', [])
            for p in sorted(points, key=lambda p: parse_time(p['Timestamp'])):
                dt = parse_time(p['Timestamp'])
                # The API includes QueryTo. Keep half-open windows to avoid duplicates.
                if not a <= dt < b:
                    continue
                duplicates += dt in seen
                seen.add(dt)
                value = p.get('NumericValue1')
                is_numeric = isinstance(value, (int, float)) and math.isfinite(value)
                if is_numeric:
                    valid += 1
                    monthly[dt.strftime('%Y-%m')] += 1
                    first = min(first, dt) if first else dt
                    last = max(last, dt) if last else dt
                    minimum = min(minimum, value) if minimum is not None else value
                    maximum = max(maximum, value) if maximum is not None else value
                writer.writerow([series['Identifier'], series.get('Unit', ''), p['Timestamp'], value,
                                 p.get('GradeCode1'), p.get('GradeName1'), p.get('Method1'),
                                 p.get('ApprovalLevel1'), p.get('ApprovalName1'), json.dumps(p.get('Qualifiers1', []))])
                count += 1
                approval[p.get('ApprovalName1', 'Unspecified')] += 1
    partial.replace(dest)
    return {'identifier': series['Identifier'], 'unique_id': series['UniqueId'], 'unit': series.get('Unit'),
            'file': name, 'rows': count, 'numeric_rows': valid, 'duplicate_timestamps': duplicates,
            'first_numeric_utc': iso(first) if first else None, 'last_numeric_utc': iso(last) if last else None,
            'minimum': minimum, 'maximum': maximum, 'monthly_numeric_rows': dict(monthly), 'approval_counts': dict(approval)}


def download(client, selected, start, end, output):
    if end <= start:
        raise AquariusError('End must be after start (end is exclusive).')
    if not selected:
        raise AquariusError('No series selected.')
    if output.exists() and any(output.iterdir()):
        raise AquariusError('Output directory must be empty; existing downloads are never overwritten.')
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'endpoint': client.base + '/GetTimeSeriesData', 'retrieved_at_utc': iso(datetime.now(timezone.utc)),
                'start_inclusive_utc': iso(start), 'end_exclusive_utc': iso(end), 'status': 'in_progress', 'series': []}
    write_json(output / 'selected_series.json', selected)
    write_json(output / 'manifest.json', manifest)
    try:
        for i, s in enumerate(selected, 1):
            result = export_series(client, s, start, end, output)
            manifest['series'].append(result)
            write_json(output / 'manifest.json', manifest)
            print(f'{i}/{len(selected)} {s["Identifier"]}: {result["numeric_rows"]} numeric rows', flush=True)
    except Exception:
        manifest['status'] = 'incomplete'
        write_json(output / 'manifest.json', manifest)
        raise
    manifest['status'] = 'complete'
    write_json(output / 'manifest.json', manifest)
    with (output / 'coverage.csv').open('w', newline='', encoding='utf-8') as f:
        fields = ['identifier', 'unit', 'file', 'rows', 'numeric_rows', 'duplicate_timestamps', 'first_numeric_utc', 'last_numeric_utc', 'minimum', 'maximum']
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(manifest['series'])


def choose(items, label):
    for i, item in enumerate(items, 1):
        print(f'{i:4}  {label(item)}')
    answer = input('Select comma-separated numbers (or all): ').strip()
    if answer.lower() == 'all':
        return items
    indices = list(dict.fromkeys(int(n.strip()) - 1 for n in answer.split(',')))
    if any(i < 0 or i >= len(items) for i in indices):
        raise ValueError('Selection is outside the displayed list.')
    return [items[i] for i in indices]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--server', default=os.getenv('AQUARIUS_SERVER'))
    p.add_argument('--username', default=os.getenv('AQUARIUS_USERNAME'))
    p.add_argument('--allow-http', action='store_true')
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('catalog', help='Browse station and time-series metadata')
    c.add_argument('--location', default='')
    c.add_argument('--search', default='')
    c.add_argument('--output', type=Path, default=Path('catalog.json'))
    d = sub.add_parser('download', help='Download exact series IDs from a saved catalog')
    d.add_argument('--catalog', type=Path, required=True)
    d.add_argument('--id', action='append', required=True, help='Exact UniqueId; repeat for multiple series')
    d.add_argument('--start', required=True)
    d.add_argument('--end', required=True, help='Exclusive end; use August 1 to include all July')
    d.add_argument('--output', type=Path, required=True)
    sub.add_parser('wizard', help='Interactively choose stations, variables and time range')
    args = p.parse_args(argv)
    client = None
    try:
        server = args.server or input('AQUARIUS server URL: ').strip()
        user = args.username or input('Username: ').strip()
        password = os.getenv('AQUARIUS_PASSWORD') or getpass.getpass('Password (hidden): ')
        client = Client(server, user, password, args.allow_http)
        del password
        if args.command == 'catalog':
            data = catalog(client, args.location, args.search)
            write_json(args.output, data)
            for s in data['series']:
                print(f'{s["UniqueId"]}  {s["Identifier"]} [{s.get("Unit", "")}]  {s.get("CorrectedStartTime", "?")} — {s.get("CorrectedEndTime", "?")}')
            print(f'Saved {len(data["series"])} series to {args.output}')
        elif args.command == 'download':
            data = json.loads(args.catalog.read_text(encoding='utf-8'))
            wanted = set(args.id)
            selected = [s for s in data['series'] if s['UniqueId'] in wanted]
            if {s['UniqueId'] for s in selected} != wanted:
                raise AquariusError('One or more requested IDs are absent from the catalog.')
            download(client, selected, parse_time(args.start), parse_time(args.end), args.output)
        else:
            locations = client.get('/GetLocationDescriptionList')['LocationDescriptions']
            text = input('Station-name filter (blank = all): ').casefold()
            locations = [x for x in locations if text in x['Identifier'].casefold()]
            if not locations:
                raise AquariusError('No matching stations.')
            chosen = choose(locations, lambda x: x['Identifier'])
            series = []
            for loc in chosen:
                series.extend(client.get('/GetTimeSeriesDescriptionList', LocationIdentifier=loc['Identifier'])['TimeSeriesDescriptions'])
            text = input('Variable filter (e.g. AirTemp, blank = all): ').casefold()
            series = [s for s in series if text in (s['Identifier'] + ' ' + s.get('Description', '')).casefold()]
            if not series:
                raise AquariusError('No matching series.')
            selected = choose(series, lambda s: f'{s["Identifier"]} [{s.get("Unit", "")}] {s.get("Description", "")} | {s.get("CorrectedStartTime", "?")} to {s.get("CorrectedEndTime", "?")}')
            start = parse_time(input('Start inclusive (YYYY-MM-DD, UTC): ').strip())
            end = parse_time(input('End exclusive (YYYY-MM-DD, UTC): ').strip())
            output = Path(input('New output folder: ').strip())
            print(f'Download {len(selected)} series from {iso(start)} to {iso(end)} (exclusive).')
            if input('Proceed? [y/N] ').strip().lower() == 'y':
                download(client, selected, start, end, output)
    except (AquariusError, ValueError, KeyError, OSError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
