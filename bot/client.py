"""Binance Futures Testnet REST client wrapper."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    BinanceAPIError,
    ConfigurationError,
    RequestError,
    ValidationError,
    get_binance_error_message,
)
from .logging_config import get_logger

TESTNET_BASE_URL = "https://testnet.binancefuture.com"
GET_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
GET_RETRY_ATTEMPTS = 3
TIME_SYNC_TTL_SECONDS = 30.0
SENSITIVE_PARAM_KEYS = {"signature"}
SENSITIVE_HEADER_KEYS = {"X-MBX-APIKEY"}


class BinanceFuturesClient:
    """Minimal REST client for Binance USDT-M Futures Testnet."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        *,
        base_url: str = TESTNET_BASE_URL,
        recv_window: int = 5000,
        timeout: float = 10.0,
    ) -> None:
        if not base_url:
            raise ConfigurationError("A Binance base URL must be provided.")
        if recv_window <= 0 or recv_window > 60000:
            raise ConfigurationError("recv_window must be between 1 and 60000 milliseconds.")
        if timeout <= 0:
            raise ConfigurationError("timeout must be greater than zero.")

        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout = timeout
        self.logger = get_logger(self.__class__.__name__)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "trading-bot/1.0",
            }
        )
        if self.api_key:
            self.session.headers["X-MBX-APIKEY"] = self.api_key
        self._configure_session_retries()

        self._exchange_info_cache: dict[str, Any] | None = None
        self._time_offset_ms = 0
        self._last_time_sync_monotonic = 0.0

    def __enter__(self) -> "BinanceFuturesClient":
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    def ping(self) -> dict[str, Any]:
        response = self._request("GET", "/fapi/v1/ping")
        if not isinstance(response, dict):
            raise RequestError("Unexpected ping response from Binance.")
        return response

    def get_server_time(self) -> dict[str, Any]:
        response = self._request("GET", "/fapi/v1/time")
        if not isinstance(response, dict) or "serverTime" not in response:
            raise RequestError("Unexpected server time response from Binance.")
        return response

    def sync_server_time(self, *, force: bool = False) -> int:
        """Sync the local request timestamp against Binance server time."""
        if not force and self._is_time_sync_fresh():
            return self._time_offset_ms

        local_before = self._system_timestamp_ms()
        response = self.get_server_time()
        local_after = self._system_timestamp_ms()

        server_time = int(response["serverTime"])
        midpoint = local_before + ((local_after - local_before) // 2)
        self._time_offset_ms = server_time - midpoint
        self._last_time_sync_monotonic = time.monotonic()
        self.logger.info(
            "Synchronized Binance server time offset=%sms.",
            self._time_offset_ms,
            extra={"correlation_id": self._new_correlation_id()},
        )
        return self._time_offset_ms

    def get_exchange_info(self, *, use_cache: bool = True) -> dict[str, Any]:
        if use_cache and self._exchange_info_cache is not None:
            return self._exchange_info_cache

        response = self._request("GET", "/fapi/v1/exchangeInfo")
        if not isinstance(response, dict) or "symbols" not in response:
            raise RequestError("Unexpected exchange info response from Binance.")

        self._exchange_info_cache = response
        return response

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = symbol.strip().upper()
        symbols = self.get_exchange_info().get("symbols", [])
        for symbol_info in symbols:
            if symbol_info.get("symbol") == normalized_symbol:
                return symbol_info
        raise ValidationError(
            f"Symbol '{normalized_symbol}' is not listed on Binance Futures Testnet."
        )

    def get_mark_price(self, symbol: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/fapi/v1/premiumIndex",
            params={"symbol": symbol.strip().upper()},
        )
        if not isinstance(response, dict) or "markPrice" not in response:
            raise RequestError("Unexpected mark price response from Binance.")
        return response

    def get_account_balances(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/fapi/v2/balance", signed=True)
        if not isinstance(response, list):
            raise RequestError("Unexpected balance response from Binance.")
        return response

    def get_usdt_balance(self) -> dict[str, Any]:
        for balance in self.get_account_balances():
            if balance.get("asset") == "USDT":
                return balance
        raise RequestError("No USDT balance was returned by Binance.")

    def create_order(self, **params: Any) -> dict[str, Any]:
        response = self._request("POST", "/fapi/v1/order", params=params, signed=True)
        if not isinstance(response, dict):
            raise RequestError("Unexpected order response from Binance.")
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        allow_timestamp_retry: bool = True,
        correlation_id: str | None = None,
    ) -> Any:
        request_correlation_id = correlation_id or self._new_correlation_id()
        method_name = method.upper()
        url = f"{self.base_url}{path}"
        ordered_params = self._clean_params(params)

        if signed:
            self._ensure_credentials()
            self._ensure_time_synced()
            ordered_params.append(("timestamp", str(self._current_timestamp_ms())))
            ordered_params.append(("recvWindow", str(self.recv_window)))
            ordered_params.append(("signature", self._sign_query_string(ordered_params)))

        sanitized_headers = self._sanitize_headers(dict(self.session.headers))
        sanitized_params = self._sanitize_params(ordered_params)
        self.logger.debug(
            "Request start | method=%s url=%s signed=%s headers=%s params=%s",
            method_name,
            url,
            signed,
            sanitized_headers,
            sanitized_params,
            extra={"correlation_id": request_correlation_id},
        )

        started_at = time.monotonic()
        try:
            response = self.session.request(
                method=method_name,
                url=url,
                params=ordered_params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            elapsed_ms = self._elapsed_ms(started_at)
            self.logger.error(
                "Network request failed after %sms | method=%s path=%s params=%s error=%s",
                elapsed_ms,
                method_name,
                path,
                sanitized_params,
                exc,
                extra={"correlation_id": request_correlation_id},
            )
            raise RequestError(
                f"Request to Binance failed for {method_name} {path}.",
                original_error=exc,
                correlation_id=request_correlation_id,
                request_path=path,
            ) from exc

        elapsed_ms = self._elapsed_ms(started_at)
        try:
            parsed_response = self._parse_response(
                response,
                correlation_id=request_correlation_id,
                request_path=path,
            )
        except BinanceAPIError as exc:
            self._log_response(
                response=response,
                payload=sanitized_params,
                response_data=exc.response_data,
                elapsed_ms=elapsed_ms,
                correlation_id=request_correlation_id,
                error=exc,
            )
            if signed and exc.code == -1021 and allow_timestamp_retry:
                self.logger.warning(
                    "Binance rejected the request timestamp. Resynchronizing server time and retrying once.",
                    extra={"correlation_id": request_correlation_id},
                )
                self.sync_server_time(force=True)
                return self._request(
                    method=method_name,
                    path=path,
                    params=params,
                    signed=signed,
                    allow_timestamp_retry=False,
                    correlation_id=request_correlation_id,
                )
            raise

        self._log_response(
            response=response,
            payload=sanitized_params,
            response_data=parsed_response,
            elapsed_ms=elapsed_ms,
            correlation_id=request_correlation_id,
        )
        return parsed_response

    def _configure_session_retries(self) -> None:
        retry_strategy = Retry(
            total=GET_RETRY_ATTEMPTS,
            connect=GET_RETRY_ATTEMPTS,
            read=GET_RETRY_ATTEMPTS,
            status=GET_RETRY_ATTEMPTS,
            backoff_factor=0.5,
            allowed_methods=frozenset({"GET"}),
            status_forcelist=GET_RETRY_STATUS_CODES,
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _ensure_credentials(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ConfigurationError(
                "BINANCE_API_KEY and BINANCE_API_SECRET are required for signed requests."
            )

    def _ensure_time_synced(self) -> None:
        if not self._is_time_sync_fresh():
            self.sync_server_time(force=True)

    def _is_time_sync_fresh(self) -> bool:
        if self._last_time_sync_monotonic <= 0:
            return False
        return (time.monotonic() - self._last_time_sync_monotonic) < TIME_SYNC_TTL_SECONDS

    def _current_timestamp_ms(self) -> int:
        return self._system_timestamp_ms() + self._time_offset_ms

    @staticmethod
    def _system_timestamp_ms() -> int:
        return int(time.time() * 1000)

    def _sign_query_string(self, params: Sequence[tuple[str, str]]) -> str:
        query_string = urlencode(list(params))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _parse_response(
        self,
        response: requests.Response,
        *,
        correlation_id: str,
        request_path: str,
    ) -> Any:
        data = self._decode_response_data(response)
        api_error_code, api_error_message = self._extract_api_error(data)

        if response.ok and api_error_code is None:
            return data

        message = get_binance_error_message(api_error_code, api_error_message)
        if api_error_code is None and response.status_code >= 400:
            message = f"Binance API request failed with HTTP {response.status_code}."
            if api_error_message:
                message = f"{message} Binance message: {api_error_message}"

        raise BinanceAPIError(
            message,
            status_code=response.status_code,
            code=api_error_code,
            binance_message=api_error_message,
            response_data=data,
            correlation_id=correlation_id,
            request_path=request_path,
        )

    def _decode_response_data(self, response: requests.Response) -> Any:
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            raw_text = response.text.strip()
            return {"raw": raw_text} if raw_text else {}
        return self._normalize_response_data(data)

    @staticmethod
    def _normalize_response_data(data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, (dict, list)):
            return data
        return {"value": data}

    @staticmethod
    def _extract_api_error(data: Any) -> tuple[int | None, str | None]:
        if not isinstance(data, dict):
            return None, None
        code = data.get("code")
        if isinstance(code, int) and code < 0:
            api_message = data.get("msg")
            return code, api_message if isinstance(api_message, str) else None
        api_message = data.get("msg")
        if isinstance(api_message, str) and "error" in api_message.lower():
            return None, api_message
        return None, None

    def _log_response(
        self,
        *,
        response: requests.Response,
        payload: list[tuple[str, str]],
        response_data: Any,
        elapsed_ms: int,
        correlation_id: str,
        error: BinanceAPIError | None = None,
    ) -> None:
        if error is not None:
            self.logger.error(
                "Response error | status=%s elapsed_ms=%s code=%s binance_message=%s params=%s body=%s",
                response.status_code,
                elapsed_ms,
                error.code,
                error.binance_message,
                payload,
                response_data,
                extra={"correlation_id": correlation_id},
            )
            return

        self.logger.debug(
            "Response success | status=%s elapsed_ms=%s params=%s body=%s",
            response.status_code,
            elapsed_ms,
            payload,
            response_data,
            extra={"correlation_id": correlation_id},
        )

    @staticmethod
    def _clean_params(params: dict[str, Any] | None) -> list[tuple[str, str]]:
        cleaned: list[tuple[str, str]] = []
        for key in sorted((params or {}).keys()):
            value = params[key]
            if value is None or value == "":
                continue
            cleaned.append((key, BinanceFuturesClient._stringify_value(value)))
        return cleaned

    @staticmethod
    def _sanitize_params(params: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
        sanitized: list[tuple[str, str]] = []
        for key, value in params:
            if key in SENSITIVE_PARAM_KEYS:
                sanitized.append((key, "***"))
            else:
                sanitized.append((key, value))
        return sanitized

    @staticmethod
    def _sanitize_headers(headers: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in headers.items():
            if key in SENSITIVE_HEADER_KEYS:
                masked_value = "***"
                if isinstance(value, str) and len(value) >= 4:
                    masked_value = f"{value[:2]}***{value[-2:]}"
                sanitized[key] = masked_value
            else:
                sanitized[key] = value
        return sanitized

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.monotonic() - started_at) * 1000)

    @staticmethod
    def _new_correlation_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _stringify_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
