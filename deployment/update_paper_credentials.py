from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path
import tempfile


def _replace_value(lines: list[str], name: str, value: str) -> list[str]:
    prefix = f"{name}="
    replacement = f"{prefix}{value}\n"
    output: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(replacement)
    return output


def update_credentials(path: Path, api_key: str, api_secret: str) -> None:
    api_key = str(api_key or "").strip()
    api_secret = str(api_secret or "").strip()
    if len(api_key) < 10 or any(character.isspace() for character in api_key):
        raise ValueError("API key appears incomplete")
    if len(api_secret) < 10 or any(character.isspace() for character in api_secret):
        raise ValueError("API secret appears incomplete")
    original_stat = path.stat()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = _replace_value(lines, "ALPACA_API_KEY", api_key)
    lines = _replace_value(lines, "ALPACA_API_SECRET", api_secret)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.writelines(lines)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, original_stat.st_mode)
        if hasattr(os, "chown"):
            os.chown(temporary_name, original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Securely replace Alpaca PAPER credentials")
    parser.add_argument("--file", default="/etc/quant-bot/quant-bot.env")
    args = parser.parse_args()
    path = Path(args.file)
    key = getpass("New $300 PAPER API key (hidden): ")
    secret = getpass("New $300 PAPER API secret (hidden): ")
    update_credentials(path, key, secret)
    print("Paper credentials saved; values were not displayed.")


if __name__ == "__main__":
    main()
