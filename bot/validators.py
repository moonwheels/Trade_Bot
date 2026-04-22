"""Validation helpers for CLI arguments and runtime configuration."""

from __future__ import annotations

import re
from math import isfinite
from decimal import Decimal, InvalidOperation

from .exceptions import ConfigurationError, ValidationError

VALID_SIDES = {"BUY", "SELL"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT"}
VALID_TIME_IN_FORCE = {"GTC", "IOC", "FOK", "GTX"}
VALID_POSITION_SIDES = {"BOTH", "LONG", "SHORT"}
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


def validate_symbol(symbol: str) -> str:
    normalized = (symbol or "").strip().upper()
    if not normalized:
        raise ValidationError("Symbol is required.")
    if not SYMBOL_PATTERN.fullmatch(normalized):
        raise ValidationError(
            "Symbol must contain only uppercase letters and digits, for example BTCUSDT."
        )
    if not normalized.endswith("USDT"):
        raise ValidationError("Only USDT-M Futures symbols ending with USDT are supported.")
    return normalized


def validate_side(side: str) -> str:
    normalized = (side or "").strip().upper()
    if normalized not in VALID_SIDES:
        raise ValidationError(f"Side must be one of {sorted(VALID_SIDES)}.")
    return normalized


def validate_order_type(order_type: str) -> str:
    normalized = (order_type or "").strip().upper()
    if normalized not in VALID_ORDER_TYPES:
        raise ValidationError(f"Order type must be one of {sorted(VALID_ORDER_TYPES)}.")
    return normalized


def _parse_positive_decimal(value: str | int | float | Decimal, field_name: str) -> Decimal:
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required.")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a valid decimal number.") from exc

    if not decimal_value.is_finite():
        raise ValidationError(f"{field_name} must be a finite decimal number.")
    if decimal_value <= 0:
        raise ValidationError(f"{field_name} must be greater than zero.")
    return decimal_value


def validate_quantity(quantity: str | int | float | Decimal) -> Decimal:
    return _parse_positive_decimal(quantity, "quantity")


def validate_price(
    price: str | int | float | Decimal | None,
    order_type: str,
) -> Decimal | None:
    normalized_order_type = validate_order_type(order_type)
    if normalized_order_type == "MARKET":
        if price in (None, ""):
            return None
        return _parse_positive_decimal(price, "price")
    return _parse_positive_decimal(price, "price")


def validate_time_in_force(time_in_force: str | None, order_type: str) -> str | None:
    normalized_order_type = validate_order_type(order_type)
    if normalized_order_type == "MARKET":
        if time_in_force:
            raise ValidationError("time_in_force is only valid for LIMIT orders.")
        return None

    normalized = (time_in_force or "GTC").strip().upper()
    if normalized not in VALID_TIME_IN_FORCE:
        raise ValidationError(
            f"time_in_force must be one of {sorted(VALID_TIME_IN_FORCE)} for LIMIT orders."
        )
    return normalized


def validate_position_side(position_side: str | None) -> str | None:
    if not position_side:
        return None
    normalized = position_side.strip().upper()
    if normalized not in VALID_POSITION_SIDES:
        raise ValidationError(f"position_side must be one of {sorted(VALID_POSITION_SIDES)}.")
    return normalized


def validate_client_order_id(client_order_id: str | None) -> str | None:
    if not client_order_id:
        return None
    normalized = client_order_id.strip()
    if not CLIENT_ORDER_ID_PATTERN.fullmatch(normalized):
        raise ValidationError(
            "client_order_id must be 1-36 characters long and contain only letters, digits, underscores, or hyphens."
        )
    return normalized


def validate_recv_window(value: str | int | None) -> int:
    raw_value = "5000" if value in (None, "") else str(value)
    try:
        recv_window = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError("BINANCE_RECV_WINDOW must be an integer.") from exc

    if recv_window <= 0 or recv_window > 60000:
        raise ConfigurationError("BINANCE_RECV_WINDOW must be between 1 and 60000.")
    return recv_window


def validate_timeout_seconds(value: str | int | float | None) -> float:
    raw_value = "10" if value in (None, "") else str(value)
    try:
        timeout_seconds = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError("BINANCE_TIMEOUT_SECONDS must be a number.") from exc

    if not isfinite(timeout_seconds):
        raise ConfigurationError("BINANCE_TIMEOUT_SECONDS must be a finite number.")
    if timeout_seconds <= 0:
        raise ConfigurationError("BINANCE_TIMEOUT_SECONDS must be greater than zero.")
    return timeout_seconds


def validate_order_inputs(
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: str | int | float | Decimal,
    price: str | int | float | Decimal | None,
    time_in_force: str | None,
    reduce_only: bool,
    position_side: str | None,
    client_order_id: str | None,
) -> dict[str, object]:
    validated_order_type = validate_order_type(order_type)
    validated_position_side = validate_position_side(position_side)

    if reduce_only and validated_position_side in {"LONG", "SHORT"}:
        raise ValidationError(
            "reduce_only cannot be combined with position_side LONG or SHORT."
        )

    return {
        "symbol": validate_symbol(symbol),
        "side": validate_side(side),
        "order_type": validated_order_type,
        "quantity": validate_quantity(quantity),
        "price": validate_price(price, validated_order_type),
        "time_in_force": validate_time_in_force(time_in_force, validated_order_type),
        "reduce_only": bool(reduce_only),
        "position_side": validated_position_side,
        "client_order_id": validate_client_order_id(client_order_id),
    }
