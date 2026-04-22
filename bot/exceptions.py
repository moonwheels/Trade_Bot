"""Custom exception types for the trading bot."""

from __future__ import annotations

from typing import Any

BINANCE_ERROR_MESSAGES: dict[int, str] = {
    -1021: "Timestamp for this request is outside the configured recvWindow. The local clock may be out of sync with Binance.",
    -1022: "Request signature is not valid. Verify the API secret and the exact signed query string.",
    -1100: "A parameter contains invalid characters.",
    -1102: "A required parameter is missing or malformed.",
    -1111: "Parameter precision exceeds the maximum allowed for this symbol.",
    -2014: "API key format is invalid.",
    -2015: "API key, IP whitelist, or permissions are invalid for this Futures endpoint.",
    -2019: "Insufficient margin is available for this order.",
}


def get_binance_error_message(code: int | None, api_message: str | None = None) -> str:
    """Return a readable Binance error message."""
    if code in BINANCE_ERROR_MESSAGES:
        base_message = BINANCE_ERROR_MESSAGES[code]
        if api_message:
            return f"{base_message} Binance message: {api_message}"
        return base_message
    return api_message or "Binance API request failed."


class TradingBotError(Exception):
    """Base exception for all bot-specific errors."""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "errorType": self.__class__.__name__,
            "message": self.args[0] if self.args else str(self),
        }
        correlation_id = getattr(self, "correlation_id", None)
        if correlation_id:
            payload["correlationId"] = correlation_id
        return payload


class ConfigurationError(TradingBotError):
    """Raised when required configuration is missing or invalid."""


class ValidationError(TradingBotError):
    """Raised when user input fails validation."""


class RequestError(TradingBotError):
    """Raised when an HTTP request fails before a valid Binance response is received."""

    def __init__(
        self,
        message: str,
        *,
        original_error: Exception | None = None,
        correlation_id: str | None = None,
        request_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.correlation_id = correlation_id
        self.request_path = request_path

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.request_path:
            parts.append(f"path={self.request_path}")
        if self.correlation_id:
            parts.append(f"correlation_id={self.correlation_id}")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.request_path:
            payload["requestPath"] = self.request_path
        if self.original_error is not None:
            payload["cause"] = self.original_error.__class__.__name__
        return payload


class BinanceAPIError(TradingBotError):
    """Raised when Binance returns a non-success response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: int | None = None,
        binance_message: str | None = None,
        response_data: Any | None = None,
        correlation_id: str | None = None,
        request_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.binance_message = binance_message
        self.response_data = response_data
        self.correlation_id = correlation_id
        self.request_path = request_path

    def __str__(self) -> str:
        parts = [super().__str__(), f"status={self.status_code}"]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.binance_message:
            parts.append(f"binance_message={self.binance_message}")
        if self.request_path:
            parts.append(f"path={self.request_path}")
        if self.correlation_id:
            parts.append(f"correlation_id={self.correlation_id}")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["statusCode"] = self.status_code
        if self.code is not None:
            payload["binanceCode"] = self.code
        if self.binance_message:
            payload["binanceMessage"] = self.binance_message
        if self.request_path:
            payload["requestPath"] = self.request_path
        return payload


class OrderExecutionError(TradingBotError):
    """Raised when an order cannot be prepared or submitted safely."""
