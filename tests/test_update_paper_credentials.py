from pathlib import Path

import pytest

from deployment.update_paper_credentials import update_credentials


def test_updates_only_paper_credentials_and_preserves_other_values(tmp_path):
    path = tmp_path / "quant-bot.env"
    path.write_text("TRADING_MODE=PAPER\nALPACA_API_KEY=old\nKEEP=value\nALPACA_API_SECRET=old\n", encoding="utf-8")
    update_credentials(path, "new-paper-key-123", "new-paper-secret-456")
    text = path.read_text(encoding="utf-8")
    assert "ALPACA_API_KEY=new-paper-key-123" in text
    assert "ALPACA_API_SECRET=new-paper-secret-456" in text
    assert "KEEP=value" in text
    assert "old" not in text


@pytest.mark.parametrize("key,secret", [("short", "long-enough-secret"), ("long-enough-key", "bad secret")])
def test_rejects_incomplete_or_whitespace_values(tmp_path, key, secret):
    path = tmp_path / "quant-bot.env"
    path.write_text("TRADING_MODE=PAPER\n", encoding="utf-8")
    with pytest.raises(ValueError):
        update_credentials(path, key, secret)
