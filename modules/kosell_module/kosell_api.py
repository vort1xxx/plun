from __future__ import annotations

from logging import getLogger

import requests

from .meta import NAME


# Часовой API из исходного KOSellRent. Новый публичный API на kodario.pro
# пока документирует только аренду на дни и не подходит для произвольных часов.
API_BASE = "https://www.kosell.store/api/v1"
TIMEOUT = 25
logger = getLogger(f"{NAME}.api")


class KOSellAPIError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class KOSellAPI:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs):
        payload = kwargs.get("json") or kwargs.get("params") or {}
        safe_payload = {
            key: payload[key]
            for key in ("product_id", "hours", "duration_hours", "currency", "search")
            if key in payload
        }
        logger.info("KOSell API запрос: %s %s payload=%s", method, path, safe_payload)
        try:
            response = self.session.request(method, f"{API_BASE}{path}", timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            logger.exception("KOSell API недоступен: %s %s (%s)", method, path, type(exc).__name__)
            raise KOSellAPIError("connection_error") from exc
        try:
            body = response.json()
        except ValueError:
            logger.error("KOSell API вернул не-JSON: %s %s status=%s", method, path, response.status_code)
            raise KOSellAPIError("invalid_json_response", response.status_code)
        body_keys = sorted(body.keys()) if isinstance(body, dict) else []
        logger.info(
            "KOSell API ответ: %s %s status=%s type=%s keys=%s",
            method,
            path,
            response.status_code,
            type(body).__name__,
            body_keys,
        )
        if not response.ok or (isinstance(body, dict) and (body.get("success") is False or body.get("error"))):
            code = "request_failed"
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    code = str(error.get("code") or error.get("message") or body.get("detail") or code)
                else:
                    code = str(error or body.get("detail") or code)
            logger.error("KOSell API отклонил запрос: %s %s status=%s code=%s", method, path, response.status_code, code)
            raise KOSellAPIError(code, response.status_code)
        return body

    def products(self, query: str = "", currency: str = "RUB") -> list[dict]:
        result = self._request("GET", "/rental/products", params={
            "currency": currency,
            "search": query,
        })
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            return result["data"]
        raise KOSellAPIError("unexpected_products_response")

    def calculate_price(self, product_id: int, hours: int):
        return self._request("POST", "/rental/calculate-price", json={
            "product_id": int(product_id),
            "hours": int(hours),
        })

    def rent(self, product_id: int, hours: int, currency: str):
        return self._request("POST", "/rental/rent", json={
            "product_id": int(product_id),
            "duration_hours": int(hours),
            "currency": currency,
        })

    def credentials(self, rental_uid: str):
        return self._request("GET", f"/rental/{rental_uid}/credentials")

    def code(self, rental_uid: str):
        return self._request("GET", f"/rental/{rental_uid}/code")
