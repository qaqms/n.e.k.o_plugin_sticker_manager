"""出可导入的 `.neko-plugin` 包（v0.16.0 起有这个脚本）。

**为什么需要它**：0.13.0~0.15.0 四个包都是手抄 zip 出来的，抄漏过一次关键字段——
`payload/profiles/default.toml` 里 `[plugin.sticker_manager]` 被写成
`enabled = true` 后紧跟 `enabled = false`（TOML 取后者），于是每次重装宿主都把插件
钉成"只注册不启动"，主人实机每次都得手动点启动。打包这一步一旦靠手工，就会把这种
错复制四轮。现在把选材规则、包内三份 toml 与载荷哈希算法都钉在这一个文件里。

用法（在插件仓根）：

    .venv/Scripts/python.exe tools/build_package.py            # 出到 ../dist/
    .venv/Scripts/python.exe tools/build_package.py --out /tmp

出包前必须刷新入口清单 `plugin.meta.json`（改过入口/工具/定时器就要重来一遍）。
它只能由宿主官方 probe 生成——那份要算源码指纹、用的也是宿主那个 Python：

    # 1) 把插件树摆进宿主期望的挂载位
    cp -r <本仓>/* N.E.K.O/plugin/plugins/sticker_manager/
    # 2) 用宿主 venv 跑 probe，结果写回挂载位
    cd N.E.K.O && ./.venv/Scripts/python.exe -c \\
      "from pathlib import Path; import json; \\
       from plugin.neko_plugin_cli.core.metadata_probe import derive_plugin_metadata; \\
       d=Path('plugin/plugins/sticker_manager').resolve(); \\
       (d/'plugin.meta.json').write_text(json.dumps(derive_plugin_metadata(d), ensure_ascii=False, indent=2)+chr(10), encoding='utf-8')"
    # 3) 拷回本仓，然后删掉第 1 步的暂存目录（别把它留在宿主仓里）

载荷哈希与宿主/官方 CLI 同算法：NFC 规范化后的 posix 相对路径（去掉 `payload/` 前缀）
按大小写敏感排序，每条贡献 `path + NUL + 内容 + NUL`。改了算法宿主会判包损坏。
"""

from __future__ import annotations

import argparse
import hashlib
import unicodedata
import zipfile
from pathlib import Path
from tomllib import loads as toml_loads

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "sticker_manager"
# 载荷目录前缀：宿主安装时按 `payload/plugins/<id>/` 解包。
PLUGIN_PREFIX = f"payload/plugins/{PACKAGE_ID}"

# 进包的规则是白名单，不是黑名单——新加的调试脚本/测试/文档不该跟着上路。
INCLUDE_DIRS = ("core", "services", "i18n")
INCLUDE_SUFFIXES = (".py", ".json", ".toml", ".ts", ".tsx")
UI_ENTRIES = (
    "ui/panel.tsx",
    "ui/shared.ts",
    "ui/preview.ts",
    "ui/library_model.ts",
    "ui/components",
)
ROOT_FILES = (
    "__init__.py",
    "plugin.toml",
    "plugin.meta.json",
    "pyproject.toml",
    "config.example.toml",
    "README.md",
)
OFFICIAL_PACK = "official/official_pack.zip"
_EXCLUDE_NAMES = {"__pycache__", ".ruff_cache", ".pytest_cache"}


def collect_plugin_files() -> list[Path]:
    """要进包的插件源文件（相对仓根的 posix 路径），排序稳定以便复算哈希。"""
    picked: list[Path] = []

    def keep(path: Path) -> bool:
        parts = set(path.relative_to(ROOT).parts)
        return not (parts & _EXCLUDE_NAMES)

    def add_dir(directory: Path) -> None:
        if not directory.is_dir():
            return
        for child in sorted(directory.rglob("*")):
            if child.is_file() and child.suffix in INCLUDE_SUFFIXES and keep(child):
                picked.append(child)

    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            picked.append(path)
    for name in INCLUDE_DIRS:
        add_dir(ROOT / name)
    for name in UI_ENTRIES:
        target = ROOT / name
        if target.is_dir():
            add_dir(target)
        elif target.is_file():
            picked.append(target)
    official = ROOT / OFFICIAL_PACK
    if official.is_file():
        picked.append(official)
    return sorted(set(picked), key=lambda p: p.relative_to(ROOT).as_posix())


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def manifest_toml() -> str:
    """包清单：名字/版本/描述一律取自 plugin.toml，杜绝第二处真值。"""
    meta = toml_loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["plugin"]
    return "\n".join(
        [
            'schema_version = "1.0"',
            'package_type = "plugin"',
            "",
            f'id = "{_escape(str(meta["id"]))}"',
            f'package_name = "{_escape(str(meta["name"]))}"',
            f'version = "{_escape(str(meta["version"]))}"',
            f'package_description = "{_escape(str(meta.get("description", "")))}"',
            "",
        ]
    )


def dependencies_toml() -> str:
    return "\n".join(
        [
            'schema_version = "1.0"',
            "",
            f"[plugins.{PACKAGE_ID}]",
            "python_requirements = []",
            "host_python_requirements = []",
            "plugin_dependencies = []",
            "advanced_plugin_dependencies = []",
            f'vendor_path = "plugins/{PACKAGE_ID}/vendor"',
            "vendor_present = false",
            "",
        ]
    )


