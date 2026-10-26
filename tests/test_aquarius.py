import csv
import json
from pathlib import Path
import tempfile
import unittest
import aquarius as aq


SERIES = {'Identifier': 'AirTemp.+47m@Test', 'UniqueId': 'abc123', 'Unit': 'degC'}


class FakeClient:
    base = 'https://example.invalid/AQUARIUS/Publish/v2'

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, route, **params):
        self.calls.append(params)
        if self.fail and len(self.calls) == 2:
            raise aq.AquariusError('Simulated network failure')
        a, b = params['QueryFrom'], params['QueryTo']
        return {'Points': [
            {'Timestamp': a, 'NumericValue1': 0, 'ApprovalName1': 'Working', 'GradeCode1': -1},
            {'Timestamp': a, 'NumericValue1': None},
            {'Timestamp': b, 'NumericValue1': 999}  # API's inclusive boundary
        ]}


class DownloaderTests(unittest.TestCase):
    def test_dates_and_offsets(self):
        self.assertEqual(aq.iso(aq.parse_time('2026-01-01')), '2026-01-01T00:00:00Z')
        self.assertEqual(aq.iso(aq.parse_time('2026-08-01T00:00:00+02:00')), '2026-07-31T22:00:00Z')
        with self.assertRaises(ValueError):
            aq.parse_time('2026-01-01T00:00:00')

    def test_month_windows_cross_year_and_leap_day(self):
        parts = list(aq.windows(aq.parse_time('2023-12-31'), aq.parse_time('2024-03-01')))
        self.assertEqual(len(parts), 3)
        self.assertEqual((parts[-1][1] - parts[-1][0]).days, 29)
        self.assertTrue(all(a[1] == b[0] for a,b in zip(parts, parts[1:])))

    def test_export_boundaries_null_zero_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = FakeClient()
            result = aq.export_series(c, SERIES, aq.parse_time('2026-01-01'), aq.parse_time('2026-03-01'), Path(tmp))
            self.assertEqual(result['rows'], 4)
            self.assertEqual(result['numeric_rows'], 2)
            self.assertEqual(result['duplicate_timestamps'], 2)
            self.assertEqual(result['maximum'], 0)
            with (Path(tmp) / result['file']).open(newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]['ApprovalName'], 'Working')
            self.assertEqual(rows[0]['Value'], '0')
            self.assertEqual(rows[1]['Value'], '')
            self.assertEqual(len(c.calls), 2)

    def test_failure_manifest_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'download'
            with self.assertRaises(aq.AquariusError):
                aq.download(FakeClient(True), [SERIES], aq.parse_time('2026-01-01'), aq.parse_time('2026-03-01'), out)
            self.assertEqual(json.loads((out/'manifest.json').read_text())['status'], 'incomplete')
            self.assertEqual(len(list(out.glob('*.partial'))), 1)
            with self.assertRaises(aq.AquariusError):
                aq.download(FakeClient(), [SERIES], aq.parse_time('2026-01-01'), aq.parse_time('2026-02-01'), out)

    def test_complete_and_invalid_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'download'
            aq.download(FakeClient(), [SERIES], aq.parse_time('2026-01-01'), aq.parse_time('2026-02-01'), out)
            self.assertEqual(json.loads((out/'manifest.json').read_text())['status'], 'complete')
            self.assertTrue((out/'coverage.csv').exists())
            with self.assertRaises(aq.AquariusError):
                aq.download(FakeClient(), [SERIES], aq.parse_time('2026-03-01'), aq.parse_time('2026-02-01'), Path(tmp)/'bad')


if __name__ == '__main__':
    unittest.main()
