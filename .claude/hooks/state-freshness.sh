#!/usr/bin/env python3
"""
SessionStart-хук Claude Code: сверяет возраст agent_docs/snapshot.md с последним
коммитом в код и, если состояние устарело, говорит об этом агенту одной строкой.

Зачем: правило «обновляй snapshot при завершении задачи» держится на памяти агента.
Этот хук превращает его нарушение в видимое последствие — агент, увидев строку,
не примет протухший snapshot за актуальное состояние.

ПО УМОЛЧАНИЮ НЕ ПОДКЛЮЧЁН. Как включить — agent_docs/setup-checklist.md.

Что считается «кодом»: любые коммиты, кроме agent_docs/, *.md и каталогов конфигурации агентов.
Порог устаревания: STARTER_SNAPSHOT_STALE_DAYS, по умолчанию 7.
Хук только читает; любая ошибка — тихий выход с кодом 0.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

STALE_DAYS = int(os.environ.get("STARTER_SNAPSHOT_STALE_DAYS") or 7)
SNAPSHOT = os.path.join("agent_docs", "snapshot.md")


def git_date(cwd, *pathspec):
    """Дата последнего коммита по pathspec (YYYY-MM-DD) или None."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", *pathspec],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return None
    return out or None


PLACEHOLDER = "never"


def declared_date(path):
    """Дата из «**Последнее обновление:** YYYY-MM-DD».
    PLACEHOLDER — если там до сих пор заготовка шаблона; None — если строки нет вовсе."""
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None
    m = re.search(r"\*\*Последнее обновление:\*\*\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    if re.search(r"\*\*Последнее обновление:\*\*\s*<!--", text):
        return PLACEHOLDER
    return None


def emit(message):
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message}},
        sys.stdout, ensure_ascii=False,
    )


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    snap = os.path.join(cwd, SNAPSHOT)
    if not os.path.isfile(snap):
        return  # не проект из стартера — молчим

    code_date = git_date(
        cwd, ".", ":(exclude)agent_docs", ":(exclude)*.md",
        ":(exclude).claude", ":(exclude).agents", ":(exclude).codex",
        ":(exclude).cursor", ":(exclude).github",
    )
    if not code_date:
        return  # коммитов в код ещё нет — нечего сверять

    snap_date = declared_date(snap)
    if snap_date is None:  # строки с датой нет — судим по git
        snap_date = git_date(cwd, SNAPSHOT)
    if not snap_date or snap_date == PLACEHOLDER:
        emit(
            "agent_docs/snapshot.md ни разу не обновлялся (плейсхолдер даты), а в коде уже есть "
            "коммиты (последний %s). Состояние проекта не зафиксировано — не полагаться на snapshot, "
            "сверить с git log и при завершении задачи выполнить /handoff." % code_date
        )
        return

    try:
        gap = (dt.date.fromisoformat(code_date) - dt.date.fromisoformat(snap_date)).days
    except ValueError:
        return
    if gap >= STALE_DAYS:
        emit(
            "agent_docs/snapshot.md обновлён %s, а последний коммит в код — %s (разрыв %d дн.). "
            "Состояние, вероятно, устарело: не полагаться на snapshot, сверить с git log, "
            "при завершении задачи выполнить /handoff." % (snap_date, code_date, gap)
        )


try:
    main()
except Exception:
    pass
sys.exit(0)
