"""Minimal .env loader (KEY=VALUE per line, # comments).

cPanel cron jobs don't receive the environment variables set in "Setup Python App",
so both the website and send_reminders.py read settings from the same .env file.
Real environment variables always win over the file.
"""
import os
from pathlib import Path


def load_env(path) -> None:
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)
