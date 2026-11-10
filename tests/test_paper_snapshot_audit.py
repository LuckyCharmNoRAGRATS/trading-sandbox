import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('audit', Path(__file__).parents[1] / 'tools/audit_paper_snapshot.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def fixture():
    sections = {name: {'status': 'ok', 'data': [], 'api_page_exhausted': True} for name in a.REQUIRED}
    for name in ('account_before', 'account_after'):
        sections[name]['data'] = {'id': 'account', 'equity': '100'}
    sections['activities']['data'] = [{'id': 'fill1', 'activity_type': 'FILL', 'transaction_time': '2026-11-04T15:00:00Z', 'symbol': 'SPY', 'side': 'buy', 'order_id': 'order1', 'qty': '0.1', 'price': '123.45'}]
    sections['activity_linked_orders']['data'] = [{'id': 'order1', 'symbol': 'SPY', 'side': 'buy', 'filled_qty': '0.1'}]
    return {'schema_version': 1, 'environment': 'paper', 'read_only': True, 'requested_date': '2026-11-04', 'sections': sections}


class AuditTests(unittest.TestCase):
    def test_decimal_cash_flow_is_not_pnl(self):
        result = a.audit(fixture())
        self.assertTrue(result['evidence_checks_passed'])
        self.assertEqual(result['fill_summary']['SPY']['trade_cash_flow_ex_fees'], '-12.345')
        self.assertIsNone(result['realized_pnl'])
        self.assertFalse(result['reconciled'])
        self.assertFalse(result['execution_authorized'])

    def test_missing_sections(self):
        s = fixture(); del s['sections']['positions']
        self.assertFalse(a.audit(s)['evidence_checks_passed'])

    def test_page_cap(self):
        s = fixture(); s['sections']['open_orders']['api_page_exhausted'] = False
        self.assertIn('coverage_unproven:open_orders', a.audit(s)['issues'])

    def test_duplicate_fill_suppresses_summary(self):
        s = fixture(); s['sections']['activities']['data'] *= 2
        self.assertIsNone(a.audit(s)['fill_summary'])

    def test_wrong_day_timezone_and_naive_time(self):
        for time in ('2026-11-04T01:00:00Z', '2026-11-04T15:00:00'):
            s = fixture(); s['sections']['activities']['data'][0]['transaction_time'] = time
            self.assertIsNone(a.audit(s)['fill_summary'])

    def test_mismatched_side(self):
        s = fixture(); s['sections']['activity_linked_orders']['data'][0]['side'] = 'sell'
        self.assertIsNone(a.audit(s)['fill_summary'])

    def test_partial_fills_exceed_order_total(self):
        s = fixture(); fill = copy.deepcopy(s['sections']['activities']['data'][0]); fill['id'] = 'fill2'
        s['sections']['activities']['data'].append(fill)
        self.assertIsNone(a.audit(s)['fill_summary'])

    def test_non_fill_requires_review(self):
        s = fixture(); s['sections']['activities']['data'].append({'id': 'cash1', 'activity_type': 'CSD'})
        self.assertIn('non_fill_cash_movements_require_review', a.audit(s)['issues'])

    def test_equity_drift(self):
        s = fixture(); s['sections']['account_after']['data']['equity'] = '101'
        self.assertIn('account_equity_changed_during_collection', a.audit(s)['issues'])

    def test_invalid_numbers(self):
        for value in ('NaN', 'Infinity', True, '-1'):
            s = fixture(); s['sections']['activities']['data'][0]['qty'] = value
            self.assertIsNone(a.audit(s)['fill_summary'])

    def test_non_object(self):
        self.assertFalse(a.audit([])['evidence_checks_passed'])


if __name__ == '__main__':
    unittest.main()
