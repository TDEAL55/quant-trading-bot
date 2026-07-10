from pathlib import Path

import pytest

import run_overnight_backtest


def test_atomic_report_write_keeps_previous_content_on_failure(monkeypatch, tmp_path):
    report_path = tmp_path / "OVERNIGHT_BACKTEST_REPORT.md"
    report_path.write_text("original report\n", encoding="utf-8")

    original_replace = Path.replace

    def failing_replace(self, target):
        if self.suffix == ".tmp":
            raise RuntimeError("simulated interruption")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        run_overnight_backtest._write_report_atomic(report_path, "new report\n")

    assert report_path.read_text(encoding="utf-8") == "original report\n"
    assert not (tmp_path / "OVERNIGHT_BACKTEST_REPORT.md.tmp").exists()
