#!/usr/bin/env python3
"""
Сравнить проект с актуальным cmit-starter по границе из STARTER_MANIFEST.

    python3 starter-diff.py [--project DIR] [--starter DIR|URL] [--no-diff]

Ничего не применяет — только показывает. Применяет агент по /starter-upgrade
с оглядкой на категорию каждого файла (template / once / mixed / project).

Категории берутся из манифеста НОВОГО стартера: граница может меняться,
и решает всегда свежая версия.
"""
import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_URL = "https://github.com/cmit-ru/cmit-starter.git"
KINDS = ("template", "once", "mixed", "project")


def read_manifest(path):
    entries = []
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        kind, rel = (x.strip() for x in line.split(":", 1))
        if kind in KINDS and rel:
            entries.append((kind, rel))
    return entries


def expand(root, rel):
    """Путь с «/» на конце — все файлы каталога (относительно root)."""
    if rel.endswith("/"):
        base = os.path.join(root, rel)
        out = []
        for d, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                out.append(os.path.relpath(os.path.join(d, f), root))
        return sorted(out)
    return [rel]


def read(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


def is_symlink_target_same(a, b):
    return os.path.islink(a) and os.path.islink(b) and os.readlink(a) == os.readlink(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--starter", default=DEFAULT_URL, help="каталог или git-URL")
    ap.add_argument("--no-diff", action="store_true", help="только сводка, без diff")
    a = ap.parse_args()

    project = os.path.abspath(a.project)
    tmp = None
    if os.path.isdir(a.starter):
        starter = os.path.abspath(a.starter)
    else:
        tmp = tempfile.mkdtemp(prefix="cmit-starter-")
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", a.starter, tmp],
                           capture_output=True, text=True)
        if r.returncode:
            print("не удалось склонировать стартер:", r.stderr.strip(), file=sys.stderr)
            return 1
        starter = tmp

    try:
        manifest = os.path.join(starter, "STARTER_MANIFEST")
        if not os.path.isfile(manifest):
            print("в стартере нет STARTER_MANIFEST — версия старше механизма обновления", file=sys.stderr)
            return 1
        v_new = (read(os.path.join(starter, "STARTER_VERSION")) or "?").strip()
        v_old = (read(os.path.join(project, "STARTER_VERSION")) or "").strip() or "нет (до введения версий)"
        print(f"# Сверка со стартером\nпроект:  {project}\nверсия в проекте: {v_old}\nверсия стартера:  {v_new}\n")

        rows, diffs = [], []
        for kind, rel in read_manifest(manifest):
            for f in expand(starter, rel):
                sp, pp = os.path.join(starter, f), os.path.join(project, f)
                if kind == "project":
                    continue  # шаблон не трогает — даже не показываем
                if kind == "once":
                    rows.append((kind, f, "живёт в проекте" if os.path.exists(pp) else "удалён/не создан — норма"))
                    continue
                if os.path.islink(sp):
                    status = "OK" if is_symlink_target_same(sp, pp) else ("НОВЫЙ" if not os.path.lexists(pp) else "ИЗМЕНЁН (symlink)")
                    rows.append((kind, f, status))
                    continue
                s_txt, p_txt = read(sp), read(pp)
                if p_txt is None:
                    rows.append((kind, f, "НОВЫЙ"))
                    if not a.no_diff:
                        diffs.append((f, kind, s_txt, ""))
                elif s_txt == p_txt:
                    rows.append((kind, f, "OK"))
                else:
                    rows.append((kind, f, "ИЗМЕНЁН"))
                    if not a.no_diff:
                        diffs.append((f, kind, s_txt, p_txt))

        w = max(len(r[1]) for r in rows) if rows else 10
        print("## Сводка\n")
        print(f"{'категория':<9} {'файл':<{w}}  статус")
        for kind, f, st in rows:
            print(f"{kind:<9} {f:<{w}}  {st}")

        n_new = sum(1 for r in rows if r[2] == "НОВЫЙ")
        n_chg = sum(1 for r in rows if r[2].startswith("ИЗМЕНЁН"))
        n_mix = sum(1 for r in rows if r[0] == "mixed" and r[2] != "OK")
        print(f"\nновых: {n_new} · изменённых: {n_chg} · из них mixed (вручную, по разделам): {n_mix}")

        if diffs:
            print("\n## Diff (проект → стартер)\n")
            for f, kind, s_txt, p_txt in diffs:
                tag = "  ← MIXED: применять по разделам, не целиком" if kind == "mixed" else ""
                print(f"### {f}{tag}\n")
                if p_txt == "":
                    print(f"(новый файл, {len(s_txt.splitlines())} строк — показать: cat {os.path.join(starter, f)})\n")
                    continue
                for line in difflib.unified_diff(p_txt.splitlines(), s_txt.splitlines(),
                                                 fromfile=f"проект/{f}", tofile=f"стартер/{f}", lineterm="", n=2):
                    print(line)
                print()
        print(f"\nстартер распакован в: {starter}" + ("  (временный, удалится)" if tmp else ""))
        return 0
    finally:
        if tmp and "--keep" not in sys.argv:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
