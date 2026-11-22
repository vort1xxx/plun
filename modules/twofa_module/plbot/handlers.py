import asyncio
import logging
import time
from threading import Lock

from playerokapi.enums import ItemDealDirections, ItemDealStatuses

from ..logic import active_grants, credential_id, find_mapping, generate_code
from ..meta import NAME
from ..storage import LOCK, get_config, get_state, save_state

logger = logging.getLogger(NAME)
_issue_lock = Lock()


def send(plbot, chat_id, text):
    plbot.account.send_message(chat_id=str(chat_id), text=text)


def issue(plbot, deal_id):
    # Serialize requests so repeated commands cannot race with confirmation.
    with _issue_lock:
        config = get_config()
        if not config["enabled"]:
            return
        with LOCK:
            grant = get_state()["deals"].get(deal_id)
        if not grant or grant.get("revoked"):
            return
        mapping = next((row for row in config["mappings"] if row["item_id"] == grant["mapping_id"]), None)
        if mapping is None or grant.get("credential_id") != credential_id(mapping) or grant.get("expires_at") and grant["expires_at"] <= time.time():
            return
        if time.time() - grant.get("last_issued_at", 0) < 5:
            return
        # A missed refund event must not leave access to an account enabled.
        live = plbot.account.get_deal(deal_id)
        allowed = {ItemDealStatuses.PAID, ItemDealStatuses.PENDING, ItemDealStatuses.SENT,
                   ItemDealStatuses.CONFIRMED, ItemDealStatuses.CONFIRMED_AUTOMATICALLY}
        if not live or live.status not in allowed or str(getattr(getattr(live, "user", None), "id", "")) != grant["buyer_id"]:
            send(plbot, grant["chat_id"], "Выдача 2FA недоступна: не удалось подтвердить оплаченный статус сделки.")
            return
        _, seconds = generate_code(mapping)
        if seconds < 5:
            time.sleep(seconds + 0.1)
        with LOCK:
            current = get_state()["deals"].get(deal_id)
            fresh_config = get_config()
            fresh_mapping = next((row for row in fresh_config["mappings"] if row["item_id"] == grant["mapping_id"]), None)
            if not fresh_config["enabled"] or not current or current.get("revoked") or fresh_mapping is None:
                return
            if credential_id(fresh_mapping) != grant["credential_id"] or current.get("expires_at") and current["expires_at"] <= time.time():
                return
        config, mapping = fresh_config, fresh_mapping
        code, seconds = generate_code(mapping)
        text = config["template"].format(code=code, seconds=seconds, account=mapping["label"], item=mapping["item_name"])
        send(plbot, grant["chat_id"], text)
        with LOCK:
            state = get_state()
            current = state["deals"].get(deal_id)
            if current is None:
                return
            current["last_issued_at"] = time.time()
            current["issued"] = True
            save_state(state)
        logger.info("Код выдан: deal=%s chat=%s", deal_id, grant["chat_id"])
        if config["auto_complete"] and live.status in {ItemDealStatuses.PAID, ItemDealStatuses.PENDING}:
            try:
                plbot.account.update_deal(deal_id, ItemDealStatuses.SENT)
                logger.info("Сделка отмечена SENT: deal=%s", deal_id)
            except Exception as exc:
                logger.error("Не удалось отметить SENT: deal=%s error=%s", deal_id, type(exc).__name__)


