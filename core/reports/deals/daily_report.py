from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
import time as clock

from playerokapi.enums import ItemDealDirections

logger = logging.getLogger("dealsreport.daily")
REPORT_TZ = timezone(timedelta(hours=2), "Kaliningrad")
MAX_PAGES = 1000


def as_datetime(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and ("T" in value or " " in value):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(REPORT_TZ)


def local_day(value):
    parsed = as_datetime(value)
    return parsed.date() if parsed else None


def amount(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Сумма отсутствует")
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("Некорректная сумма")
        return result
    except InvalidOperation:
        raise ValueError("Некорректная сумма") from None


def enum_name(value):
    return getattr(value, "name", str(value or ""))


def all_pages(fetch, field, **kwargs):
    records, seen_ids, seen_cursors = [], set(), set()
    cursor = None
    for _ in range(MAX_PAGES):
        page = fetch(count=24, after_cursor=cursor, **kwargs)
        rows = getattr(page, field, None)
        if not isinstance(rows, list):
            raise ValueError("API не вернул список записей")
        for row in rows:
            rid = str(getattr(row, "id", "") or "")
            if not rid:
                raise ValueError("API не вернул ID записи")
            if rid not in seen_ids:
                records.append(row)
                seen_ids.add(rid)
        info = getattr(page, "page_info", None)
        if info is None:
            if rows:
                raise ValueError("API не вернул информацию о страницах")
            return records
        if not info.has_next_page:
            return records
        cursor = info.end_cursor
        if not cursor or cursor in seen_cursors:
            raise ValueError("API не вернул новый курсор; история неполная")
        seen_cursors.add(cursor)
        clock.sleep(0.2)
    raise ValueError("Превышен лимит страниц; история неполная")


def build_report(day, deals, transactions, balance, captured_at=None):
    count = 0
    sales = confirmed = expenses = Decimal("0")
    missing_sales = missing_expenses = 0
    for deal in deals:
        status = enum_name(getattr(deal, "status", None))
        if status == "ROLLED_BACK":
            continue
        created = local_day(getattr(deal, "created_at", None))
        completed = local_day(getattr(deal, "completed_at", None))
        is_confirmed = status in {"CONFIRMED", "CONFIRMED_AUTOMATICALLY"}
        try:
            price = amount(getattr(getattr(deal, "item", None), "price", None))
        except ValueError:
            price = None
        if created == day:
            count += 1
            if price is None:
                missing_sales += 1
            else:
                sales += price
        if is_confirmed and completed == day and price is not None:
            confirmed += price
    for transaction in transactions:
        if local_day(getattr(transaction, "created_at", None)) != day:
            continue
        if enum_name(getattr(transaction, "operation", None)) not in {"ITEM_PREMIUM_PRIORITY", "ITEM_DEFAULT_PRIORITY"}:
            continue
        if enum_name(getattr(transaction, "status", None)) != "CONFIRMED":
            continue
        try:
            cost = abs(amount(getattr(transaction, "value", None)))
            fee = amount(getattr(transaction, "fee", 0) or 0)
            expenses += cost + abs(fee)
        except ValueError:
            missing_expenses += 1
    frozen = amount(getattr(balance, "frozen", None))
    pending = amount(getattr(balance, "pending_income", None))
    warnings = []
    if missing_sales or missing_expenses:
        warnings.append(f"Не переданы суммы: сделок — {missing_sales}, продвижений — {missing_expenses}. Суммы неполные.")
    return dict(day=day.isoformat(), count=count, sales=str(sales), confirmed=str(confirmed),
                frozen=str(frozen), pending=str(pending), expenses=str(expenses), profit=str(sales - expenses),
                captured_at=(captured_at or datetime.now(REPORT_TZ)).isoformat(), warnings=warnings)


def fetch_report(account, day):
    deals = all_pages(account.get_deals, "deals", direction=ItemDealDirections.OUT)
    start = datetime.combine(day, time.min, REPORT_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(day + timedelta(days=1), time.min, REPORT_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    transactions = all_pages(account.get_transactions, "transactions", from_date=start, to_date=end)
    account.get()
    balance = getattr(getattr(account, "profile", None), "balance", None)
    if balance is None:
        raise ValueError("API не передал баланс профиля")
    return build_report(day, deals, transactions, balance)


def rub(value):
    return f"{Decimal(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def format_report(report):
    day = date.fromisoformat(report["day"]).strftime("%d.%m.%Y")
    text = (f"📊 Ежедневный отчёт Playerok за {day}\n🕒 Калининград\n\n"
            f"1. Кол-во совершённых сделок: {report['count']}\n"
            f"2. Сумма совершённых сделок: {rub(report['sales'])}\n"
            f"3. Расходы на платное продвижение: {rub(report['expenses'])}")
    if report["warnings"]:
        text += "\n\n⚠️ " + "\n".join(report["warnings"])
    return text
