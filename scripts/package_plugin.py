#!/usr/bin/env python
"""Package the plugin into an AstrBot-installable ZIP.

AstrBot 插件 ZIP 的规范：插件文件直接位于 ZIP 根目录（main.py、metadata.yaml、
_conf_schema.json、src/、pages/ 等），解压后放进 data/plugins/<plugin_name>/。

用法:
    python scripts/package_plugin.py            # 打包到 dist/
    python scripts/package_plugin.py -o out.zip # 指定输出路径

打包时自动排除:
    - 版本控制与 IDE：.git/ .github/ .idea/ .gitignore .pre-commit-config.yaml
    - 本地运行环境：venv/ __pycache__/ *.pyc *.pyo
    - 测试与调试文件：tests/ log.txt filterjson.txt *.log
    - 无用产物：dist/ .DS_Store Thumbs.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 顶层文件（显示名 -> 打包名一致，直接用相对路径）
TOP_LEVEL_FILES = [
    "main.py",
    "bootstrap.py",
    "metadata.yaml",
    "_conf_schema.json",
    "logo.png",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "requirements.txt",
    "requirements-dev.txt",
]

# 需要整体打包的目录
TOP_LEVEL_DIRS = [
    "assets",
    "docs",
    "pages",
    "scripts",
    "skills",
    "src",
]

# 需要排除的文件/目录名（任意层级命中即跳过）
EXCLUDE_NAMES = {
    ".git",
    ".github",
    ".idea",
    "venv",
    "__pycache__",
    "tests",
    "dist",
    "node_modules",
}

# 需要排除的文件后缀
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".log"}

# 需要排除的具体顶层文件
EXCLUDE_TOP_LEVEL = {
    ".gitignore",
    ".pre-commit-config.yaml",
    "CLAUDE.md",
    "AGENTS.md",
    "log.txt",
    "filterjson.txt",
}


def _should_skip(rel: Path) -> bool:
    if any(part in EXCLUDE_NAMES for part in rel.parts):
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def collect_files() -> list[Path]:
    """收集需要打包的（相对）文件列表。"""
    files: list[Path] = []
    for name in TOP_LEVEL_FILES:
        p = PLUGIN_ROOT / name
        if p.is_file():
            files.append(p.relative_to(PLUGIN_ROOT))
        else:
            print(f"  [跳过] 缺失顶层文件: {name}")

    for dirname in TOP_LEVEL_DIRS:
        root = PLUGIN_ROOT / dirname
        if not root.is_dir():
            print(f"  [跳过] 缺失目录: {dirname}")
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not _should_skip(p.relative_to(PLUGIN_ROOT)):
                files.append(p.relative_to(PLUGIN_ROOT))

    for name in sorted(EXCLUDE_TOP_LEVEL):
        p = PLUGIN_ROOT / name
        if p.exists():
            print(f"  [排除] {name}")

    # 去除可能的重复（防御）
    seen: set[str] = set()
    unique: list[Path] = []
    for f in sorted(files, key=lambda x: str(x)):
        key = str(f).replace("\\", "/")
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 AstrBot 插件 ZIP")
    parser.add_argument(
        "-o", "--output", type=str, default=None, help="输出 ZIP 路径"
    )
    args = parser.parse_args()

    # 读取插件名与版本，用于默认输出文件名
    meta = (PLUGIN_ROOT / "metadata.yaml").read_text(encoding="utf-8")
    plugin_name = "astrbot_plugin_rsshub"
    version = "unknown"
    for line in meta.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            plugin_name = line.split(":", 1)[1].strip().strip("'\"")
        elif line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip("'\"")

    output_dir = PLUGIN_ROOT / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_path = output_dir / f"{plugin_name}-{version}.zip"

    files = collect_files()
    if not files:
        print("错误: 没有找到任何需要打包的文件")
        return 1

    total_bytes = 0
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as zf:
        for rel in files:
            arc = str(rel).replace("\\", "/")
            zf.write(PLUGIN_ROOT / rel, arcname=arc)
            total_bytes += (PLUGIN_ROOT / rel).stat().st_size

    print("=" * 60)
    print("  打包完成")
    print("=" * 60)
    print(f"  插件名 : {plugin_name}")
    print(f"  版本   : {version}")
    print(f"  文件数 : {len(files)}")
    print(f"  大小   : {total_bytes / 1024:.1f} KB (未压缩) -> "
          f"{output_path.stat().st_size / 1024:.1f} KB (压缩)")
    print(f"  输出   : {output_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
