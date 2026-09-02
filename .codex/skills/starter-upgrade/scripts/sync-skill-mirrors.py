#!/usr/bin/env python3
"""
Пересоздать зеркала скиллов из `.agents/skills/<name>` в `.claude`, `.codex`, `.cursor`.

    python3 sync-skill-mirrors.py [--project DIR] [--check] [--quiet]

Источник истины — `.agents/skills/<name>/`. Зеркала — **обычные копии каталогов,
не symlink**: Portainer клонирует git-стеки через go-git с `NoSymlinkFS`, и любой
symlink в репозитории рвёт клон («repository contains a symlink, which is not allowed
for security reasons»), после чего в `/data/compose/<id>` остаётся `.git` и все
следующие вебхуки падают с «repository already exists».

Плата за копии — расхождение зеркал с источником; этот скрипт её и снимает.
Запускать после любой правки скилла (и стартерного, и проектного).

`--check` ничего не меняет: печатает расхождения и возвращает 1, если зеркало
отсутствует, является symlink или отличается по составу/содержимому файлов.
"""
import argparse
import os
import shutil
import sys

MIRROR_ROOTS = (".claude", ".codex", ".cursor")
SOURCE_ROOT = ".agents/skills"
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = (".pyc", ".pyo")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def skill_names(src_root):
    if not os.path.isdir(src_root):
        return []
    return sorted(
        n for n in os.listdir(src_root)
        if not n.startswith(".") and os.path.isdir(os.path.join(src_root, n))
    )


def walk_files(root):
    """Относительный путь → абсолютный, для всех файлов каталога."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(d, f)
            out[os.path.relpath(full, root)] = full
    return out


def same_file(a, b):
    if os.path.islink(a) or os.path.islink(b):
        return False
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def compare(src, dst):
    """Список расхождений зеркала с источником; пустой — зеркало актуально."""
    if os.path.islink(dst):
        return ["symlink → %s (Portainer не задеплоит репозиторий с symlink)" % os.readlink(dst)]
    if not os.path.exists(dst):
        return ["нет каталога"]
    if not os.path.isdir(dst):
        return ["не каталог"]

    s_files, d_files = walk_files(src), walk_files(dst)
    problems = []
    for rel in sorted(set(s_files) - set(d_files)):
        problems.append("нет файла %s" % rel)
    for rel in sorted(set(d_files) - set(s_files)):
        problems.append("лишний файл %s" % rel)
    for rel in sorted(set(s_files) & set(d_files)):
        if os.path.islink(d_files[rel]):
            problems.append("symlink-файл %s" % rel)
        elif not same_file(s_files[rel], d_files[rel]):
            problems.append("отличается %s" % rel)
    return problems


def replace(src, dst):
    if os.path.islink(dst) or os.path.isfile(dst):
        os.unlink(dst)
    elif os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst, symlinks=False, ignore=IGNORE)


def main():
    ap = argparse.ArgumentParser(description="Синхронизировать зеркала скиллов копиями каталогов.")
    ap.add_argument("--project", default=os.getcwd(), help="корень проекта (по умолчанию текущий каталог)")
    ap.add_argument("--check", action="store_true", help="только проверить, ничего не менять; код 1 при расхождении")
    ap.add_argument("--quiet", action="store_true", help="печатать только расхождения и ошибки")
    a = ap.parse_args()

    project = os.path.abspath(a.project)
    src_root = os.path.join(project, SOURCE_ROOT)
    names = skill_names(src_root)
    if not names:
        if not a.quiet:
            print("скиллов в %s нет — нечего синхронизировать" % SOURCE_ROOT)
        return 0

    stale = 0
    synced = 0
    for name in names:
        src = os.path.join(src_root, name)
        for root in MIRROR_ROOTS:
            rel = os.path.join(root, "skills", name)
            dst = os.path.join(project, rel)
            problems = compare(src, dst)
            if not problems:
                continue
            if a.check:
                stale += 1
                print("%s — %s" % (rel, "; ".join(problems)))
            else:
                replace(src, dst)
                synced += 1
                if not a.quiet:
                    print("обновлено: %s (%s)" % (rel, "; ".join(problems)))

    if a.check:
        if stale:
            print("\nзеркал разошлось: %d — запустить без --check" % stale, file=sys.stderr)
            return 1
        if not a.quiet:
            print("зеркала скиллов совпадают с %s (%d шт.)" % (SOURCE_ROOT, len(names)))
        return 0

    if not a.quiet:
        print("готово: скиллов %d, обновлено зеркал %d" % (len(names), synced))
    return 0


if __name__ == "__main__":
    sys.exit(main())
