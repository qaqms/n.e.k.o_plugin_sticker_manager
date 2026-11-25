"""面板模块排版门：每个 ui 文件的 export 都要写在第一段 JSX 之前。

2026-10 的 Steam 版宿主拼接面板模块时，会把 JSX 闭合标签（`</span>`、`/>`）里的
“/”当成正则字面量的开头，之后的花括号计数全乱，排在 JSX 后面的顶层 `export`
就不会被剥掉。浏览器随即报 `Unexpected token 'export'`，整个面板一片空白
（0.9.7 新概览页的 RecentThumb 写在 OverviewPage 前面，实机踩过）。
新宿主已修好，但插件要兼容还没更新的宿主，所以辅助组件一律放在导出之后。
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"
EXPORT = re.compile(r"^\s*export\b")
JSX_CLOSE = re.compile(r"</|/>")


def test_exports_come_before_any_jsx() -> None:
    offenders = []
    for path in sorted(UI.rglob("*.ts*")):
        lines = path.read_text(encoding="utf-8").splitlines()
        code = [(i, line) for i, line in enumerate(lines, 1) if not line.lstrip().startswith("//")]
        exports = [i for i, line in code if EXPORT.match(line)]
        jsx = [i for i, line in code if JSX_CLOSE.search(line)]
        if exports and jsx and jsx[0] < exports[-1]:
            offenders.append(f"{path.relative_to(UI.parent)}: JSX 第 {jsx[0]} 行，export 第 {exports[-1]} 行")
    assert offenders == [], offenders
