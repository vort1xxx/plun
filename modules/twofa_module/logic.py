"""TOTP generation and configuration validation; no bot dependencies."""
import base64
import hashlib
import hmac
import re
import struct
import time
from string import Formatter
from urllib.parse import parse_qs, unquote, urlparse

DEFAULT_TEMPLATE = "🔐 2FA-код для {account}: {code}\nДействует ещё {seconds} сек."
DEFAULT_CONFIG = {
    "enabled": True, "command": "!2fa", "auto_issue": False,
    "auto_complete": False, "template": DEFAULT_TEMPLATE, "mappings": [],
}


def parse_secret(raw):
    """Accept a Base32 secret or an otpauth://totp URI."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Введите Base32-секрет или ссылку otpauth://totp.")
    raw = raw.strip()
    digits, period, algorithm, label = 6, 30, "SHA1", ""
    if raw.lower().startswith("otpauth://"):
        uri = urlparse(raw)
        if uri.netloc.lower() != "totp":
            raise ValueError("Поддерживаются только TOTP-секреты, HOTP не подходит.")
        params = parse_qs(uri.query)
        raw = params.get("secret", [""])[0]
        try:
            digits = int(params.get("digits", ["6"])[0])
            period = int(params.get("period", ["30"])[0])
        except ValueError:
            raise ValueError("Некорректные параметры digits или period.") from None
        algorithm = params.get("algorithm", ["SHA1"])[0].upper()
        label = unquote(uri.path.lstrip("/"))
    secret = re.sub(r"\s+", "", raw).upper().rstrip("=")
    if not re.fullmatch(r"[A-Z2-7]+", secret):
        raise ValueError("Секрет должен быть в Base32: буквы A–Z и цифры 2–7.")
    try:
        decoded = base64.b32decode(secret + "=" * (-len(secret) % 8))
    except (ValueError, base64.binascii.Error):
        raise ValueError("Некорректная длина Base32-секрета.") from None
    if not decoded:
        raise ValueError("Пустой секрет.")
    validate_parameters(digits, period, algorithm)
    return dict(secret=secret, digits=digits, period=period, algorithm=algorithm, label=label)


def validate_parameters(digits, period, algorithm):
    if type(digits) is not int or digits not in (6, 8):
        raise ValueError("Код должен содержать 6 или 8 цифр.")
    if type(period) is not int or not 15 <= period <= 300:
        raise ValueError("Период кода должен быть от 15 до 300 секунд.")
    if algorithm not in ("SHA1", "SHA256", "SHA512"):
        raise ValueError("Поддерживаются SHA1, SHA256 и SHA512.")


def generate_code(mapping, timestamp=None):
    digits = mapping.get("digits", 6)
    period = mapping.get("period", 30)
    algorithm = mapping.get("algorithm", "SHA1")
    validate_parameters(digits, period, algorithm)
    secret = parse_secret(mapping["secret"])["secret"]
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    now = int(time.time() if timestamp is None else timestamp)
    digest = hmac.new(key, struct.pack(">Q", now // period), getattr(hashlib, algorithm.lower())).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(value % (10 ** digits)).zfill(digits), period - now % period


def valid_template(template):
    if not isinstance(template, str) or not 1 <= len(template) <= 2000:
        return False
    try:
        parts = list(Formatter().parse(template))
        names = {name for _, name, _, _ in parts if name is not None}
        if "code" not in names or not names <= {"code", "account", "item", "seconds"}:
            return False
        if any(spec or conv for _, name, spec, conv in parts if name is not None):
            return False
        template.format(code="123456", account="account", item="item", seconds=30)
        return True
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("Конфигурация должна быть объектом.")
    result = {**DEFAULT_CONFIG, **raw}
    for flag in ("enabled", "auto_issue", "auto_complete"):
        if type(result[flag]) is not bool:
            raise ValueError(f"Поле {flag} должно быть true или false.")
    if not isinstance(result["command"], str) or not re.fullmatch(r"![\w-]{1,20}", result["command"]):
        raise ValueError("Команда должна начинаться с !, например !2fa.")
    result["command"] = result["command"].casefold()
    if not valid_template(result["template"]):
        raise ValueError("Шаблон должен содержать {code}; доступны {account}, {item}, {seconds} без спецификаторов формата.")
    if not isinstance(result["mappings"], list) or len(result["mappings"]) > 500:
        raise ValueError("Привязки должны быть списком (до 500 товаров).")
    mappings, ids = [], set()
    for row in result["mappings"]:
        if not isinstance(row, dict):
            raise ValueError("Каждая привязка должна быть объектом.")
        item_id = row.get("item_id")
        if isinstance(item_id, str):
            item_id = item_id.strip()
        if not isinstance(item_id, str) or not item_id.strip() or len(item_id) > 100 or item_id in ids:
            raise ValueError("Некорректный или повторяющийся ID товара.")
        ids.add(item_id)
        parsed = parse_secret(row.get("secret"))
        digits, period = row.get("digits", parsed["digits"]), row.get("period", parsed["period"])
        algorithm = row.get("algorithm", parsed["algorithm"])
        validate_parameters(digits, period, algorithm)
        hours = row.get("access_hours", 0)
        if type(hours) is not int or not 0 <= hours <= 87600:
            raise ValueError("Срок доступа: от 0 до 87600 часов; 0 — без ограничения.")
        label = row.get("label") or parsed["label"] or row.get("item_name") or item_id
        name = row.get("item_name") or item_id
        if not isinstance(label, str) or len(label) > 150 or not isinstance(name, str) or len(name) > 500:
            raise ValueError("Название аккаунта — до 150 символов, товара — до 500.")
        mappings.append(dict(item_id=item_id.strip(), item_name=name, label=label,
                             secret=parsed["secret"], digits=digits, period=period,
                             algorithm=algorithm, access_hours=hours))
    result["mappings"] = mappings
    return {key: result[key] for key in DEFAULT_CONFIG}


def find_mapping(mappings, item_id, item_name):
    direct = next((row for row in mappings if row["item_id"] == str(item_id)), None)
    if direct is not None:
        return direct
    name = " ".join(str(item_name or "").split()).casefold()
    matches = [row for row in mappings if name and " ".join(row["item_name"].split()).casefold() == name]
    return matches[0] if len(matches) == 1 else None


def active_grants(state, chat_id, buyer_id, mappings, now=None):
    now = time.time() if now is None else now
    by_id = {row["item_id"]: row for row in mappings}
    results = []
    for grant in state.get("deals", {}).values():
        mapping = by_id.get(grant.get("mapping_id"))
        if mapping is None or str(grant.get("chat_id")) != str(chat_id) or str(grant.get("buyer_id")) != str(buyer_id):
            continue
        if grant.get("revoked") or grant.get("credential_id") != credential_id(mapping):
            continue
        if grant.get("expires_at") and grant["expires_at"] <= now:
            continue
        results.append((grant, mapping))
    return sorted(results, key=lambda pair: pair[0]["created_at"], reverse=True)


def credential_id(mapping):
    values = (mapping["secret"], mapping.get("algorithm", "SHA1"), mapping.get("digits", 6), mapping.get("period", 30))
    return hashlib.sha256(repr(values).encode()).hexdigest()
