#!/usr/bin/env bash
set -euo pipefail

DATABASE_URL="${DATABASE_URL:-sqlite:////var/lib/quant-bot/quant-bot.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/quant-bot}"
KEEP_BACKUPS="${KEEP_BACKUPS:-14}"

python3 - "$DATABASE_URL" "$BACKUP_DIR" "$KEEP_BACKUPS" <<'PY'
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

database_url = sys.argv[1]
backup_dir = Path(sys.argv[2])
keep_backups = int(sys.argv[3])
db_path = Path(database_url.replace("sqlite:///", "", 1))

backup_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
backup_path = backup_dir / f"quant-bot-{timestamp}.db"

with sqlite3.connect(str(db_path)) as source, sqlite3.connect(str(backup_path)) as target:
    source.backup(target)

backups = sorted(backup_dir.glob("quant-bot-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
for old_backup in backups[keep_backups:]:
    old_backup.unlink(missing_ok=True)

print(backup_path)
PY
