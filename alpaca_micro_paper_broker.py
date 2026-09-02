from __future__ import annotations

import os
from typing import Any, Mapping

from alpaca_live_broker import AlpacaLiveBroker, TradingClient
from alpaca_paper_broker import ALPACA_PAPER_ENDPOINT, _is_true, _normalize_url


PAPER_MICRO_CONFIRMATION_PHRASE = "ENABLE_PAPER_MICRO_TRIAL"


class AlpacaMicroPaperBroker(AlpacaLiveBroker):
    """Paper endpoint using the same whole-share bracket path as micro-live."""

    def __init__(
        self,
        trading_client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        *,
        read_only: bool = False,
    ):
        env = dict(os.environ if environ is None else environ)
        if str(env.get("TRADING_MODE", "")).strip().upper() != "PAPER":
            raise RuntimeError("micro paper broker requires TRADING_MODE=PAPER")
        if not read_only:
            if not _is_true(env.get("PAPER_MICRO_TRIAL_ENABLED", "false")):
                raise RuntimeError("PAPER_MICRO_TRIAL_ENABLED must be true")
            if not _is_true(env.get("ALPACA_ORDER_SUBMISSION_ENABLED", "false")):
                raise RuntimeError("ALPACA_ORDER_SUBMISSION_ENABLED must be true")
            if str(env.get("PAPER_MICRO_TRIAL_CONFIRMATION", "")).strip() != PAPER_MICRO_CONFIRMATION_PHRASE:
                raise RuntimeError("PAPER_MICRO_TRIAL_CONFIRMATION is missing or invalid")

        self.mode = "PAPER"
        self.backend = "ALPACA_PAPER_MICRO"
        self.api_key = str(env.get("ALPACA_API_KEY", "")).strip()
        self.api_secret = str(env.get("ALPACA_API_SECRET", "")).strip()
        self.base_url = str(env.get("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)).strip() or ALPACA_PAPER_ENDPOINT
        self.order_submission_enabled = not read_only
        self.allow_short_selling = False
        if _normalize_url(self.base_url) != _normalize_url(ALPACA_PAPER_ENDPOINT):
            raise RuntimeError("ALPACA_PAPER_BASE_URL must be https://paper-api.alpaca.markets")
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Missing ALPACA_API_KEY or ALPACA_API_SECRET")
        if TradingClient is None and trading_client is None:
            raise RuntimeError("alpaca-py is required for Alpaca paper trading")
        self._trading_client = trading_client or TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=True,
            url_override=self.base_url,
        )
        self._validate_account_ready()

    def get_account(self) -> dict[str, Any]:
        account = super().get_account()
        account.pop("live_endpoint_confirmed", None)
        account.update({"paper_endpoint_confirmed": True, "broker_backend": self.backend})
        return account
