import ast
from concurrent.futures import Future
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def extract(path, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


ns = dict(date=date, datetime=datetime, time=time, timedelta=timedelta, timezone=timezone,
          Decimal=Decimal, InvalidOperation=InvalidOperation, REPORT_TZ=timezone(timedelta(hours=2)),
          MAX_PAGES=1000, clock=SimpleNamespace(sleep=Mock()), ItemDealDirections=SimpleNamespace(OUT="OUT"))
extract("daily_report.py", ns)


def deal(did, created, completed=None, status="PAID", price=100):
    return SimpleNamespace(id=did, created_at=created, completed_at=completed, status=status,
                           item=SimpleNamespace(price=price))


class DailyTests(unittest.TestCase):
    def test_requested_daily_report_format(self):
        report = dict(day="2026-11-17", count=4, sales="324", expenses="114", warnings=[])
        self.assertEqual(ns["format_report"](report), (
            "📊 Ежедневный отчёт Playerok за 17.11.2026\n"
            "🕒 Калининград\n\n"
            "1. Кол-во совершённых сделок: 4\n"
            "2. Сумма совершённых сделок: 324,00 ₽\n"
            "3. Расходы на платное продвижение: 114,00 ₽"
        ))

    def test_six_metrics_and_confirmation_day(self):
        sales = [deal("a", "2026-10-09T10:00:00Z", "2026-10-09T12:00:00Z", "CONFIRMED", 100),
                 deal("b", "2026-10-09T11:00:00Z", price=200),
                 deal("old", "2026-10-08T10:00:00Z", "2026-10-09T12:00:00Z", "CONFIRMED", 50),
                 deal("refund", "2026-10-09T10:00:00Z", status="ROLLED_BACK", price=999),
                 deal("no-time", None, price=999)]
        transactions = [SimpleNamespace(created_at="2026-10-09T10:00:00Z", operation="ITEM_PREMIUM_PRIORITY", status="CONFIRMED", value=-20),
                        SimpleNamespace(created_at="2026-10-09T10:00:00Z", operation="WITHDRAW", status="CONFIRMED", value=1000),
                        SimpleNamespace(created_at="2026-10-09T10:00:00Z", operation="ITEM_PREMIUM_PRIORITY", status="FAILED", value=999)]
        report = ns["build_report"](date(2026, 10, 9), sales, transactions, SimpleNamespace(frozen=77, pending_income=88))
        self.assertEqual((report["count"], report["sales"], report["confirmed"], report["expenses"], report["profit"], report["frozen"], report["pending"]), (2, "300", "150", "20", "280", "77", "88"))

    def test_local_midnight_boundaries(self):
        self.assertEqual(ns["local_day"]("2026-10-08T22:00:00Z"), date(2026, 10, 9))
        self.assertEqual(ns["local_day"]("2026-10-09T21:59:59Z"), date(2026, 10, 9))
        self.assertEqual(ns["local_day"]("2026-10-09T22:00:00Z"), date(2026, 10, 10))
        self.assertIsNone(ns["local_day"]("2026-10-09"))

    def test_decimal_arithmetic(self):
        report = ns["build_report"](date(2026, 10, 9), [deal("a", "2026-10-09T10:00:00Z", price="0.10"), deal("b", "2026-10-09T10:00:00Z", price="0.20")], [], SimpleNamespace(frozen=0, pending_income=0))
        self.assertEqual(report["sales"], "0.30")

    def test_promotion_fee_is_included(self):
        tx = SimpleNamespace(created_at="2026-10-09T10:00:00Z", operation="ITEM_PREMIUM_PRIORITY", status="CONFIRMED", value=20, fee=2)
        report = ns["build_report"](date(2026, 10, 9), [], [tx], SimpleNamespace(frozen=0, pending_income=0))
        self.assertEqual(report["expenses"], "22")

    def test_missing_amounts_are_warned(self):
        report = ns["build_report"](date(2026, 10, 9), [deal("a", "2026-10-09T10:00:00Z", price=None)], [], SimpleNamespace(frozen=0, pending_income=0))
        self.assertTrue(report["warnings"])
        with self.assertRaises(ValueError):
            ns["build_report"](date(2026, 10, 9), [], [], SimpleNamespace(frozen=None, pending_income=0))

    def test_pagination_deduplicates_and_rejects_incomplete(self):
        fetch = Mock(side_effect=[SimpleNamespace(deals=[SimpleNamespace(id="a")], page_info=SimpleNamespace(has_next_page=True, end_cursor="c")), SimpleNamespace(deals=[SimpleNamespace(id="a"), SimpleNamespace(id="b")], page_info=SimpleNamespace(has_next_page=False))])
        self.assertEqual(len(ns["all_pages"](fetch, "deals")), 2)
        fetch = Mock(return_value=SimpleNamespace(deals=[SimpleNamespace(id="a")], page_info=SimpleNamespace(has_next_page=True, end_cursor="c")))
        with self.assertRaises(ValueError):
            ns["all_pages"](fetch, "deals")

    def test_schedule_is_20_kaliningrad(self):
        scheduler = dict(REPORT_TZ=ns["REPORT_TZ"], timedelta=timedelta, datetime=datetime)
        extract("daily_scheduler.py", scheduler)
        self.assertIsNone(scheduler["due_day"](datetime(2026, 10, 10, 17, 59, tzinfo=timezone.utc)))
        self.assertEqual(scheduler["due_day"](datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)), date(2026, 10, 9))

    def test_delivery_receipts_and_pending_send(self):
        state = {"reports": {}, "sent": {}}
        def save(name, value):
            state.clear()
            state.update(deepcopy(value))
        future = Future()
        future.set_result(None)
        active = Event()
        active.set()
        thread_send = Mock(return_value=future)
        scheduler = dict(REPORT_TZ=ns["REPORT_TZ"], timedelta=timedelta, datetime=datetime, ACTIVE=active,
            _report_lock=Lock(), _inflight={}, logger=logging.getLogger("test"),
            MainSettings=SimpleNamespace(get=lambda _: {"telegram": {"bot": {"signed_users": [1]}}}),
            Data=SimpleNamespace(get=lambda _: deepcopy(state), set=save),
            get_playerok_bot=lambda: SimpleNamespace(account="account"),
            get_telegram_bot=lambda: SimpleNamespace(bot=SimpleNamespace(send_message=Mock(return_value="send"))),
            get_telegram_bot_loop=lambda: SimpleNamespace(is_running=lambda: True),
            asyncio=SimpleNamespace(run_coroutine_threadsafe=thread_send),
            fetch_report=Mock(return_value={"day": "2026-10-09"}), format_report=lambda _: "report")
        extract("daily_scheduler.py", scheduler)
        now = datetime(2026, 10, 10, 20, tzinfo=ns["REPORT_TZ"])
        scheduler["deliver_daily"](now)
        scheduler["deliver_daily"](now)
        thread_send.assert_called_once()
        self.assertEqual(state["sent"]["2026-10-09"], ["1"])
        self.assertIn("2026-10-09", state["reports"])
        state["sent"]["2026-10-09"] = []
        pending = Future()
        scheduler["_inflight"][("2026-10-09", "1")] = pending
        scheduler["deliver_daily"](now)
        thread_send.assert_called_once()
        pending.set_result(None)
        scheduler["deliver_daily"](now)
        self.assertEqual(state["sent"]["2026-10-09"], ["1"])


if __name__ == "__main__":
    unittest.main()
