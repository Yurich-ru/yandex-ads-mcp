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


MIRROR_DIRS = (".claude", ".codex", ".cursor", ".agents")

# Признаки другого фреймворка или старого стартера: второе хранилище состояния.
FOREIGN_MARKERS = (
    (".claude/SNAPSHOT.md", "состояние фреймворка alexeykrol v5"),
    (".claude/BACKLOG.md", "план фреймворка alexeykrol v5"),
    (".claude/rules/", "правила фреймворка alexeykrol v5"),
    ("manifest.md", "манифест старого стартера"),
    ("docs/adr.md", "однофайловый ADR старого стартера"),
    ("docs/development-history.md", "однофайловая история старого стартера"),
    ("docs/guides/", "гайды старого стартера"),
)


SKIP_WALK_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}


def project_symlinks(project, limit=15):
    """Symlink среди файлов проекта: сначала спросить git, иначе обойти дерево."""
    try:
        r = subprocess.run(["git", "-C", project, "ls-files", "-s"],
                           capture_output=True, text=True)
    except OSError:
        r = None
    if r is not None and r.returncode == 0:
        found = sorted(ln.split("\t", 1)[1] for ln in r.stdout.splitlines()
                       if ln.startswith("120000 ") and "\t" in ln)
    else:
        found = []
        for d, dirs, files in os.walk(project):
            dirs[:] = [x for x in dirs if x not in SKIP_WALK_DIRS]
            for n in dirs + files:
                path = os.path.join(d, n)
                if os.path.islink(path):
                    found.append(os.path.relpath(path, project))
        found = sorted(set(found))
    return found[:limit], len(found)


def skill_tree(root):
    """Содержимое каталога скилла: относительный путь → байты."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(d, f)
            try:
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root)] = fh.read()
            except OSError:
                out[os.path.relpath(path, root)] = None
    return out


def stale_skill_mirrors(project):
    """Зеркала скиллов, которые остались symlink или разошлись с .agents/skills."""
    src_root = os.path.join(project, ".agents", "skills")
    if not os.path.isdir(src_root):
        return []
    bad = []
    for name in sorted(os.listdir(src_root)):
        src = os.path.join(src_root, name)
        if name.startswith(".") or not os.path.isdir(src):
            continue
        src_tree = None
        for root in (".claude", ".codex", ".cursor"):
            rel = "%s/skills/%s" % (root, name)
            dst = os.path.join(project, root, "skills", name)
            if os.path.islink(dst):
                bad.append("`%s` (symlink)" % rel)
                continue
            if not os.path.isdir(dst):
                bad.append("`%s` (нет)" % rel)
                continue
            if src_tree is None:
                src_tree = skill_tree(src)
            if skill_tree(dst) != src_tree:
                bad.append("`%s` (расходится)" % rel)
    return bad


def ignored_by_git(project, rel):
    """True/False — игнорирует ли git путь; None — git не ответил."""
    try:
        r = subprocess.run(["git", "-C", project, "check-ignore", "-q", rel],
                           capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None  # 128 — не репозиторий или git недоступен


def ignored_by_gitignore_text(project, d):
    """Запасной разбор .gitignore, когда git недоступен."""
    path = os.path.join(project, ".gitignore")
    if not os.path.isfile(path):
        return False
    dir_blocked = star_blocked = reincluded = False
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        pat = line.lstrip("!").strip().strip("/")
        if neg:
            if pat.startswith(d + "/"):
                reincluded = True
        elif pat in (d, d + "/**"):
            dir_blocked = True
        elif pat == d + "/*":
            star_blocked = True
    return dir_blocked or (star_blocked and not reincluded)


def collect_warnings(project, starter):
    """Грабли, которые всплыли на прогоне по существующим проектам."""
    out = []

    links, n_links = project_symlinks(project)
    if links:
        more = "" if n_links <= len(links) else " и ещё %d" % (n_links - len(links))
        out.append(
            "в репозитории есть symlink (%d): " % n_links + ", ".join("`%s`" % x for x in links) + more + "."
            "\n    Portainer клонирует git-стеки через go-git с NoSymlinkFS и такой репозиторий не клонирует:"
            "\n    деплой падает, в /data/compose/<id> остаётся .git и все следующие вебхуки тоже падают."
            "\n    Заменить копиями каталогов (зеркала скиллов — sync-skill-mirrors.py)."
        )

    stale = stale_skill_mirrors(project)
    if stale:
        out.append(
            "зеркала скиллов расходятся с `.agents/skills`: " + ", ".join(stale) + "."
            "\n    Зеркала — копии, а не symlink, поэтому их надо пересоздавать после правки скилла:"
            "\n    python3 .agents/skills/starter-upgrade/scripts/sync-skill-mirrors.py"
        )

    blocked = []
    for d in MIRROR_DIRS:
        by_git = ignored_by_git(project, d + "/skills/handoff")
        if by_git if by_git is not None else ignored_by_gitignore_text(project, d):
            blocked.append(d)
    if blocked:
        out.append(
            ".gitignore проекта игнорирует " + ", ".join("`%s/`" % d for d in blocked) +
            " — зеркала скиллов и хуки не попадут в коммит."
            "\n    Правило вида `.claude/` закрывает каталог целиком, вложенное им уже не переоткрыть:"
            "\n    заменить на `.claude/*` и добавить исключения `!.claude/skills/`, `!.claude/hooks/`."
            "\n    .gitignore — once, править самому нельзя: сказать пользователю."
        )

    try:
        names = os.listdir(project)
    except OSError:
        names = []
    if "claude.md" in names:
        out.append(
            "в корне есть `claude.md` строчными — дубль `CLAUDE.md`, на case-insensitive ФС конфликт."
            "\n    Содержимое — указатель: `git mv claude.md CLAUDE.md`; иначе сначала перенести в AGENTS.md."
        )

    foreign = []
    for rel, what in FOREIGN_MARKERS:
        path = os.path.join(project, rel.rstrip("/"))
        if os.path.isdir(path) if rel.endswith("/") else os.path.isfile(path):
            foreign.append("`%s` (%s)" % (rel, what))
    if foreign:
        out.append(
            "признаки другого фреймворка или старого стартера: " + ", ".join(foreign) + "."
            "\n    Второе хранилище состояния, слияние вручную: накатывать поверх, чужое не сливать"
            "\n    и не удалять; вопрос консолидации — в snapshot «Не проверено» и backlog."
        )

    tpl = read(os.path.join(starter, "CLAUDE.md"))
    prj = read(os.path.join(project, "CLAUDE.md"))
    if prj is not None and tpl is not None and prj.strip() != tpl.strip():
        out.append(
            "проектный `CLAUDE.md` не равен шаблонному (`@AGENTS.md`) — содержимое перенести"
            "\n    в AGENTS.md → «Правила проекта» до замены, построчно проверив, что ничего не потеряно."
        )
    return out


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

        warns = collect_warnings(project, starter)
        if warns:
            print("\n## Предупреждения\n")
            for w in warns:
                print("  - " + w)

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
