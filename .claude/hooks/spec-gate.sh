#!/usr/bin/env python3
"""
PreToolUse-хук Claude Code: одёргивает, когда код правят без согласованного ТЗ.

По `AGENTS.md`, раздел «1a. Дисциплина ТЗ», код пишется после `/spec`. Хук смотрит,
есть ли в `agent_docs/specs/` свежее ТЗ со статусом «согласовано» или «реализовано»,
и если нет — просит подтверждение у человека (`permissionDecision: ask`). Это
одёргивание, а не запрет: правки документов и конфигурации он пропускает молча.

ПО УМОЛЧАНИЮ НЕ ПОДКЛЮЧЁН. Как включить — agent_docs/setup-checklist.md.

Свежесть ТЗ: STARTER_SPEC_GATE_DAYS, по умолчанию 14 дней. Обход: STARTER_SPEC_GATE=off.
Хук никогда не блокирует работу: любая ошибка — тихий выход с кодом 0.
"""
import glob
import json
import os
import re
import sys
import time

TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
FRESH_DAYS = int(os.environ.get("STARTER_SPEC_GATE_DAYS") or 14)

# Документация и конфигурация — не код, их правка гейтом не закрывается.
DOC_DIRS = ("agent_docs/", ".agents/", ".claude/", ".codex/", ".cursor/", ".github/")
DOC_NAMES = {".gitignore", ".editorconfig", ".cursorignore"}
DOC_PREFIXES = (".env", "STARTER_", "docker-compose")
ROOT_CONFIG_SUFFIXES = (".json", ".yaml", ".yml", ".toml")

# Шапка ТЗ: «- **Статус:** согласовано». Строка шаблона со всеми статусами через «/»
# сюда не попадает — значение сравнивается целиком.
STATUS_RE = re.compile(r"^\s*[-*]\s*\*\*Статус:\*\*\s*(.+?)\s*$", re.M)
APPROVED = {"согласовано", "реализовано"}

REASON = (
    "Нет согласованного ТЗ в agent_docs/specs/. "
    "По AGENTS.md код пишется после /spec. Продолжить без ТЗ?"
)


def relative_path(cwd, path):
    """Путь относительно корня проекта в posix-виде; None — если файл вне проекта."""
    rel = os.path.relpath(os.path.join(cwd, os.path.expanduser(path)), cwd)
    if rel.startswith(os.pardir):
        return None
    return rel.replace(os.sep, "/")


def is_doc_path(rel):
    name = os.path.basename(rel)
    low = rel.lower()
    if low.endswith(".md"):  # AGENTS.md, CLAUDE.md, README.md и любые другие документы
        return True
    if rel.startswith(DOC_DIRS):
        return True
    if name in DOC_NAMES or name.startswith(DOC_PREFIXES):
        return True
    if "/" not in rel and name.lower().endswith(ROOT_CONFIG_SUFFIXES):
        return True
    return False


def has_approved_spec(specs_dir):
    """Есть ли в agent_docs/specs/ ТЗ со статусом «согласовано»/«реализовано» не старше порога."""
    cutoff = time.time() - FRESH_DAYS * 86400
    for path in glob.glob(os.path.join(specs_dir, "*.md")):
        if os.path.basename(path) == "README.md":
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read(64 * 1024)  # шапка в начале файла, читать целиком незачем
        except OSError:
            continue
        for match in STATUS_RE.finditer(text):
            if match.group(1).strip().strip(".").lower() in APPROVED:
                return True
    return False


def main():
    if (os.environ.get("STARTER_SPEC_GATE") or "").strip().lower() == "off":
        return

    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") not in TOOLS:
        return

    tool_input = data.get("tool_input") or {}
    path = (tool_input.get("file_path") or tool_input.get("notebook_path") or "").strip()
    if not path:
        return

    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    specs_dir = os.path.join(cwd, "agent_docs", "specs")
    if not os.path.isdir(specs_dir):
        return  # не проект из стартера или каталог убран — страховка молчит

    rel = relative_path(cwd, path)
    if rel is None or is_doc_path(rel):
        return
    if has_approved_spec(specs_dir):
        return

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": REASON,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )


try:
    main()
except Exception:
    pass
sys.exit(0)
