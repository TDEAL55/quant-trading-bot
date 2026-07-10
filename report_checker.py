from pathlib import Path


SUMMARY_DIR = Path(__file__).resolve().parent / "daily_summaries"


def _latest_summary_file(summary_dir=SUMMARY_DIR):
    summary_dir = Path(summary_dir)
    if not summary_dir.exists():
        return None

    candidates = [path for path in summary_dir.iterdir() if path.is_file()]
    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


def _parse_summary(text):
    parsed = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            line = line[2:]
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed


def _truthy(value):
    return str(value).strip().lower() in {"true", "yes", "1", "submitted"}


def check_latest_report(summary_dir=SUMMARY_DIR, print_fn=print):
    latest_file = _latest_summary_file(summary_dir)
    if latest_file is None:
        message = f"No daily summaries found in {Path(summary_dir)}."
        print_fn(message)
        return {
            "found": False,
            "message": message,
        }

    parsed = _parse_summary(latest_file.read_text(encoding="utf-8"))
    latest_report_date = parsed.get("date", latest_file.stem)
    order_state = parsed.get("order submitted or skipped", "skipped")
    submitted = _truthy(order_state)
    review_field = str(parsed.get("review required", "")).strip()
    error_field = str(parsed.get("errors if any", "")).strip().upper()
    review_required = _truthy(review_field) or error_field == "REVIEW_REQUIRED"
    stop_reason = parsed.get("reason", "N/A")
    order_id = parsed.get("order_id", "N/A")

    lines = [
        f"latest report date: {latest_report_date}",
        f"did bot run?: yes",
        f"did it submit an order?: {'yes' if submitted else 'no'}",
        f"submitted={submitted}",
        f"order_id: {order_id}",
        f"stop_reason: {stop_reason}",
        f"review_required: {review_required}",
    ]

    for line in lines:
        print_fn(line)

    return {
        "found": True,
        "latest_file": str(latest_file),
        "latest_report_date": latest_report_date,
        "submitted": submitted,
        "order_id": order_id,
        "stop_reason": stop_reason,
        "review_required": review_required,
    }


def main():
    check_latest_report()


if __name__ == "__main__":
    main()