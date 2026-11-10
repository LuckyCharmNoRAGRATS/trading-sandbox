import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).parents[1] / 'tools/collect_paper_snapshot.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class Fake:
    def __init__(self, full=False, fail=False):
        self.full, self.fail, self.calls = full, fail, []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == '/v2/account':
            if self.fail:
                raise RuntimeError('SECRET MUST NOT APPEAR')
            return {'id': 'acct', 'equity': '100000'}
        if path == '/v2/positions':
            return []
        if path == '/v2/orders':
            return [{'id': str(i)} for i in range(500)] if self.full else []
        if path == '/v2/account/activities':
            if params.get('page_token'):
                return []
            return [{'id': str(i)} for i in range(100)]
        if path == '/v2/account/portfolio/history':
            return {'timestamp': [1793749200], 'equity': [100000]}
        raise AssertionError(path)


class CollectorTests(unittest.TestCase):
    def test_activity_pagination_and_historical_labels(self):
        client = Fake()
        report = c.collect(client, '2026-11-04')
        self.assertTrue(report['collection_ok'])
        self.assertFalse(report['reconciled'])
        self.assertFalse(report['execution_authorized'])
        calls = [q for p, q in client.calls if p.endswith('/activities')]
        self.assertEqual(calls[1]['page_token'], '99')
        self.assertEqual(len(report['sections']['activities']['data']), 100)

    def test_order_limit_marks_incomplete(self):
        report = c.collect(Fake(full=True), '2026-11-04')
        self.assertFalse(report['collection_ok'])
        self.assertEqual(report['sections']['open_orders']['status'], 'incomplete')

    def test_errors_preserve_other_sections_without_secrets(self):
        report = c.collect(Fake(fail=True), '2026-11-04')
        self.assertFalse(report['collection_ok'])
        self.assertNotIn('SECRET', str(report))
        self.assertEqual(report['sections']['activities']['status'], 'ok')

    def test_repeated_activity_page_rejected(self):
        class Repeated:
            def get(self, *args):
                return [{'id': str(i)} for i in range(100)]
        with self.assertRaises(c.CollectionError):
            c.activities(Repeated(), '2026-11-04')

    def test_page_budget_rejected(self):
        with self.assertRaises(c.CollectionError):
            c.activities(Fake(), '2026-11-04', max_pages=1)

    def test_invalid_date_before_reads(self):
        client = Fake()
        with self.assertRaises(ValueError):
            c.collect(client, 'not-a-date')
        self.assertEqual(client.calls, [])

    def test_endpoint_and_redirect_guard(self):
        reader = c.PaperReader('test', 'test')
        for path in ['/v2/orders/../account', 'https://api.alpaca.markets/v2/orders', '/v2/watchlists']:
            with self.assertRaises(c.CollectionError):
                reader.get(path)
        with self.assertRaises(c.CollectionError):
            c.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example')

    def test_transport_uses_only_get_on_paper(self):
        reader = c.PaperReader('test', 'test')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{}'
        with patch.object(reader.opener, 'open', return_value=Response()) as mocked:
            reader.get('/v2/account')
        req = mocked.call_args.args[0]
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(req.full_url, 'https://paper-api.alpaca.markets/v2/account')

    def test_private_non_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'evidence.json'
            c.save({'ok': True}, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                c.save({}, path)

    def test_invalid_serialization_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            with self.assertRaises(ValueError):
                c.save({'equity': float('nan')}, path)
            self.assertFalse(path.exists())

    def test_nonfinite_account_rejected(self):
        class Bad(Fake):
            def get(self, path, params=None):
                if path == '/v2/account':
                    return {'id': 'acct', 'equity': 'NaN'}
                return super().get(path, params)
        self.assertFalse(c.collect(Bad(), '2026-11-04')['collection_ok'])

    def test_duplicate_positions_rejected(self):
        class Bad(Fake):
            def get(self, path, params=None):
                if path == '/v2/positions':
                    return [{'symbol': 'SPY', 'qty': '1'}] * 2
                return super().get(path, params)
        report = c.collect(Bad(), '2026-11-04')
        self.assertEqual(report['sections']['positions']['status'], 'error')

    def test_malformed_activity_link_preserves_report(self):
        class Bad(Fake):
            def get(self, path, params=None):
                if path.endswith('/activities'):
                    return [{'id': 'a', 'order_id': {'bad': 'value'}}]
                return super().get(path, params)
        report = c.collect(Bad(), '2026-11-04')
        self.assertEqual(report['sections']['activity_linked_orders']['status'], 'error')
        self.assertEqual(report['sections']['account_after']['status'], 'ok')

    def test_missing_credentials(self):
        with self.assertRaises(c.CollectionError):
            c.PaperReader('', '')

    def test_linked_order_lookup_identity(self):
        oid = '12345678-1234-1234-1234-123456789012'
        class Linked(Fake):
            def get(self, path, params=None):
                if path.endswith('/activities'):
                    return [{'id': 'fill1', 'order_id': oid}]
                if path == '/v2/orders/' + oid:
                    return {'id': 'wrong'}
                return super().get(path, params)
        report = c.collect(Linked(), '2026-11-04')
        self.assertFalse(report['collection_ok'])
        self.assertEqual(report['sections']['activity_linked_orders']['status'], 'error')


if __name__ == '__main__':
    unittest.main()
