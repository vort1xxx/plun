import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

DEFAULT_CONFIG = {"enabled": False, "groups": []}
LOCAL_TZ = timezone(timedelta(hours=2))


def clock_minutes(value, allow_midnight=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("Время укажите в формате ЧЧ:ММ.")
    hour, minute = map(int, value.split(":"))
    if allow_midnight and hour == 24 and minute == 0:
        return 1440
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Некорректное время.")
    return hour * 60 + minute


def parse_window(text):
    parts = re.split(r"\s*[-–—]\s*", text.strip())
    if len(parts) != 2:
        raise ValueError("Введите окно проверки: 18:00 - 20:00 (Калининград).")
    start, end = parts
    if clock_minutes(start) == clock_minutes(end, True):
        raise ValueError("Начало и конец должны отличаться. Весь день: 00:00 - 24:00.")
    return start, end


def active_window(timestamp, group):
    local = datetime.fromtimestamp(timestamp, LOCAL_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_minutes = clock_minutes(group["window_start"])
    end_minutes = clock_minutes(group["window_end"], True)
    start = midnight + timedelta(minutes=start_minutes)
    end = midnight + timedelta(minutes=end_minutes)
    if end_minutes < start_minutes:
        if local < end:
            start -= timedelta(days=1)
        else:
            end += timedelta(days=1)
    return (start.timestamp(), end.timestamp()) if start <= local < end else None


def next_check(timestamp, group):
    if active_window(timestamp, group):
        return timestamp
    local = datetime.fromtimestamp(timestamp, LOCAL_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=clock_minutes(group["window_start"]))
    if start <= local:
        start += timedelta(days=1)
    return start.timestamp()


def parse_period(text):
    match = re.fullmatch(r"\s*(\d+)\s*(с|сек|секунд|s|м|мин|минут|m|h|ч|час|часов|д|дней|d)?\s*", text.casefold())
    if not match:
        raise ValueError("Введите период: 30 мин, 2 ч, 1 д. Число без единицы означает минуты.")
    amount, unit = int(match[1]), match[2] or "мин"
    seconds = amount * (1 if unit in ("с", "сек", "секунд", "s") else 3600 if unit in ("h", "ч", "час", "часов") else 86400 if unit in ("d", "д", "дней") else 60)
    if not 1 <= seconds <= 30 * 86400:
        raise ValueError("Период должен быть от 1 секунды до 30 дней.")
    return seconds


def period_label(seconds):
    if seconds % 60:
        return f"{seconds} сек."
    if seconds % 86400 == 0:
        return f"{seconds // 86400} д."
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    return f"{seconds // 60} мин."


def money(value):
    try:
        result = Decimal(str(value).replace(",", "."))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError("API вернул некорректную цену поднятия.") from None


def validate_config(raw):
    if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool or not isinstance(raw.get("groups", []), list):
        raise ValueError("Некорректная конфигурация групп.")
    groups, group_ids, item_ids = [], set(), set()
    if len(raw.get("groups", [])) > 100:
        raise ValueError("Не более 100 групп.")
    for row in raw.get("groups", []):
        if not isinstance(row, dict):
            raise ValueError("Группа должна быть объектом.")
        gid, name = row.get("id"), row.get("name")
        if not isinstance(gid, str) or not re.fullmatch(r"[a-f0-9]{12}", gid) or gid in group_ids:
            raise ValueError("Некорректный или повторяющийся ID группы.")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Название группы: 1–80 символов.")
        group_ids.add(gid)
        interval = row.get("check_interval_seconds", row.get("interval_seconds", 30))
        position = row.get("position", 5)
        if position == 0 and "check_interval_seconds" not in row:
            position = 1
        if type(interval) is not int or not 1 <= interval <= 2592000:
            raise ValueError("Период группы: 1 секунда–30 дней.")
        if type(position) is not int or not 1 <= position <= 100000:
            raise ValueError("Порог позиции: 1–100000.")
        start, end = row.get("window_start", "18:00"), row.get("window_end", "20:00")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("Время должно быть строкой ЧЧ:ММ.")
        start, end = parse_window(f"{start} - {end}")
        if type(row.get("enabled", False)) is not bool or not isinstance(row.get("items", []), list):
            raise ValueError("Некорректные настройки группы.")
        items = []
        if len(row.get("items", [])) > 500:
            raise ValueError("Не более 500 товаров в группе.")
        for item in row.get("items", []):
            if not isinstance(item, dict):
                raise ValueError("Товар должен быть объектом.")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", item_id) or item_id in item_ids:
                raise ValueError("Некорректный ID товара или товар уже находится в другой группе.")
            item_ids.add(item_id)
            title = item.get("name", item_id)
            if not isinstance(title, str) or len(title) > 500:
                raise ValueError("Некорректное название товара.")
            items.append({"id": item_id, "name": title})
        groups.append(dict(id=gid, name=name.strip(), enabled=row.get("enabled", False) and row.get("mode", "links") == "links",
                           check_interval_seconds=interval, window_start=start, window_end=end,
                           position=position, items=items))
    return {"enabled": raw.get("enabled", False), "groups": groups}


def position_requires_bump(position, threshold):
    if type(position) is not int or position < 1:
        return None
    return position > threshold


def item_position(item):
    # sequence is the item's actual position in the seller's item listing.
    # priorityPosition describes its separate priority placement and can stay
    # at 10 even when the item itself is much further down the listing.
    for field in ("sequence", "priority_position"):
        position = getattr(item, field, None)
        if type(position) is int and position > 0:
            return position, field
    return None, ""


