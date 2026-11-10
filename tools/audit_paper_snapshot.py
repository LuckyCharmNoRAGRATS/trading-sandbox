"""Offline evidence audit. No network, broker client, or trading authorization."""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

REQUIRED = ('account_before', 'account_after', 'positions', 'open_orders',
            'submitted_orders', 'activities', 'activity_linked_orders', 'portfolio_history')


def number(value):
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite():
            raise ValueError('nonfinite number')
        return result
    except InvalidOperation:
        raise ValueError('invalid number') from None


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('invalid timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result


def validate_payload(name, value, day):
    if name in ('positions', 'open_orders', 'submitted_orders', 'activity_linked_orders'):
        if not isinstance(value, list):
            raise ValueError('expected records')
        seen = set()
        for row in value:
            if not isinstance(row, dict):
                raise ValueError('expected object')
            key = row.get('symbol' if name == 'positions' else 'id')
            if not isinstance(key, str) or not key or key in seen:
                raise ValueError('invalid identity')
            seen.add(key)
            if name == 'positions':
                number(row['qty'])
            else:
                if not isinstance(row.get('symbol'), str) or not row['symbol'] or row.get('side') not in ('buy', 'sell'):
                    raise ValueError('invalid order')
                if number(row['filled_qty']) < 0:
                    raise ValueError('negative filled quantity')
    if name == 'portfolio_history':
        if not isinstance(value, dict):
            raise ValueError('expected history')
        times, equity = value.get('timestamp'), value.get('equity')
        if not isinstance(times, list) or not times or not isinstance(equity, list) or len(times) != len(equity):
            raise ValueError('invalid history arrays')
        previous = None
        for ts, eq in zip(times, equity):
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                raise ValueError('invalid epoch')
            number(ts)
            instant = datetime.fromtimestamp(ts, timezone.utc)
            if previous is not None and ts <= previous:
                raise ValueError('history chronology')
            if instant.astimezone(ZoneInfo('America/New_York')).date() != day:
                raise ValueError('history outside requested date')
            previous = ts
            number(eq)


def audit(snapshot, *, now=None, max_age_seconds=900):
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
        raise ValueError('max age must be a positive integer')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('audit time must be timezone-aware')
    issues = []
    result = {'schema_version': 1, 'reconciled': False, 'execution_authorized': False,
              'realized_pnl': None, 'unrealized_pnl': None, 'issues': issues,
              'fill_summary': None,
              'limitations': ['Trade cash flow excludes fees and is not profit.',
                              'Opening inventory, cost basis and cash movements remain unreconciled.',
                              'Current account and positions are not historical closing balances.']}
    if not isinstance(snapshot, dict):
        issues.append('snapshot_not_object')
        result['evidence_checks_passed'] = False
        return result
    if snapshot.get('schema_version') != 1 or snapshot.get('environment') != 'paper' or snapshot.get('read_only') is not True:
        issues.append('unsupported_snapshot_contract')
    try:
        day = date.fromisoformat(snapshot['requested_date'])
    except (KeyError, TypeError, ValueError):
        day = None
        issues.append('invalid_requested_date')
    result['requested_date'] = str(day) if day else None
    began = ended = None
    try:
        began = timestamp(snapshot['collection_started_at'])
        ended = timestamp(snapshot['collection_finished_at'])
        if began > ended or ended > now:
            raise ValueError('invalid collection chronology')
        result['snapshot_age_seconds'] = (now - ended).total_seconds()
        result['collection_duration_seconds'] = (ended - began).total_seconds()
        if (now - began).total_seconds() > max_age_seconds:
            issues.append('stale_snapshot')
    except (KeyError, TypeError, ValueError):
        issues.append('invalid_collection_timestamps')
    result['max_age_seconds'] = max_age_seconds
    sections = snapshot.get('sections')
    if not isinstance(sections, dict):
        sections = {}
    data = {}
    for name in REQUIRED:
        section = sections.get(name)
        if not isinstance(section, dict) or section.get('status') != 'ok' or 'data' not in section:
            issues.append('missing_or_failed_section:' + name)
            continue
        try:
            section_start = timestamp(section['started_at'])
            section_end = timestamp(section['finished_at'])
            if began is None or ended is None or not began <= section_start <= section_end <= ended:
                raise ValueError('section outside collection')
        except (KeyError, TypeError, ValueError):
            issues.append('invalid_section_timestamps:' + name)
        try:
            validate_payload(name, section['data'], day)
        except (ValueError, KeyError, TypeError, OverflowError, OSError):
            issues.append('invalid_section_payload:' + name)
            continue
        data[name] = section['data']
        if name in ('open_orders', 'submitted_orders', 'activities') and section.get('api_page_exhausted') is not True:
            issues.append('coverage_unproven:' + name)
    before, after = data.get('account_before'), data.get('account_after')
    if isinstance(before, dict) and isinstance(after, dict):
        if not before.get('id') or before.get('id') != after.get('id'):
            issues.append('account_identity_mismatch')
        try:
            if number(before['equity']) != number(after['equity']):
                issues.append('account_equity_changed_during_collection')
        except (ValueError, KeyError, TypeError):
            issues.append('invalid_account_equity')
    else:
        issues.append('invalid_account_records')

    linked = data.get('activity_linked_orders')
    orders = {}
    if not isinstance(linked, list):
        issues.append('invalid_linked_orders')
    else:
        for order in linked:
            if not isinstance(order, dict) or not isinstance(order.get('id'), str) or not order['id'] or order['id'] in orders:
                issues.append('invalid_or_duplicate_linked_order')
            else:
                orders[order['id']] = order
    activities = data.get('activities')
    if not isinstance(activities, list):
        issues.append('invalid_activities')
    else:
        seen, totals, by_order = set(), {}, {}
        fill_errors = False
        non_fill_types = set()
        for record in activities:
            try:
                if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id'] or record['id'] in seen:
                    raise ValueError('invalid_activity_identity')
                seen.add(record['id'])
                kind = record.get('activity_type')
                if not isinstance(kind, str) or not kind:
                    raise ValueError('missing_activity_type')
                if kind != 'FILL':
                    non_fill_types.add(kind)
                    continue
                fill_time = timestamp(record['transaction_time'])
                if fill_time.tzinfo is None or fill_time.astimezone(ZoneInfo('America/New_York')).date() != day:
                    raise ValueError('fill_outside_requested_day')
                symbol, side, oid = record['symbol'], record['side'], record['order_id']
                if not isinstance(symbol, str) or not symbol or side not in ('buy', 'sell') or not isinstance(oid, str):
                    raise ValueError('invalid_fill_identity')
                qty, price = number(record['qty']), number(record['price'])
                if qty <= 0 or price <= 0:
                    raise ValueError('invalid_fill_numbers')
                order = orders.get(oid)
                if not order or order.get('symbol') != symbol or order.get('side') != side:
                    raise ValueError('fill_order_identity_mismatch')
                by_order[oid] = by_order.get(oid, Decimal(0)) + qty
                item = totals.setdefault(symbol, {'buy_qty': Decimal(0), 'sell_qty': Decimal(0), 'trade_cash_flow_ex_fees': Decimal(0), 'fill_count': 0})
                item[side + '_qty'] += qty
                item['trade_cash_flow_ex_fees'] += qty * price * (-1 if side == 'buy' else 1)
                item['fill_count'] += 1
            except (ValueError, KeyError, TypeError, AttributeError):
                issues.append('invalid_or_unmatched_activity')
                fill_errors = True
        for oid, qty in by_order.items():
            try:
                # A day's fills can be less than lifetime filled_qty, never greater.
                if qty > number(orders[oid]['filled_qty']):
                    raise ValueError('fill quantity exceeds order')
            except (ValueError, KeyError, TypeError):
                issues.append('fill_quantity_exceeds_or_missing_order_total')
                fill_errors = True
        result['non_fill_activity_types'] = sorted(non_fill_types)
        if non_fill_types:
            issues.append('non_fill_cash_movements_require_review')
        if not fill_errors and sections['activities'].get('api_page_exhausted') is True:
            result['fill_summary'] = {symbol: {k: str(v) if isinstance(v, Decimal) else v for k, v in row.items()} for symbol, row in sorted(totals.items())}
    result['evidence_checks_passed'] = not issues
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--max-age-seconds', type=int, default=900)
    args = parser.parse_args()
    try:
        raw = args.snapshot.read_bytes()
        result = audit(json.loads(raw), max_age_seconds=args.max_age_seconds)
        result['source_sha256'] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        parser.exit(2, 'Cannot read valid snapshot JSON.\n')
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['evidence_checks_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
