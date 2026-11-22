from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from playerokapi.enums import TransactionOperations


REPORT_TZ = ZoneInfo("Europe/Kaliningrad")


def _api_utc(local_dt: datetime) -> datetime:
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def _local_date(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(REPORT_TZ).date()
    except (TypeError, ValueError):
        return None


def fetch_profit_report(account, start: date, end: date) -> dict:
    """Собирает отчёт только по продажам Playerok."""
    local_start = datetime.combine(start, time.min, tzinfo=REPORT_TZ)
    local_end_exclusive = datetime.combine(end + timedelta(days=1), time.min, tzinfo=REPORT_TZ)
    cursor = None
    transactions = []
    pages = 0
    truncated = False
    while pages < 100:
        page = account.get_transactions(
            from_date=_api_utc(local_start),
            to_date=_api_utc(local_end_exclusive),
            count=24,
            after_cursor=cursor,
        )
        transactions.extend(getattr(page, "transactions", []) or [])
        info = getattr(page, "page_info", None)
        if not info or not getattr(info, "has_next_page", False):
            break
        cursor = getattr(info, "end_cursor", None)
        if not cursor:
            truncated = True
            break
        pages += 1
    else:
        truncated = True

    pending_items = []
    pending_total = 0.0
    sales_count = 0
    sales_total = 0.0
    invalid_amounts = 0
    for transaction in transactions:
        created = _local_date(getattr(transaction, "created_at", None))
        if created is None or not start <= created <= end:
            continue
        operation = getattr(getattr(transaction, "operation", None), "name", "")
        if operation != TransactionOperations.SELL.name:
            continue
        status = getattr(getattr(transaction, "status", None), "name", "")
        if status in ("PENDING", "PROCESSING"):
            amount = None
            try:
                amount = float(getattr(transaction, "value", 0) or 0)
                pending_total += amount
            except (TypeError, ValueError):
                invalid_amounts += 1
            props = getattr(transaction, "props", None)
            deal_id = str(getattr(props, "deal_id", "") or "")
            product_name = ""
            if deal_id:
                try:
                    deal = account.get_deal(deal_id)
                    item = getattr(deal, "item", None)
                    product_name = str(
                        getattr(item, "name", None)
                        or getattr(item, "title", None)
                        or ""
                    )
                except Exception:
                    product_name = ""
            if not product_name:
                product_name = f"Товар по сделке {deal_id}" if deal_id else f"Операция Playerok ({operation})"
            pending_items.append({
                "name": product_name,
                "deal_id": deal_id,
                "transaction_id": str(getattr(transaction, "id", "") or ""),
                "status": status,
                "amount": amount,
            })
            continue
        if status != "CONFIRMED":
            continue
        try:
            sales_total += float(getattr(transaction, "value", 0) or 0)
            sales_count += 1
        except (TypeError, ValueError):
            invalid_amounts += 1

    return {
        "start": start,
        "end": end,
        "sales_count": sales_count,
        "sales_total": sales_total,
        "pending_count": len(pending_items),
        "pending_total": pending_total,
        "pending_items": pending_items,
        "invalid_amounts": invalid_amounts,
        "truncated": truncated,
    }


def format_rub(value: float) -> str:
    if abs(value - round(value)) < 0.005:
        return f"{round(value):,}".replace(",", " ") + " ₽"
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_profit_report(report: dict) -> str:
    start = report["start"].strftime("%d.%m.%Y")
    end = report["end"].strftime("%d.%m.%Y")
    text = (
        f"📊 Отчёт Playerok за {start} — {end}\n\n"
        f"✅ Подтверждённых продаж: {report['sales_count']}\n"
        f"💳 Зачислено по продажам: {format_rub(report['sales_total'])}\n"
        f"⏳ Продаж в процессе: {report['pending_count']} · {format_rub(report['pending_total'])}"
    )
    pending_items = report.get("pending_items", [])
    text += "\n\n📦 Товары по продажам в процессе:"
    if pending_items:
        status_labels = {"PENDING": "ожидание", "PROCESSING": "обработка"}
        for index, item in enumerate(pending_items, 1):
            amount = "—" if item.get("amount") is None else format_rub(item["amount"])
            label = f"{index}. {item['name']} — {amount} ({status_labels.get(item['status'], item['status'].lower())})"
            if item.get("deal_id"):
                label += f"\n   Сделка: https://playerok.com/deal/{item['deal_id']}"
            text += "\n" + label
    else:
        text += "\nНет продаж в процессе за этот период."
    if report["invalid_amounts"]:
        text += f"\nℹ️ Не удалось прочитать сумму у операций: {report['invalid_amounts']}"
    if report["truncated"]:
        text += "\n⚠️ Получена только часть транзакций: достигнут лимит страниц API."
    return text
