"""Order preparation and execution logic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

from .client import BinanceFuturesClient
from .exceptions import OrderExecutionError, ValidationError
from .logging_config import get_logger


@dataclass(slots=True)
class OrderRequest:
    """Validated order parameters used by the service layer."""

    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    time_in_force: str | None = None
    reduce_only: bool = False
    position_side: str | None = None
    client_order_id: str | None = None


class OrderService:
    """Prepare exchange-compliant orders and submit them via the REST client."""

    def __init__(self, client: BinanceFuturesClient) -> None:
        self.client = client
        self.logger = get_logger(self.__class__.__name__)

    def submit_order(self, order: OrderRequest) -> dict[str, Any]:
        symbol_info = self.client.get_symbol_info(order.symbol)
        self._validate_symbol_is_tradeable(symbol_info, order.symbol)

        quantity = self._normalize_quantity(order.quantity, symbol_info, order.order_type)
        price = None
        if order.order_type == "LIMIT":
            if order.price is None:
                raise ValidationError("price is required for LIMIT orders.")
            price = self._normalize_price(order.price, symbol_info)

        self._validate_min_notional(order.symbol, symbol_info, quantity, price)

        payload: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.order_type,
            "quantity": self._format_decimal(quantity),
            "newOrderRespType": "RESULT",
        }
        if price is not None:
            payload["price"] = self._format_decimal(price)
        if order.time_in_force is not None:
            payload["timeInForce"] = order.time_in_force
        if order.reduce_only:
            payload["reduceOnly"] = True
        if order.position_side:
            payload["positionSide"] = order.position_side
        if order.client_order_id:
            payload["newClientOrderId"] = order.client_order_id

        self.logger.info(
            "Submitting %s %s order for %s with quantity=%s%s",
            order.side,
            order.order_type,
            order.symbol,
            self._format_decimal(quantity),
            f" price={self._format_decimal(price)}" if price is not None else "",
        )
        response = self.client.create_order(**payload)
        self.logger.info(
            "Order accepted by Binance: orderId=%s status=%s",
            response.get("orderId"),
            response.get("status"),
        )
        return response

    def _validate_symbol_is_tradeable(self, symbol_info: dict[str, Any], symbol: str) -> None:
        if symbol_info.get("status") != "TRADING":
            raise OrderExecutionError(f"Symbol '{symbol}' is not currently in TRADING status.")
        if symbol_info.get("quoteAsset") != "USDT" or symbol_info.get("marginAsset") != "USDT":
            raise ValidationError("Only USDT-M Futures symbols are supported.")

    def _normalize_quantity(
        self,
        quantity: Decimal,
        symbol_info: dict[str, Any],
        order_type: str,
    ) -> Decimal:
        lot_filter = self._find_filter(
            symbol_info,
            "MARKET_LOT_SIZE" if order_type == "MARKET" else "LOT_SIZE",
            required=False,
        )
        if lot_filter is None:
            lot_filter = self._find_filter(symbol_info, "LOT_SIZE")

        quantity_precision = int(symbol_info.get("quantityPrecision", 0))
        return self._normalize_exchange_value(
            value=quantity,
            step=Decimal(lot_filter["stepSize"]),
            min_value=Decimal(lot_filter["minQty"]),
            max_value=Decimal(lot_filter["maxQty"]),
            precision=quantity_precision,
            field_name="quantity",
            symbol=symbol_info.get("symbol", ""),
        )

    def _normalize_price(self, price: Decimal, symbol_info: dict[str, Any]) -> Decimal:
        price_filter = self._find_filter(symbol_info, "PRICE_FILTER")
        price_precision = int(symbol_info.get("pricePrecision", 0))
        return self._normalize_exchange_value(
            value=price,
            step=Decimal(price_filter["tickSize"]),
            min_value=Decimal(price_filter["minPrice"]),
            max_value=Decimal(price_filter["maxPrice"]),
            precision=price_precision,
            field_name="price",
            symbol=symbol_info.get("symbol", ""),
        )

    def _validate_min_notional(
        self,
        symbol: str,
        symbol_info: dict[str, Any],
        quantity: Decimal,
        price: Decimal | None,
    ) -> None:
        notional_filter = self._find_filter(symbol_info, "MIN_NOTIONAL", required=False)
        if notional_filter is None:
            notional_filter = self._find_filter(symbol_info, "NOTIONAL", required=False)
        if notional_filter is None:
            return

        min_notional_value = (
            notional_filter.get("notional")
            or notional_filter.get("minNotional")
            or notional_filter.get("minNotionalValue")
        )
        if not min_notional_value:
            return

        min_notional = Decimal(str(min_notional_value))
        if min_notional <= 0:
            return

        reference_price = price
        if reference_price is None:
            mark_price = self.client.get_mark_price(symbol)
            reference_price = Decimal(str(mark_price["markPrice"]))

        notional = quantity * reference_price
        if notional < min_notional:
            raise ValidationError(
                f"Order notional {self._format_decimal(notional)} is below the minimum {self._format_decimal(min_notional)}."
            )

    @staticmethod
    def _find_filter(
        symbol_info: dict[str, Any],
        filter_type: str,
        *,
        required: bool = True,
    ) -> dict[str, Any] | None:
        for filter_info in symbol_info.get("filters", []):
            if filter_info.get("filterType") == filter_type:
                return filter_info
        if required:
            raise OrderExecutionError(
                f"Required exchange filter '{filter_type}' was not found for {symbol_info.get('symbol')}."
            )
        return None

    @staticmethod
    def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return value
        return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

    def _normalize_exchange_value(
        self,
        *,
        value: Decimal,
        step: Decimal,
        min_value: Decimal,
        max_value: Decimal,
        precision: int,
        field_name: str,
        symbol: str,
    ) -> Decimal:
        rounded_value = self._round_down_to_step(value, step)
        normalized_value = self._apply_precision(rounded_value, step, precision)

        if normalized_value != value:
            self.logger.warning(
                "Adjusted %s from %s to %s for %s to satisfy step=%s and precision=%s.",
                field_name,
                self._format_decimal(value),
                self._format_decimal(normalized_value),
                symbol,
                self._format_decimal(step),
                precision,
            )

        if normalized_value <= 0:
            raise ValidationError(
                f"{field_name.capitalize()} rounds down to zero for this symbol's exchange rules."
            )
        if normalized_value < min_value:
            raise ValidationError(
                f"{field_name.capitalize()} {self._format_decimal(normalized_value)} is below the minimum {self._format_decimal(min_value)}."
            )
        if max_value > 0 and normalized_value > max_value:
            raise ValidationError(
                f"{field_name.capitalize()} {self._format_decimal(normalized_value)} exceeds the maximum {self._format_decimal(max_value)}."
            )
        if self._decimal_places(normalized_value) > precision:
            raise ValidationError(
                f"{field_name.capitalize()} exceeds the allowed precision of {precision} decimals for {symbol}."
            )
        return normalized_value

    @staticmethod
    def _apply_precision(value: Decimal, step: Decimal, precision: int) -> Decimal:
        if precision < 0:
            return value
        step_decimals = OrderService._decimal_places(step)
        scale = min(step_decimals, precision) if step_decimals else precision
        if scale <= 0:
            return value.quantize(Decimal("1"), rounding=ROUND_DOWN)
        quantum = Decimal("1").scaleb(-scale)
        return value.quantize(quantum, rounding=ROUND_DOWN)

    @staticmethod
    def _decimal_places(value: Decimal) -> int:
        exponent = value.normalize().as_tuple().exponent
        return abs(exponent) if exponent < 0 else 0

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        text = format(value.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
