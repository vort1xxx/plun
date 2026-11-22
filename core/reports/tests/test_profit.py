import ast
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
ns = dict(date=date, datetime=datetime, time=time, timedelta=timedelta, timezone=timezone,
          REPORT_TZ=timezone(timedelta(hours=2)), TransactionOperations=NS(SELL=NS(name='SELL')))
tree = ast.parse((ROOT / 'profit/profit.py').read_text(encoding='utf-8'))
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]), 'profit.py', 'exec'), ns)


def transaction(value, operation='SELL', status='CONFIRMED', created='2026-10-09T12:00:00Z'):
    return NS(value=value, operation=NS(name=operation), status=NS(name=status), created_at=created,
              props=NS(deal_id=''), id='transaction')


class ProfitTests(unittest.TestCase):
    def report(self, transactions):
        account = NS(get_transactions=Mock(return_value=NS(transactions=transactions, page_info=NS(has_next_page=False))))
        return ns['fetch_profit_report'](account, date(2026, 10, 9), date(2026, 10, 9))

    def test_expenses_and_failed_sales_are_excluded(self):
        report = self.report([transaction(100), transaction(-20, 'ITEM_PREMIUM_PRIORITY'),
                              transaction(500, status='FAILED'), transaction(900, 'WITHDRAW')])
        self.assertEqual((report['sales_count'], report['sales_total']), (1, 100))

    def test_pending_sales_are_separate(self):
        report = self.report([transaction(100), transaction(50, status='PENDING'), transaction(20, status='PROCESSING')])
        self.assertEqual((report['sales_total'], report['pending_total'], report['pending_count']), (100, 70, 2))

    def test_kaliningrad_date_bounds(self):
        report = self.report([transaction(10, created='2026-10-08T22:00:00Z'),
                              transaction(20, created='2026-10-09T22:00:00Z'), transaction(90, created=None)])
        self.assertEqual(report['sales_total'], 10)


if __name__ == '__main__':
    unittest.main()
