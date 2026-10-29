"""轻量只读文档一致性校验（不修改任何文件）。

范围：AGENTS.md、README.md、docs/**/*.md。
检查项：
- 本地相对链接目标存在（剥离 #锚点，不校验锚点内容，历史锚点不误报；```围栏内跳过）
- docs/issues、docs/tasks、docs/architecture 的 frontmatter id 重复
- 索引表格行内的状态词与被链接 Issue/Task 正文 frontmatter status 不一致
  （Issue 只认 open/resolved，Task 只认 pending/in_progress/completed/cancelled；
  仅扫描表格行，叙述行中的历史状态词不误报；工具只报告，不自动改历史）
- Task frontmatter depends_on / related_* 目标存在；depends_on 无循环。
  支持内联 [a, b] 与 YAML 块列表（key: 后跟 - item 行）两种写法（ISSUE-20261013-10）
- 同一 Owner 名下多个 in_progress Task 提醒（warning，不影响退出码）

外部 http(s)/mailto/纯锚点链接跳过。退出码：存在 error 为 1，否则 0。
用法：python scripts/check_docs.py [--root 项目根目录]
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

STATUS_ISSUE = {"open", "resolved"}
STATUS_TASK = {"pending", "in_progress", "completed", "cancelled"}

LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)\s*\)")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

ID_DIRS = ("issues", "tasks", "architecture")


def parse_frontmatter(text: str) -> dict:
    """解析文件头部 --- 包围的 frontmatter：支持 ``key: value``、内联
    ``[a, b]`` 以及 YAML 块列表（``key:`` 后跟 ``- item`` 行）。块列表是
    治理标准模板的合法写法，不得静默丢弃（ISSUE-20261013-10）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict = {}
    i = 1
    while i < len(lines):
        s = lines[i].strip()
        if s == "---":
            break
        if not s.startswith(("-", "#")) and ":" in s:
            key, _, value = s.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                fm[key] = value
            else:
                # 块列表：收集后续 - item 行（允许中间空行），遇其他内容停止
                items: list = []
                j = i + 1
                while j < len(lines):
                    t = lines[j].strip()
                    if t == "---":
                        break
                    if t.startswith("- "):
                        items.append(t[2:].strip())
                        j += 1
                        continue
                    if not t:
                        j += 1
                        continue
                    break
                if items:
                    fm[key] = items
                    i = j
                    continue
        i += 1
    return fm


