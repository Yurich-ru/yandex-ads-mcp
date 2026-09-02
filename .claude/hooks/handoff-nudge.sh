#!/usr/bin/env python3
"""
Stop-хук Claude Code: один раз за сессию напоминает зафиксировать состояние,
когда контекст переваливает порог.

ПО УМОЛЧАНИЮ НЕ ПОДКЛЮЧЁН. Как включить — agent_docs/setup-checklist.md.

Порог по контексту, а не по размеру транскрипта: размер .jsonl не пропорционален
токенам (разброс 17-165 байт на токен), потому что в файле лежит весь диалог
с сырыми выводами инструментов, а в контексте — только пережившее сжатия.
Точный размер контекста — в блоке usage последнего ответа модели.

Порог: CLAUDE_HANDOFF_NUDGE_TOKENS, по умолчанию 150000.
Хук никогда не блокирует: любая ошибка — тихий выход с кодом 0.
"""
import json
import os
import sys

THRESHOLD = int(os.environ.get("CLAUDE_HANDOFF_NUDGE_TOKENS") or 150_000)
TAIL_BYTES = 4 * 1024 * 1024  # хвост по байтам, а не по строкам: строки бывают огромными

USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def last_context_tokens(path):
    """Размер контекста из последнего блока usage, или None если определить не удалось."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()  # отбросить обрезанную посередине строку
            chunk = fh.read()
    except OSError:
        return None

    for line in reversed(chunk.splitlines()):
        if b'"usage"' not in line:
            continue
        try:
            usage = (json.loads(line).get("message") or {}).get("usage") or {}
        except Exception:
            continue
        total = sum(int(usage.get(k) or 0) for k in USAGE_KEYS)
        if total > 0:
            return total
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    transcript = data.get("transcript_path")
    session = data.get("session_id")
    if not transcript or not session or not os.path.isfile(transcript):
        return
    if data.get("agent_id"):  # сабагенты не считаем — у них свой транскрипт
        return

    marker = os.path.join("/tmp", "claude-handoff-nudged-%s" % session)
    if os.path.exists(marker):
        return

    ctx = last_context_tokens(transcript)
    if ctx is None or ctx <= THRESHOLD:  # None = не смогли определить, это не ноль
        return

    try:
        open(marker, "w").close()
    except OSError:
        return

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": (
                    "Контекст этой сессии — около %dk токенов, порог напоминания пройден. "
                    "Уместно предложить пользователю выполнить /handoff (зафиксировать "
                    "состояние в agent_docs/snapshot.md и смежных файлах) и продолжить "
                    "в новой сессии. Напоминание приходит один раз за сессию — не повторять."
                ) % (ctx // 1000),
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