def _inline(table: dict) -> str:
    parts: list[str] = []
    for key, value in table.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, str):
            text = f'"{_escape(value)}"'
        else:
            text = repr(value)
        parts.append(f"{key} = {text}")
    return "{ " + ", ".join(parts) + " }"


def profile_toml() -> str:
    """装机默认档：业务段的数字全部取自 plugin.toml（三处同源的第三源在这里也成立）。

    `[plugin.sticker_manager]` 只写一次 `enabled`/`auto_start`——重复键取后者，
    那正是 0.13.0~0.15.0 把插件钉成"只注册不启动"的地方。
    """
    section = toml_loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["sticker_manager"]
    lines = [
        'name = "default"',
        f'enabled_plugins = ["{PACKAGE_ID}"]',
        "",
        f"[plugin.{PACKAGE_ID}]",
        "enabled = true",
        "auto_start = true",
    ]
    for key in ("send", "storage", "awareness"):
        table = section.get(key)
        if isinstance(table, dict) and table:
            lines.append(f"{key} = {_inline(table)}")
    lines.append("")
    return "\n".join(lines)


def payload_hash(entries: list[tuple[str, bytes]]) -> str:
    """`entries` 的键必须是**去掉 payload/ 前缀**的 posix 相对路径。"""
    digest = hashlib.sha256()
    ordered = sorted(entries, key=lambda item: unicodedata.normalize("NFC", item[0]))
    for relative, content in ordered:
        digest.update(unicodedata.normalize("NFC", relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def metadata_toml(hash_value: str) -> str:
    return "\n".join(
        [
            "[payload]",
            'hash_algorithm = "sha256"',
            f'hash = "{hash_value}"',
            "",
            "[source]",
            'kind = "local"',
            f'paths = ["{PACKAGE_ID}"]',
            "",
        ]
    )


def assert_meta_fresh(files: list[Path]) -> None:
    """入口清单必须比它描述的代码新。

    `plugin.meta.json` 是宿主官方 probe 导入插件后生成的（注册了哪些入口、参数形状、
    定时器），宿主刷新注册表时**只读这份文件不再导入插件**
    （`plugin/server/application/plugins/registry_service.py:438`）。
    带着旧 meta 出包 = 包会宣传一个装上去根本不会注册的入口（本轮新增的 `turns`
    定时器正是这种后果）。生成方式见模块 docstring。
    """
    meta = ROOT / "plugin.meta.json"
    if not meta.is_file():
        raise SystemExit(
            "[FAIL] 缺 plugin.meta.json——先按模块 docstring 里的命令用宿主 venv 生成，"
            "不要拿旧包里的凑数。"
        )
    newest = max((path.stat().st_mtime for path in files if path.suffix in {".py", ".ts", ".tsx"}), default=0.0)
    if meta.stat().st_mtime < newest:
        raise SystemExit(
            "[FAIL] plugin.meta.json 比插件代码旧——改过入口/定时器就先重新生成再出包。"
        )


def build(out_dir: Path) -> Path:
    """装配 zip。归档名与哈希用的"相对路径"是两回事，这里分开算清楚：

    - 归档名：`payload/plugins/<id>/<仓内相对路径>`、`payload/dependencies.toml`、
      `payload/profiles/default.toml`；
    - 哈希键：**去掉 `payload/` 前缀**后的路径（所以插件文件带 `plugins/<id>/` 一段）。
      少算这一截，宿主安装时会判包损坏——手抄那四轮就是这么差点栽的。
    """
    plugin_files = collect_plugin_files()
    assert_meta_fresh(plugin_files)
    plugin_entries = [
        (f"plugins/{PACKAGE_ID}/{path.relative_to(ROOT).as_posix()}", path.read_bytes())
        for path in plugin_files
    ]
    generated = [
        ("dependencies.toml", dependencies_toml().encode("utf-8")),
        ("profiles/default.toml", profile_toml().encode("utf-8")),
    ]
    hash_entries = plugin_entries + generated
    version = toml_loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))["plugin"]["version"]
    target = out_dir / f"{PACKAGE_ID}_v{version}.neko-plugin"
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, content in hash_entries:
            archive.writestr(f"payload/{relative}", content)
        archive.writestr("manifest.toml", manifest_toml())
        archive.writestr("metadata.toml", metadata_toml(payload_hash(hash_entries)))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--out", default=str(ROOT.parent / "dist"), help="输出目录（默认 ../dist）")
    args = parser.parse_args()
    target = build(Path(args.out))
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        raw = sum(info.file_size for info in archive.infolist())
    print(f"[OK] {target}")
    print(f"     entries={len(names)} raw_bytes={raw / 1024 / 1024:.1f} MiB")
    print(f"     sha256={hashlib.sha256(target.read_bytes()).hexdigest()[:16]}")
    required = (
        f"payload/plugins/{PACKAGE_ID}/services/turns.py",
        f"payload/plugins/{PACKAGE_ID}/core/eagerness.py",
        f"payload/{'profiles/default.toml'}",
    )
    missing = [want for want in required if want not in names]
    if missing:  # 新文件没进包=白干；宁可红也不出"装上没变化"的包
        print(f"[FAIL] 包内缺: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
