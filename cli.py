"""Command-line entry point for the Binance Futures Testnet trading bot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
TESTNET_BASE_URL = "https://testnet.binancefuture.com"
load_dotenv(ENV_PATH)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI trading bot for Binance Futures Testnet (USDT-M only).",
        epilog=(
            "Examples:\n"
            "  python cli.py --test-connection\n"
            "  python cli.py price --symbol BTCUSDT\n"
            "  python cli.py place-order --symbol BTCUSDT --side BUY --type MARKET --quantity 0.002\n"
            "  python cli.py place-order --symbol BTCUSDT --side SELL --type LIMIT --quantity 0.002 --price 80000"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Test public connectivity and verify the configured Binance Testnet API keys.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("TRADING_BOT_LOG_LEVEL", "INFO"),
        help="Override the configured logging level: DEBUG, INFO, WARNING, ERROR, or CRITICAL.",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    ping_parser = subparsers.add_parser(
        "ping",
        help="Check public connectivity to Binance Futures Testnet.",
    )
    ping_parser.set_defaults(handler=handle_ping)

    balance_parser = subparsers.add_parser(
        "balance",
        help="Fetch the authenticated USDT Futures balance from Testnet.",
    )
    balance_parser.set_defaults(handler=handle_balance)

    price_parser = subparsers.add_parser(
        "price",
        help="Fetch the current mark price for a USDT-M symbol.",
    )
    price_parser.add_argument(
        "--symbol",
        required=True,
        help="USDT-M symbol, for example BTCUSDT.",
    )
    price_parser.set_defaults(handler=handle_price)

    order_parser = subparsers.add_parser(
        "place-order",
        help="Submit a MARKET or LIMIT order to Binance Futures Testnet.",
    )
    order_parser.add_argument(
        "--symbol",
        required=True,
        help="USDT-M symbol, for example BTCUSDT.",
    )
    order_parser.add_argument("--side", required=True, help="Order side: BUY or SELL.")
    order_parser.add_argument(
        "--type",
        dest="order_type",
        required=True,
        help="Order type: MARKET or LIMIT.",
    )
    order_parser.add_argument("--quantity", required=True, help="Order quantity, for example 0.002.")
    order_parser.add_argument(
        "--price",
        help="Limit price. Required only for LIMIT orders.",
    )
    order_parser.add_argument(
        "--time-in-force",
        help="Time in force for LIMIT orders. Defaults to GTC when omitted.",
    )
    order_parser.add_argument(
        "--reduce-only",
        action="store_true",
        help="Submit the order with reduceOnly=true.",
    )
    order_parser.add_argument(
        "--position-side",
        help="Optional Binance position side: BOTH, LONG, or SHORT.",
    )
    order_parser.add_argument(
        "--client-order-id",
        help="Optional custom client order identifier (1-36 chars, letters, digits, _ or -).",
    )
    order_parser.set_defaults(handler=handle_place_order)

    return parser


def create_client_from_env() -> "BinanceFuturesClient":
    from bot.client import BinanceFuturesClient
    from bot.validators import validate_recv_window, validate_timeout_seconds

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    recv_window = validate_recv_window(os.getenv("BINANCE_RECV_WINDOW", "5000"))
    timeout = validate_timeout_seconds(os.getenv("BINANCE_TIMEOUT_SECONDS", "10"))
    return BinanceFuturesClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=TESTNET_BASE_URL,
        recv_window=recv_window,
        timeout=timeout,
    )


def handle_ping(
    args: argparse.Namespace,
    client: "BinanceFuturesClient",
    order_service: "OrderService",
) -> int:
    del args, order_service
    client.ping()
    server_time = client.get_server_time()
    print_json(
        {
            "status": "ok",
            "baseUrl": TESTNET_BASE_URL,
            "serverTime": server_time["serverTime"],
        }
    )
    return 0


def handle_test_connection(
    args: argparse.Namespace,
    client: "BinanceFuturesClient",
    order_service: "OrderService",
) -> int:
    from bot.exceptions import TradingBotError

    del args, order_service
    server_time = client.get_server_time()
    result: dict[str, Any] = {
        "status": "ok",
        "success": True,
        "baseUrl": TESTNET_BASE_URL,
        "publicConnectivity": True,
        "serverTime": server_time["serverTime"],
    }

    try:
        balance = client.get_usdt_balance()
    except TradingBotError as exc:
        result.update(
            {
                "status": "error",
                "success": False,
                "authenticated": False,
                "error": build_error_payload(exc),
            }
        )
        print_json(result)
        return 1

    result.update(
        {
            "authenticated": True,
            "asset": balance.get("asset"),
            "availableBalance": balance.get("availableBalance"),
            "updateTime": balance.get("updateTime"),
        }
    )
    print_json(result)
    return 0


def handle_balance(
    args: argparse.Namespace,
    client: "BinanceFuturesClient",
    order_service: "OrderService",
) -> int:
    del args, order_service
    balance = client.get_usdt_balance()
    print_json(
        {
            "asset": balance.get("asset"),
            "balance": balance.get("balance"),
            "availableBalance": balance.get("availableBalance"),
            "crossWalletBalance": balance.get("crossWalletBalance"),
            "crossUnPnl": balance.get("crossUnPnl"),
            "maxWithdrawAmount": balance.get("maxWithdrawAmount"),
            "updateTime": balance.get("updateTime"),
        }
    )
    return 0


def handle_price(
    args: argparse.Namespace,
    client: "BinanceFuturesClient",
    order_service: "OrderService",
) -> int:
    from bot.validators import validate_symbol

    del order_service
    symbol = validate_symbol(args.symbol)
    mark_price = client.get_mark_price(symbol)
    print_json(
        {
            "symbol": mark_price.get("symbol", symbol),
            "markPrice": mark_price.get("markPrice"),
            "indexPrice": mark_price.get("indexPrice"),
            "estimatedSettlePrice": mark_price.get("estimatedSettlePrice"),
            "lastFundingRate": mark_price.get("lastFundingRate"),
            "nextFundingTime": mark_price.get("nextFundingTime"),
            "time": mark_price.get("time"),
        }
    )
    return 0


def handle_place_order(
    args: argparse.Namespace,
    client: "BinanceFuturesClient",
    order_service: "OrderService",
) -> int:
    from bot.orders import OrderRequest
    from bot.validators import validate_order_inputs

    del client
    validated = validate_order_inputs(
        symbol=args.symbol,
        side=args.side,
        order_type=args.order_type,
        quantity=args.quantity,
        price=args.price,
        time_in_force=args.time_in_force,
        reduce_only=args.reduce_only,
        position_side=args.position_side,
        client_order_id=args.client_order_id,
    )
    order_request = OrderRequest(**validated)
    response = order_service.submit_order(order_request)
    print_json(response)
    return 0


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def build_error_payload(error: Exception) -> dict[str, Any]:
    if hasattr(error, "to_dict") and callable(error.to_dict):
        return error.to_dict()
    return {
        "errorType": error.__class__.__name__,
        "message": str(error),
    }


def emit_error(error: Exception) -> None:
    payload = {
        "status": "error",
        "success": False,
    }
    payload.update(build_error_payload(error))
    print_json(payload)


def log_error(logger: Any, error: Exception) -> None:
    extra: dict[str, Any] = {}
    correlation_id = getattr(error, "correlation_id", None)
    if correlation_id:
        extra["correlation_id"] = correlation_id
    logger.error("%s", error, extra=extra)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.test_connection and args.command:
        parser.error("--test-connection cannot be combined with another command.")
    if not args.test_connection and not args.command:
        parser.error("a command is required unless --test-connection is provided")

    from bot.exceptions import TradingBotError
    from bot.logging_config import configure_logging, get_logger
    from bot.orders import OrderService

    client = None

    try:
        configure_logging(
            level=args.log_level,
            log_file=os.getenv("TRADING_BOT_LOG_FILE") or None,
        )
        logger = get_logger("cli")
        logger.debug("Using Binance Futures Testnet base URL: %s", TESTNET_BASE_URL)
        if not DOTENV_AVAILABLE and ENV_PATH.exists():
            logger.warning(
                "python-dotenv is not installed, so .env was not loaded. Install dependencies with 'pip install -r requirements.txt'."
            )

        client = create_client_from_env()
        order_service = OrderService(client)
        handler = handle_test_connection if args.test_connection else args.handler
        return handler(args, client, order_service)
    except TradingBotError as exc:
        logger = get_logger("cli")
        log_error(logger, exc)
        emit_error(exc)
        return 1
    except KeyboardInterrupt:
        logger = get_logger("cli")
        logger.warning("Execution interrupted by user.")
        emit_error(RuntimeError("Execution interrupted by user."))
        return 130
    except Exception as exc:
        logger = get_logger("cli")
        logger.exception("Unhandled exception while running the trading bot.")
        emit_error(RuntimeError(f"Unexpected error: {exc.__class__.__name__}"))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
