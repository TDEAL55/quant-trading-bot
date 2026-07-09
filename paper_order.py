import os
import re

from logger_setup import logger


class PaperOrderManager:
    """Safety-first paper order gate that defaults to dry-run only."""

    def __init__(self, mode=None, dry_run=None, submit_enabled=False, trading_client=None):
        self.mode = (mode or os.getenv("TRADING_MODE", "SIMULATION")).upper()
        self.dry_run = True if dry_run is None else bool(dry_run)
        self.submit_enabled = bool(submit_enabled)
        self.trading_client = trading_client
        self._seen_order_keys = set()

        if self.mode == "LIVE":
            raise RuntimeError("LIVE mode is blocked for paper_order; use PAPER mode only.")

    def _credentials(self):
        return {
            "api_key": os.getenv("ALPACA_API_KEY", ""),
            "api_secret": os.getenv("ALPACA_API_SECRET", ""),
        }

    def _missing_credentials(self):
        creds = self._credentials()
        missing = []
        if not creds["api_key"]:
            missing.append("ALPACA_API_KEY")
        if not creds["api_secret"]:
            missing.append("ALPACA_API_SECRET")
        return missing

    def _is_market_open(self):
        if self.trading_client is None:
            return False
        clock = self.trading_client.get_clock()
        return bool(getattr(clock, "is_open", False))

    def _reject(self, symbol, side, notional, reason, simulated_order=None):
        logger.info(
            "paper_order rejected symbol=%s side=%s notional=%s reason=%s",
            symbol,
            side,
            notional,
            reason,
        )
        return {
            "approved": False,
            "reason": reason,
            "dry_run": self.dry_run,
            "submitted": False,
            "simulated_order": simulated_order,
        }

    def _approve(self, symbol, side, notional, simulated_order):
        logger.info(
            "paper_order approved symbol=%s side=%s notional=%s dry_run=%s",
            symbol,
            side,
            notional,
            self.dry_run,
        )
        return {
            "approved": True,
            "reason": "approved",
            "dry_run": self.dry_run,
            "submitted": False,
            "simulated_order": simulated_order,
        }

    def _parse_buy_notional_command(self, command):
        pattern = r"^\s*(BUY|SELL)\s*\$\s*([0-9]*\.?[0-9]+)\s+OF\s+([A-Za-z\.\-]+)\s*$"
        matched = re.match(pattern, str(command), flags=re.IGNORECASE)
        if not matched:
            raise ValueError("command must be in the form: BUY $<notional> of <symbol>")

        side, notional_text, symbol = matched.groups()
        return {
            "side": side.upper(),
            "notional": float(notional_text),
            "symbol": symbol.upper(),
        }

    def place_order(
        self,
        command,
        symbol=None,
        side=None,
        notional=None,
        order_type="market",
        asset_class="equity",
        leverage=1,
    ):
        """Validate and gate a BUY-only notional paper order without real submission."""
        parsed_symbol = symbol
        parsed_side = side
        parsed_notional = notional

        if command is not None:
            try:
                parsed = self._parse_buy_notional_command(command)
                parsed_symbol = parsed["symbol"]
                parsed_side = parsed["side"]
                parsed_notional = parsed["notional"]
            except ValueError as exc:
                return self._reject(parsed_symbol, parsed_side, parsed_notional, str(exc))

        missing = self._missing_credentials()
        if missing:
            return self._reject(parsed_symbol, parsed_side, parsed_notional, f"missing credentials: {', '.join(missing)}")

        if self.mode != "PAPER":
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "paper_order only supports PAPER mode")

        normalized_side = str(parsed_side or "").upper()
        if normalized_side != "BUY":
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "only BUY notional orders are supported; SELL/shorting is blocked")

        if str(order_type).lower() != "market":
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "only market orders are supported")

        if str(asset_class).lower() != "equity":
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "options are not supported")

        if float(leverage) != 1.0:
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "margin, shorting, and leverage are not supported")

        try:
            order_notional = float(parsed_notional)
        except (TypeError, ValueError):
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "notional must be a number")
        if order_notional <= 0:
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "notional must be greater than 0")
        if order_notional > 25.0:
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "maximum notional value is $25")

        resolved_symbol = str(parsed_symbol or "").upper()
        if not resolved_symbol:
            return self._reject(parsed_symbol, parsed_side, parsed_notional, "symbol is required")

        order_key = (resolved_symbol, normalized_side, round(order_notional, 8), str(order_type).lower())
        if order_key in self._seen_order_keys:
            return self._reject(resolved_symbol, normalized_side, order_notional, "duplicate order rejected")

        if not self._is_market_open():
            return self._reject(resolved_symbol, normalized_side, order_notional, "market is closed")

        simulated_order = {
            "symbol": resolved_symbol,
            "notional": order_notional,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "extended_hours": False,
        }

        self._seen_order_keys.add(order_key)

        if self.dry_run:
            return self._approve(resolved_symbol, normalized_side, order_notional, simulated_order)

        if not self.submit_enabled:
            return self._reject(resolved_symbol, normalized_side, order_notional, "order submission disabled", simulated_order)

        # Real submission path intentionally remains unused by default.
        return self._reject(resolved_symbol, normalized_side, order_notional, "real order submission is not enabled", simulated_order)


def create_paper_order_manager(mode=None, dry_run=None, submit_enabled=False, trading_client=None):
    """Create a paper order manager with conservative safe defaults."""
    return PaperOrderManager(
        mode=mode,
        dry_run=dry_run,
        submit_enabled=submit_enabled,
        trading_client=trading_client,
    )