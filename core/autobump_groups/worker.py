import logging
import time
from threading import Event, Lock, Thread

from playerokapi.enums import ItemStatuses, PriorityTypes
from settings import Settings as MainSettings

from .logic import active_window, next_check, money, position_requires_bump
from .meta import NAME
from .storage import LOCK, get_config, get_state, save_state

logger = logging.getLogger(NAME)
ACTIVE = Event()
_thread = None
_thread_lock = Lock()
_tick_lock = Lock()
_POSITION_CACHE_TTL = 120
_position_cache_key = None
_position_cache_until = 0.0
_position_cache = {}


def seller_listing_positions(account):
    """Return actual 1-based positions from the account's paginated item list."""
    global _position_cache_key, _position_cache_until, _position_cache
    item_ids = frozenset(
        str(row["id"])
        for group in get_config()["groups"]
        for row in group["items"]
    )
    cache_key = (str(getattr(account, "id", "")), tuple(sorted(item_ids)))
    now = time.monotonic()
    if cache_key == _position_cache_key and now < _position_cache_until:
        return _position_cache

    positions = {}
    after_cursor = None
    seen_cursors = set()
    offset = 0
    while item_ids - positions.keys():
        page = account.get_my_items(count=24, after_cursor=after_cursor)
        rows = getattr(page, "items", None) or []
        for listed_item in rows:
            offset += 1
            listed_id = str(getattr(listed_item, "id", ""))
            if listed_id in item_ids:
                positions[listed_id] = offset

        page_info = getattr(page, "page_info", None)
        next_cursor = getattr(page_info, "end_cursor", None)
        if (not page_info or not getattr(page_info, "has_next_page", False)
                or not next_cursor or next_cursor == after_cursor or next_cursor in seen_cursors):
            break
        seen_cursors.add(next_cursor)
        after_cursor = next_cursor

    _position_cache_key = cache_key
    _position_cache_until = now + _POSITION_CACHE_TTL
    _position_cache = positions
    return positions


def current_item_position(account, item_id, item):
    positions = seller_listing_positions(account)
    position = positions.get(str(item_id))
    if type(position) is int and position > 0:
        return position, "seller_listing"
    # The detailed item response may include sequence even if it is absent from
    # the paginated list. priority_position is deliberately excluded: it is a
    # separate paid-priority rank and can remain 10 while listing rank is 145.
    position = getattr(item, "sequence", None)
    if type(position) is int and position > 0:
        return position, "sequence"
    return None, None


def builtin_enabled():
    return (MainSettings.get("config") or {}).get("playerok", {}).get("auto_bump_items", {}).get("enabled", False)


def runtime_status(plbot, now=None):
    config = get_config()
    if not config['enabled']:
        return "Поднятие по группам выключено в меню /bumpgroups."
    if not ACTIVE.is_set():
        return "Ожидание запуска планировщика групп."
    if builtin_enabled():
        return "Работа приостановлена: включено обычное автоподнятие."
    if plbot is None or getattr(plbot, 'account', None) is None:
        return "Ожидание запуска аккаунта Playerok."
    if not _thread or not _thread.is_alive():
        return "Планировщик не запущен. Перезапустите бот."
    if not config['groups']:
        return "Групп нет. Создайте группу и привяжите товары ссылками."
    groups = [g for g in config['groups'] if g['enabled'] and g['items']]
    if not groups:
        return "Нет включённых групп с привязанными товарами."
    timestamp = time.time() if now is None else now
    if not any(active_window(timestamp, g) for g in groups):
        return "Ожидание заданного времени проверки по Калининграду."
    return "Планировщик работает. Результаты проверок — в разделе «Товары и статус»."


def group_current(gid, item_id, manual=False):
    config = get_config()
    if not ACTIVE.is_set() or (not manual and not config["enabled"]) or builtin_enabled():
        return None
    return next((g for g in config["groups"] if g["id"] == gid and (manual or g["enabled"]) and any(i["id"] == item_id for i in g["items"])), None)


def record(item_id, **values):
    with LOCK:
        state = get_state()
        state["items"].setdefault(item_id, {}).update(values)
        save_state(state)