def register_deal(plbot, event):
    config = get_config()
    if not config["enabled"]:
        return
    deal = event.deal
    if getattr(deal, "direction", None) == ItemDealDirections.IN or getattr(deal, "status", None) == ItemDealStatuses.ROLLED_BACK:
        return
    item = getattr(deal, "item", None)
    mapping = find_mapping(config["mappings"], getattr(item, "id", ""), getattr(item, "name", ""))
    if mapping is None:
        return
    deal_id = str(getattr(deal, "id", "") or "")
    chat_id = str(getattr(event.chat, "id", "") or "")
    buyer_id = str(getattr(getattr(deal, "user", None), "id", "") or "")
    if not all((deal_id, chat_id, buyer_id)):
        logger.error("Сделка не содержит ID покупателя/чата: deal=%s", deal_id)
        return
    with LOCK:
        state = get_state()
        existing = state["deals"].get(deal_id)
        if existing:
            if config["auto_issue"] and not existing.get("issued") and not existing.get("revoked"):
                retry = True
            else:
                return
        else:
            retry = False
            now = time.time()
            hours = mapping["access_hours"]
            state["deals"][deal_id] = dict(deal_id=deal_id, mapping_id=mapping["item_id"],
                chat_id=chat_id, buyer_id=buyer_id, created_at=now,
                expires_at=now + hours * 3600 if hours else 0, issued=False, revoked=False,
                credential_id=credential_id(mapping))
            save_state(state)
    logger.info("Оплаченный товар привязан к 2FA: deal=%s item=%s", deal_id, mapping["item_id"])
    if config["auto_issue"]:
        issue(plbot, deal_id)
    elif not retry:
        send(plbot, chat_id, f"🔐 Для получения 2FA-кода напишите {config['command']}.\nЕсли товаров несколько: {config['command']} ID_сделки.")


async def on_new_deal(plbot, event):
    try:
        await asyncio.to_thread(register_deal, plbot, event)
    except Exception as exc:
        # Exception messages can contain request bodies; never log secret/code.
        logger.error("Ошибка обработки оплаты: error=%s", type(exc).__name__)


def handle_command(plbot, event):
    config = get_config()
    if not config["enabled"]:
        return
    message = getattr(event, "message", None)
    sender = str(getattr(getattr(message, "user", None), "id", "") or "")
    text = str(getattr(message, "text", "") or "").strip()
    parts = text.split(maxsplit=1)
    if not parts or parts[0].casefold() != config["command"] or not sender or sender == str(plbot.account.id):
        return
    chat_id = str(getattr(event.chat, "id", "") or "")
    if not chat_id:
        return
    with LOCK:
        state = get_state()
        grants = [pair for pair in active_grants(state, chat_id, sender, config["mappings"]) if not pair[0].get("revoked")]
    if len(parts) == 2:
        arg = parts[1].strip()
        exact = [pair for pair in grants if pair[0]["deal_id"] == arg]
        grants = exact or [pair for pair in grants if pair[1]["label"].casefold() == arg.casefold()]
    # Repeated purchases of the same listing use the latest valid grant.
    unique = {}
    for pair in grants:
        unique.setdefault(pair[0]["mapping_id"], pair)
    grants = list(unique.values())
    if not grants:
        send(plbot, chat_id, "Нет доступного 2FA для оплаченного товара в этом чате. Обратитесь к продавцу.")
        return
    if len(grants) > 1:
        for start in range(0, len(grants), 10):
            choices = "\n".join(f"• {mapping['label']}: {config['command']} {grant['deal_id']}" for grant, mapping in grants[start:start + 10])
            send(plbot, chat_id, "Выберите товар для выдачи кода:\n" + choices)
        return
    issue(plbot, grants[0][0]["deal_id"])


async def on_new_message(plbot, event):
    try:
        await asyncio.to_thread(handle_command, plbot, event)
    except Exception as exc:
        logger.error("Ошибка выдачи 2FA: error=%s", type(exc).__name__)
        try:
            config = get_config()
            text = str(getattr(getattr(event, "message", None), "text", "") or "").split()
            if text and text[0].casefold() == config["command"]:
                await asyncio.to_thread(send, plbot, event.chat.id, "Не удалось получить 2FA-код. Обратитесь к продавцу.")
        except Exception:
            pass


def revoke(deal_id):
    with LOCK:
        state = get_state()
        if deal_id in state["deals"]:
            state["deals"][deal_id]["revoked"] = True
            save_state(state)
            logger.info("Доступ отозван после возврата: deal=%s", deal_id)


async def on_rollback(plbot, event):
    try:
        await asyncio.to_thread(revoke, str(event.deal.id))
    except Exception as exc:
        logger.error("Ошибка отзыва доступа: error=%s", type(exc).__name__)
