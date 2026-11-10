"""Standalone GET-only evidence collector. Never imports or starts the trader."""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = 'https://paper-api.alpaca.markets'
ALLOWED = {'/v2/account', '/v2/positions', '/v2/orders',
           '/v2/account/activities', '/v2/account/portfolio/history'}


class CollectionError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError('Redirect refused')


class PaperReader:
    def __init__(self, key, secret):
        if not key or not secret:
            raise CollectionError('Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment')
        self.headers = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path, params=None):
        if path not in ALLOWED and not re.fullmatch(r'/v2/orders/[0-9a-fA-F-]{36}', path):
            raise CollectionError('Endpoint refused')
        url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
        request = urllib.request.Request(url, headers=self.headers, method='GET')
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise CollectionError(f'Broker HTTP {exc.code}; response body withheld') from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise CollectionError('Broker transport or JSON error; details withheld') from None


def finite_number(value):
    try:
        if isinstance(value, bool) or not Decimal(str(value)).is_finite():
            raise CollectionError('Invalid numeric evidence')
    except (InvalidOperation, ValueError):
        raise CollectionError('Invalid numeric evidence') from None


def now():
    return datetime.now(timezone.utc).isoformat()


def rows(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise CollectionError('Expected a list of records')
    ids = [x.get('id') for x in value]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise CollectionError('Missing or duplicate record IDs')
    return value


def activities(reader, day, max_pages=1000):
    result, seen, token = [], set(), None
    for _ in range(max_pages):
        query = {'date': day, 'direction': 'asc', 'page_size': 100}
        if token:
            query['page_token'] = token
        page = rows(reader.get('/v2/account/activities', query))
        if any(x['id'] in seen for x in page):
            raise CollectionError('Repeated activity page or record')
        result.extend(page)
        seen.update(x['id'] for x in page)
        if len(page) < 100:
            return result
        token = page[-1]['id']
    raise CollectionError('Activity page budget exhausted; coverage unknown')


def collect(reader, day):
    target = date.fromisoformat(day)
    start = datetime.combine(target, datetime.min.time(), ZoneInfo('America/New_York'))
    end = start + timedelta(days=1)
    report = {'schema_version': 1, 'environment': 'paper', 'read_only': True,
              'requested_date': day, 'report_timezone': 'America/New_York',
              'collection_started_at': now(), 'sections': {},
              'reconciled': False, 'execution_authorized': False,
              'limitations': ['Sequential reads are not an atomic snapshot.',
                  'Account and positions describe collection time, not historical day-end.',
                  'Historical positions and realized P&L require opening inventory and cost basis.',
                  'Order query covers submission times; activity-linked orders are fetched separately.',
                  'API coverage flags do not establish accounting completeness or trading readiness.']}

    def capture(name, function):
        began = now()
        try:
            data = function()
            report['sections'][name] = {'status': 'ok', 'started_at': began,
                                        'finished_at': now(), 'data': data}
            return data
        except Exception as exc:
            report['sections'][name] = {'status': 'error', 'started_at': began,
                                        'finished_at': now(), 'error_type': type(exc).__name__}
            return None

    def account():
        value = reader.get('/v2/account')
        if not isinstance(value, dict) or not value.get('id') or 'equity' not in value:
            raise CollectionError('Invalid account')
        finite_number(value['equity'])
        return value

    def positions():
        value = reader.get('/v2/positions')
        if not isinstance(value, list) or any(not isinstance(x, dict) or not x.get('symbol') or 'qty' not in x for x in value):
            raise CollectionError('Invalid positions')
        symbols = [x['symbol'] for x in value]
        if len(set(symbols)) != len(symbols):
            raise CollectionError('Duplicate position symbols')
        for position in value:
            finite_number(position['qty'])
        return value

    capture('account_before', account)
    capture('positions', positions)
    for name, query in [('open_orders', {'status': 'open'}),
                         ('submitted_orders', {'status': 'all',
                          'after': (start - timedelta(seconds=1)).isoformat(), 'until': end.isoformat()})]:
        data = capture(name, lambda q=query: rows(reader.get('/v2/orders',
                       dict(q, limit=500, direction='asc', nested='false'))))
        report['sections'][name]['api_page_exhausted'] = data is not None and len(data) < 500
        if data is not None and len(data) >= 500:
            report['sections'][name]['status'] = 'incomplete'
    activity = capture('activities', lambda: activities(reader, day))
    if activity is not None:
        report['sections']['activities']['api_page_exhausted'] = True
        def linked():
            raw_ids = [x['order_id'] for x in activity if x.get('order_id')]
            if any(not isinstance(oid, str) for oid in raw_ids):
                raise CollectionError('Invalid linked order ID')
            order_ids = sorted(set(raw_ids))
            result = []
            for oid in order_ids:
                if not isinstance(oid, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', oid):
                    raise CollectionError('Invalid linked order ID')
                order = reader.get('/v2/orders/' + oid)
                if not isinstance(order, dict) or order.get('id') != oid:
                    raise CollectionError('Linked order identity mismatch')
                result.append(order)
            return result
        capture('activity_linked_orders', linked)
    def history():
        value = reader.get('/v2/account/portfolio/history',
                           {'date_start': day, 'date_end': day, 'timeframe': '1D'})
        if not isinstance(value, dict) or not value.get('timestamp') or not isinstance(value.get('equity'), list) or len(value['timestamp']) != len(value['equity']):
            raise CollectionError('Empty or malformed portfolio history')
        for equity in value['equity']:
            finite_number(equity)
        return value
    capture('portfolio_history', history)
    capture('account_after', account)
    report['collection_finished_at'] = now()
    report['collection_ok'] = all(x['status'] == 'ok' for x in report['sections'].values())
    return report


def save(report, destination):
    # Exclusive creation: never overwrite earlier evidence; private permissions.
    payload = json.dumps(report, indent=2, allow_nan=False) + '\n'
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='Broker activity date YYYY-MM-DD (New York)')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        date.fromisoformat(args.date)
        reader = PaperReader(os.getenv('APCA_API_KEY_ID'), os.getenv('APCA_API_SECRET_KEY'))
        report = collect(reader, args.date)
        save(report, args.output)
    except (ValueError, OSError, CollectionError) as exc:
        parser.exit(2, f'Collection failed ({type(exc).__name__}); check date, credentials and output path.\n')
    print('Snapshot saved. Accounting remains unreconciled.')
    return 0 if report['collection_ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
