import os
from pathlib import Path

import report_checker


def test_check_latest_report_prints_latest_summary_fields(tmp_path, capsys):
    summary_dir = tmp_path / "daily_summaries"
    summary_dir.mkdir()

    first = summary_dir / "2026-07-07.md"
    first.write_text(
        "\n".join(
            [
                "# Daily Summary 2026-07-07",
                "- date: 2026-07-07",
                "- order submitted or skipped: skipped",
                "- reason: market closed",
                "- errors if any: ",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    second = summary_dir / "2026-07-08.md"
    second.write_text(
        "\n".join(
            [
                "# Daily Summary 2026-07-08",
                "- date: 2026-07-08",
                "- order submitted or skipped: submitted",
                "- reason: approved",
                "- errors if any: REVIEW_REQUIRED",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(first, (1, 1))
    os.utime(second, (2, 2))

    report_checker.check_latest_report(summary_dir=summary_dir)
    output = capsys.readouterr().out.splitlines()

    assert output == [
        "latest report date: 2026-07-08",
        "did bot run?: yes",
        "did it submit an order?: yes",
        "submitted=True",
        "order_id: N/A",
        "stop_reason: approved",
        "review_required: True",
    ]


def test_check_latest_report_handles_empty_directory(tmp_path, capsys):
    summary_dir = tmp_path / "daily_summaries"
    summary_dir.mkdir()

    result = report_checker.check_latest_report(summary_dir=summary_dir)
    output = capsys.readouterr().out.strip()

    assert result["found"] is False
    assert output == f"No daily summaries found in {Path(summary_dir)}."