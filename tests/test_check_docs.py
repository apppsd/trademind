"""scripts/check_docs.py 的固定案例（TASK-20261013-18）。

要求：对坏链接、重复ID、状态不一致和依赖循环有固定案例；
对历史相对链接/锚点、叙述行状态词、代码围栏内容不误报；
并保证真实仓库当前零错误（收敛不回退）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_docs  # noqa: E402


def errors(findings):
    return [f for f in findings if f[0] == "ERROR"]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_broken_local_link_reported(tmp_path):
    write(tmp_path / "docs" / "research" / "r.md", "见 [任务](../tasks/MISSING-TASK.md)。")
    errs = errors(check_docs.check(tmp_path))
    assert any("本地链接目标不存在" in f[3] and "MISSING-TASK" in f[3] for f in errs)


def test_historical_anchor_suffix_not_false_positive(tmp_path):
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-99.md",
          "---\nid: TASK-20261013-99\nstatus: completed\n---\n正文")
    write(tmp_path / "docs" / "research" / "r.md",
          "历史 [链接](../tasks/TASK-20261013-99.md#某个历史锚点) 不应误报。")
    assert errors(check_docs.check(tmp_path)) == []


def test_duplicate_id_reported(tmp_path):
    for name in ("ISSUE-20261013-01.md", "ISSUE-20261013-02.md"):
        write(tmp_path / "docs" / "issues" / name,
              "---\nid: ISSUE-20261013-01\nstatus: open\n---\n正文")
    errs = errors(check_docs.check(tmp_path))
    assert any("id 重复" in f[3] for f in errs)


def test_index_status_mismatch_reported(tmp_path):
    write(tmp_path / "docs" / "issues" / "ISSUE-20261013-01.md",
          "---\nid: ISSUE-20261013-01\nstatus: resolved\n---\n正文")
    write(tmp_path / "docs" / "issues" / "index.md",
          "# Issues\n\n| [01](ISSUE-20261013-01.md) | 问题 | open | 说明 |\n")
    errs = errors(check_docs.check(tmp_path))
    assert any("索引状态 open" in f[3] and "ISSUE-20261013-01" in f[3] for f in errs)


def test_index_status_match_and_narrative_not_flagged(tmp_path):
    write(tmp_path / "docs" / "issues" / "ISSUE-20261013-01.md",
          "---\nid: ISSUE-20261013-01\nstatus: resolved\n---\n正文")
    write(tmp_path / "docs" / "issues" / "index.md",
          "# Issues\n\n"
          "| [01](ISSUE-20261013-01.md) | 问题 | resolved（2026-10-14 证据） | 说明 |\n\n"
          "2026-10-13 [ISSUE-20261013-01](ISSUE-20261013-01.md) open：历史叙述行不构成索引状态。\n")
    assert errors(check_docs.check(tmp_path)) == []


def test_code_fence_links_skipped(tmp_path):
    write(tmp_path / "docs" / "research" / "r.md",
          "正文\n\n```\n[假链接](../tasks/围栏内不校验.md)\n```\n")
    assert errors(check_docs.check(tmp_path)) == []


def test_file_scheme_link_resolved(tmp_path):
    target = tmp_path / "docs" / "tasks" / "TASK-20261013-99.md"
    write(target, "---\nid: TASK-20261013-99\nstatus: completed\n---\n正文")
    link = "file:///" + str(target).replace("\\", "/")
    write(tmp_path / "docs" / "research" / "r.md", f"见 [任务]({link})。")
    assert errors(check_docs.check(tmp_path)) == []


def test_missing_depends_on_target_reported(tmp_path):
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-01.md",
          "---\nid: TASK-20261013-01\nstatus: pending\ndepends_on: [TASK-20261013-404]\n---\n正文")
    errs = errors(check_docs.check(tmp_path))
    assert any("depends_on 目标不存在：TASK-20261013-404" in f[3] for f in errs)


def test_dependency_cycle_reported(tmp_path):
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-01.md",
          "---\nid: TASK-20261013-01\nstatus: pending\ndepends_on: [TASK-20261013-02]\n---\n正文")
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-02.md",
          "---\nid: TASK-20261013-02\nstatus: pending\ndepends_on: [TASK-20261013-01]\n---\n正文")
    errs = errors(check_docs.check(tmp_path))
    assert any("depends_on 循环" in f[3] for f in errs)


# -- ISSUE-20261013-10 reopen: YAML 块列表是合法写法，不得静默跳过 --------------

def test_block_list_missing_dependency_reported(tmp_path):
    """复核反例：块列表 depends_on 指向不存在的目标必须报错。"""
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-01.md",
          "---\nid: TASK-20261013-01\nstatus: pending\ndepends_on:\n"
          "  - TASK-20990101-99\n---\n正文")
    errs = errors(check_docs.check(tmp_path))
    assert any("depends_on 目标不存在：TASK-20990101-99" in f[3] for f in errs)


def test_block_list_cycle_reported(tmp_path):
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-01.md",
          "---\nid: TASK-20261013-01\nstatus: pending\ndepends_on:\n"
          "  - TASK-20261013-02\n---\n正文")
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-02.md",
          "---\nid: TASK-20261013-02\nstatus: pending\ndepends_on:\n"
          "  - TASK-20261013-01\n---\n正文")
    errs = errors(check_docs.check(tmp_path))
    assert any("depends_on 循环" in f[3] for f in errs)


def test_block_list_and_quoted_refs_resolve(tmp_path):
    """合法块列表（含引号项）与内联列表一样解析；目标存在时不报错。"""
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-01.md",
          "---\nid: TASK-20261013-01\nstatus: pending\ndepends_on:\n"
          "  - 'TASK-20261013-02'\nrelated_issues:\n"
          "  - \"ISSUE-20261013-01\"\n---\n正文")
    write(tmp_path / "docs" / "tasks" / "TASK-20261013-02.md",
          "---\nid: TASK-20261013-02\nstatus: pending\n---\n正文")
    write(tmp_path / "docs" / "issues" / "ISSUE-20261013-01.md",
          "---\nid: ISSUE-20261013-01\nstatus: open\n---\n正文")
    assert errors(check_docs.check(tmp_path)) == []


def test_parse_frontmatter_block_and_inline_lists():
    fm = check_docs.parse_frontmatter(
        "---\nid: TASK-x\nstatus: in_progress\ndepends_on:\n"
        "  - TASK-a\n\n  - 'TASK-b'\nrelated_architecture: [ARCH-001, 'ARCH-002']\n---\n正文")
    assert fm["depends_on"] == ["TASK-a", "'TASK-b'"]
    assert check_docs.split_refs(fm["depends_on"]) == ["TASK-a", "TASK-b"]
    assert check_docs.split_refs(fm["related_architecture"]) == ["ARCH-001", "ARCH-002"]


def test_real_repo_has_zero_errors():
    findings = check_docs.check(ROOT)
    assert errors(findings) == []


# -- 2026-10-29 治理审阅补充：斜杠状态格式与索引重复行 ---------------------------

def test_index_slash_status_format_recognized(tmp_path):
    """「completed / Owner」是索引常用格式，必须参与 frontmatter 一致性校验。"""
    write(tmp_path / "docs" / "tasks" / "TASK-20261029-01.md",
          "---\nid: TASK-20261029-01\nstatus: completed\n---\n正文")
    write(tmp_path / "docs" / "tasks" / "index.md",
          "| [TASK-20261029-01](TASK-20261029-01.md) | in_progress / Claude Code | 内容 |\n")
    errs = errors(check_docs.check(tmp_path))
    assert any("in_progress" in f[3] and "TASK-20261029-01" in f[3] for f in errs)


def test_index_duplicate_task_rows_warned(tmp_path):
    """同一 Task 在索引多个表格行 → WARN（历史小节收敛提示）。"""
    write(tmp_path / "docs" / "tasks" / "TASK-20261029-01.md",
          "---\nid: TASK-20261029-01\nstatus: completed\n---\n正文")
    write(tmp_path / "docs" / "tasks" / "index.md",
          "| [A](TASK-20261029-01.md) | completed / Claude Code | 行一 |\n"
          "\n正文段落引用 [A](TASK-20261029-01.md) 不算行。\n"
          "| [B](TASK-20261029-01.md) | completed / Claude Code | 行二 |\n")
    warns = [f for f in check_docs.check(tmp_path) if f[0] == "WARN"]
    assert any("出现 2 行" in f[3] and "TASK-20261029-01" in f[3] for f in warns)


def test_index_prose_reference_not_counted_as_row(tmp_path):
    """正文段落重复引用同一 Task 不是表格行，不应告警。"""
    write(tmp_path / "docs" / "tasks" / "TASK-20261029-01.md",
          "---\nid: TASK-20261029-01\nstatus: completed\n---\n正文")
    write(tmp_path / "docs" / "tasks" / "index.md",
          "| [A](TASK-20261029-01.md) | completed / Claude Code | 表格行 |\n"
          "\n正文一 [A](TASK-20261029-01.md)。\n正文二 [A](TASK-20261029-01.md)。\n")
    warns = [f for f in check_docs.check(tmp_path) if f[0] == "WARN"]
    assert not any("出现" in f[3] for f in warns)