def split_refs(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        parts: list = []
        for item in value:
            parts.extend(split_refs(item))
        return parts
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    parts = re.split(r"[,，、]", v)
    return [p.strip().strip("'\"") for p in parts if p.strip()]


def cell_status_tokens(cell: str, allowed: set) -> list:
    """单元格中的状态词：整格等于状态词；状态词后接全/半角括号；
    或索引常用格式「状态词 / Owner」「状态词/Owner」（2026-10-29 补，治理审阅）。"""
    c = cell.strip()
    out = []
    for st in allowed:
        if c == st or c.startswith(st + "（") or c.startswith(st + "("):
            out.append(st)
            continue
        rest = c[len(st):] if c.startswith(st) else None
        if rest is not None:
            stripped = rest.lstrip()
            if stripped.startswith(("/", "／")) and len(stripped) > 1:
                out.append(st)
    return out


def strip_anchor(target: str) -> str:
    return target.split("#", 1)[0]


def check(root: Path) -> list:
    """返回 [(level, path显示, 行号, 消息)]；level 为 ERROR 或 WARN。"""
    findings: list = []

    def err(path, line, msg):
        findings.append(("ERROR", path, line, msg))

    def warn(path, line, msg):
        findings.append(("WARN", path, line, msg))

    files = [p for p in (root / "AGENTS.md", root / "README.md") if p.exists()]
    docs = root / "docs"
    if docs.exists():
        files.extend(sorted(docs.rglob("*.md")))

    texts: dict = {}
    for f in files:
        try:
            texts[f] = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            err(f.relative_to(root), 0, f"无法读取：{exc}")
            texts[f] = ""

    # 1) id 注册表（issues/tasks/architecture），重复 id 报错
    ids: dict = {}
    fm_by_file: dict = {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        parts = rel.split("/")
        if len(parts) == 3 and parts[0] == "docs" and parts[1] in ID_DIRS:
            if parts[2] == "index.md":
                continue  # 索引文件不要求 frontmatter id
            fm = parse_frontmatter(texts[f])
            fm_by_file[f] = fm
            doc_id = fm.get("id", "")
            if not doc_id:
                warn(rel, 1, "frontmatter 缺少 id")
                continue
            if doc_id in ids:
                err(rel, 1, f"id 重复：{doc_id}（已在 {ids[doc_id]} 登记）")
            else:
                ids[doc_id] = rel

    # 2) 逐文件扫描：坏链接、索引状态一致性
    tasks_index = docs / "tasks" / "index.md"
    issues_index = docs / "issues" / "index.md"
    linked_task_paths: set = set()
    task_index_rows: dict = {}  # resolved task path -> [行号…]；索引重复行检测（2026-10-29）
    linked_issue_paths: set = set()

    for f in files:
        rel = f.relative_to(root).as_posix()
        in_fence = False
        for lineno, line in enumerate(texts[f].splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in LINK_RE.finditer(line):
                target = m.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                if target.startswith("file:///"):
                    # 绝对 file 链接：百分号解码后校验目标存在（不作为索引引用统计）
                    fs_path = Path(urllib.parse.unquote(target[len("file:///"):]))
                    if not fs_path.exists():
                        err(rel, lineno, f"file链接目标不存在：{target}")
                    continue
                if SCHEME_RE.match(target):
                    continue
                local = strip_anchor(target)
                if not local:
                    continue
                resolved = (f.parent / local)
                if not resolved.exists():
                    err(rel, lineno, f"本地链接目标不存在：{target}")
                    continue
                rp = resolved.resolve()
                if f == tasks_index:
                    linked_task_paths.add(rp)
                    if stripped.startswith("|"):  # 只统计表格行；正文引用可重复
                        task_index_rows.setdefault(rp, []).append(lineno)
                elif f == issues_index:
                    linked_issue_paths.add(rp)

            # 索引表格行的状态一致性（只看表格行）
            if f in (tasks_index, issues_index) and stripped.startswith("|"):
                cells = [c for c in line.strip().strip("|").split("|")]
                row_tokens = {}
                for allowed, name in ((STATUS_ISSUE, "issues"), (STATUS_TASK, "tasks")):
                    toks = []
                    for cell in cells:
                        toks.extend(cell_status_tokens(cell, allowed))
                    if toks:
                        row_tokens[name] = toks
                if not row_tokens:
                    continue
                for m in LINK_RE.finditer(line):
                    target = strip_anchor(m.group(1))
                    if not target:
                        continue
                    resolved = (f.parent / target)
                    if not resolved.exists():
                        continue
                    sub = resolved.parent.name
                    fm = fm_by_file.get(resolved, {})
                    status = fm.get("status", "")
                    if sub == "issues" and "issues" in row_tokens:
                        for tok in row_tokens["issues"]:
                            if status and tok != status:
                                err(rel, lineno,
                                    f"索引状态 {tok} 与 {resolved.name} frontmatter status={status} 不一致")
                    elif sub == "tasks" and "tasks" in row_tokens:
                        for tok in row_tokens["tasks"]:
                            if status and tok != status:
                                err(rel, lineno,
                                    f"索引状态 {tok} 与 {resolved.name} frontmatter status={status} 不一致")

    # 3) Task/Issue 文件未被本索引引用（提醒，不判错）
    if docs.exists():
        for f in sorted((docs / "tasks").glob("TASK-*.md")):
            if f.resolve() not in linked_task_paths:
                warn(f.relative_to(root).as_posix(), 1, "未被 docs/tasks/index.md 引用")
        for f in sorted((docs / "issues").glob("ISSUE-*.md")):
            if f.resolve() not in linked_issue_paths:
                warn(f.relative_to(root).as_posix(), 1, "未被 docs/issues/index.md 引用")

    # 4) depends_on / related_* 目标存在；depends_on 循环
    deps: dict = {}
    for f, fm in fm_by_file.items():
        if not fm:
            continue
        rel = f.relative_to(root).as_posix()
        for key in ("depends_on", "related_issues", "related_tasks", "related_architecture"):
            for ref in split_refs(fm.get(key, "")):
                if not ref.startswith(("TASK-", "ISSUE-", "ARCH-")):
                    continue
                if ref not in ids:
                    err(rel, 1, f"{key} 目标不存在：{ref}")
        dep_list = [r for r in split_refs(fm.get("depends_on", "")) if r.startswith("TASK-")]
        doc_id = fm.get("id", "")
        if doc_id:
            deps[doc_id] = [r for r in dep_list if r in ids]

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in deps}

    def visit(node, stack):
        color[node] = GRAY
        for dep in deps.get(node, []):
            if color.get(dep, BLACK) == GRAY:
                err(ids.get(node, node), 1, f"depends_on 循环：{' -> '.join(stack + [node, dep])}")
            elif color.get(dep, BLACK) == WHITE:
                visit(dep, stack + [node])
        color[node] = BLACK

    for node in deps:
        if color[node] == WHITE:
            visit(node, [])

    # 5) Owner 重叠提醒
    owners: dict = {}
    for f, fm in fm_by_file.items():
        if f.parent.name == "tasks" and fm.get("status") == "in_progress":
            owner = fm.get("owner", "").strip() or "(空)"
            owners.setdefault(owner, []).append(fm.get("id", f.name))
    for owner, task_ids in sorted(owners.items()):
        if len(task_ids) > 1:
            warn("docs/tasks", 0, f"Owner「{owner}」同时持有 {len(task_ids)} 个 in_progress：{'、'.join(sorted(task_ids))}")

    # 6) 索引重复行提醒（2026-10-29 治理审阅）：同一 Task 在任务索引多个表格行出现，
    #    容易出现两处状态不一致（Task28-01 曾同时 in_progress/completed）。
    for rp, linenos in sorted(task_index_rows.items()):
        if len(linenos) > 1:
            warn("docs/tasks/index.md", linenos[0],
                 f"任务在索引出现 {len(linenos)} 行（{', '.join(map(str, linenos))}）："
                 f"只应保留一条当前记录，历史小节请移除状态行或改为正文引用：{rp.name}")

    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读文档一致性校验")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    findings = check(root)
    for level, path, line, msg in findings:
        loc = f"{path}:{line}" if line else path
        print(f"{level}  {loc}  {msg}")
    errors = sum(1 for f in findings if f[0] == "ERROR")
    warns = sum(1 for f in findings if f[0] == "WARN")
    print(f"检查完成：{errors} 个错误，{warns} 个提醒（范围：AGENTS.md、README.md、docs/**）")
    return 1 if errors else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