def process_item(account, group, row, now=None, manual=False):
    clock = time.time if now is None else lambda: now
    timestamp = clock()
    window = active_window(timestamp, group)
    if window is None and not manual:
        return
    item_id = row["id"]
    signature = str((group['id'], group['window_start'], group['window_end'], group['check_interval_seconds'], group['position'], window[0])) if window else ''
    with LOCK:
        state = get_state()
        previous = state["items"].get(item_id, {})
        if previous.get("uncertain"):
            return
        if previous.get("schedule") != signature:
            previous = {**previous, "schedule": signature, "group_id": group["id"], "item_name": row["name"], "next_at": timestamp, "result": "scheduled"}
            state["items"][item_id] = previous
            save_state(state)
        if not manual and previous.get("next_at", 0) > timestamp:
            return
        # Save the next deadline before any remote call; restarting cannot
        # make an overdue item repeatedly buy the same priority.
        deadline = min(timestamp + group['check_interval_seconds'], window[1]) if window else timestamp
        previous.update(next_at=next_check(deadline, group), last_check=timestamp,
                        result="checking", position=None, position_source=None)
        state["items"][item_id] = previous
        save_state(state)
    item = account.get_item(id=item_id)
    if item is None or str(getattr(getattr(item, "user", None), "id", "")) != str(account.id):
        record(item_id, result="not_owned")
        return
    if item.status != ItemStatuses.APPROVED:
        record(item_id, result="inactive")
        return
    if not manual and getattr(item, "priority", PriorityTypes.DEFAULT) == PriorityTypes.DEFAULT:
        record(item_id, result="default_priority")
        return
    position, source = current_item_position(account, item_id, item)
    record(item_id, position=position, position_source=source)
    needed = position_requires_bump(position, group["position"])
    if not manual and needed is not True:
        record(item_id, result="unknown_position" if needed is None else "position_ok", position=position)
        return
    if not manual and active_window(clock(), group) is None:
        record(item_id, result="outside_window", position=position)
        return
    time.sleep(1)
    statuses = account.get_item_priority_statuses(item_id, item.raw_price)
    status = next((status for status in statuses if status.type == PriorityTypes.PREMIUM or money(status.price) > 0), None)
    if status is None:
        record(item_id, result="no_priority", position=position)
        return
    time.sleep(1)
    with LOCK:
        current = group_current(group["id"], item_id, manual)
        if current is None or current != group:
            record(item_id, result="settings_changed")
            return
        if not manual and active_window(clock(), group) is None:
            record(item_id, result="outside_window", position=position)
            return
        record(item_id, uncertain=True, result="attempting", attempted_at=clock(), quoted_price=str(status.price), position=position)
    try:
        account.increase_item_priority_status(item_id, status.id)
    except Exception as exc:
        # A timeout may follow a successful charge. Keep the item paused until
        # the seller explicitly checks and clears this state from the menu.
        record(item_id, uncertain=True, result="uncertain", error=type(exc).__name__)
        logger.error("Неопределённый результат поднятия: item=%s error=%s", item_id, type(exc).__name__)
        return
    record(item_id, uncertain=False, result="raised", last_success=clock(), error="")
    logger.info("Товар поднят: group=%s item=%s price=%s", group["id"], item_id, status.price)


def raise_group(account, gid, now=None):
    """Manual batch shares the scheduler lock and payment uncertainty guard."""
    if not _tick_lock.acquire(blocking=False):
        raise ValueError("Сейчас выполняется другая проверка или поднятие. Попробуйте позже.")
    try:
        if not ACTIVE.is_set():
            raise ValueError("Планировщик групп ещё не запущен.")
        if builtin_enabled():
            raise ValueError("Сначала отключите обычное автоподнятие бота.")
        group = next((g for g in get_config()['groups'] if g['id'] == gid), None)
        if group is None:
            raise ValueError("Группа удалена.")
        if not group['items']:
            raise ValueError("В группе нет привязанных товаров.")
        totals = dict(raised=0, skipped=0, errors=0, uncertain=0)
        for row in group['items']:
            if group_current(gid, row['id'], manual=True) != group:
                totals['skipped'] += 1
                continue
            try:
                process_item(account, group, row, now, manual=True)
                info = get_state()['items'].get(row['id'], {})
                if info.get('uncertain'):
                    totals['uncertain'] += 1
                elif info.get('result') == 'raised':
                    totals['raised'] += 1
                else:
                    totals['skipped'] += 1
            except Exception as exc:
                totals['errors'] += 1
                logger.error("Ошибка ручного поднятия: item=%s error=%s", row['id'], type(exc).__name__)
                try:
                    record(row['id'], result='check_error', error=type(exc).__name__)
                except (OSError, ValueError):
                    pass
        return totals
    finally:
        _tick_lock.release()


def tick(account, now=None):
    if not _tick_lock.acquire(blocking=False):
        return
    try:
        config = get_config()
        if not ACTIVE.is_set() or not config["enabled"] or builtin_enabled():
            return
        for group in config["groups"]:
            if not group["enabled"] or active_window(time.time() if now is None else now, group) is None:
                continue
            for row in group['items']:
                if group_current(group["id"], row["id"]) is None:
                    break
                try:
                    process_item(account, group, row, now)
                except Exception as exc:
                    try:
                        record(row['id'], result='check_error', error=type(exc).__name__)
                    except (OSError, ValueError):
                        pass
                    logger.error("Ошибка проверки товара: item=%s error=%s", row["id"], type(exc).__name__)
    finally:
        _tick_lock.release()


def start(plbot):
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        def run():
            while True:
                if ACTIVE.wait(5):
                    try:
                        account = getattr(plbot, "account", None)
                        if account is not None:
                            tick(account)
                    except Exception as exc:
                        logger.error("Ошибка планировщика: error=%s", type(exc).__name__)
                    time.sleep(1)
        _thread = Thread(target=run, daemon=True, name="bumpgroups")
        _thread.start()


async def on_enabled(module):
    ACTIVE.set()
    from plbot.playerokbot import get_playerok_bot
    bot = get_playerok_bot()
    if bot is not None:
        start(bot)


async def on_bot_init(plbot):
    start(plbot)


async def on_disabled(module):
    ACTIVE.clear()


