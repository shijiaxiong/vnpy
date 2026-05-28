#!/usr/bin/env python3
"""
连板 × 题材热力图 —— 统一命令行入口
==========================================

用法:
  python scripts/main.py collect --start 20260501          # 采集涨停数据
  python scripts/main.py concept                           # 刷新题材缓存
  python scripts/main.py mood --start 20260520             # 计算情绪指标
  python scripts/main.py render --start 20260520           # 生成热力图
  python scripts/main.py all --start 20260501              # 全流程: 采集 → 情绪 → 热力图
  python scripts/main.py pipeline --date 20260527          # 单日流水线

依赖: pip install -r requirements_mood.txt
"""

import os
import sys
import argparse
import subprocess
import importlib.util
from pathlib import Path

# ===== 自动发现项目根目录 =====
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

# ===== 依赖检查 =====
REQUIRED = {
    "akshare": "akshare>=1.12.0",
    "pandas": "pandas>=2.0.0",
}


def check_deps():
    """检查必要依赖是否安装"""
    missing = []
    for mod_name, pkg in REQUIRED.items():
        if importlib.util.find_spec(mod_name) is None:
            missing.append(pkg)
    if missing:
        print(f"❌ 缺少依赖: {', '.join(missing)}")
        print(f"   pip install -r {PROJECT_DIR / 'requirements_mood.txt'}")
        sys.exit(1)
    print("✅ 依赖检查通过")


def run_script(name, args):
    """运行脚本并传递参数"""
    script = SCRIPT_DIR / f"{name}.py"
    cmd = [sys.executable, str(script)] + args
    return subprocess.run(cmd, cwd=str(PROJECT_DIR))


def cmd_collect(args):
    check_deps()
    extra = []
    if args.start:
        extra.extend(["--start", args.start])
    if args.end:
        extra.extend(["--end", args.end])
    if args.mode:
        extra.extend(["--mode", args.mode])
    return run_script("zt_collector", extra)


def cmd_concept(args):
    check_deps()
    extra = []
    if args.force:
        extra.append("--force")
    if args.quick:
        extra.append("--quick")
    return run_script("concept_cache", extra)


def cmd_mood(args):
    check_deps()
    extra = []
    if args.start:
        extra.extend(["--start", args.start])
    if args.end:
        extra.extend(["--end", args.end])
    return run_script("mood_engine", extra)


def cmd_render(args):
    check_deps()
    extra = []
    if args.start:
        extra.extend(["--start", args.start])
    if args.end:
        extra.extend(["--end", args.end])
    if args.days:
        extra.extend(["--days", str(args.days)])
    if args.output:
        extra.extend(["--output", args.output])
    return run_script("heatmap_render", extra)


def cmd_all(args):
    """全流程: collect → mood → render"""
    check_deps()
    date_args = []
    if args.start:
        date_args.extend(["--start", args.start])
    if args.end:
        date_args.extend(["--end", args.end])

    print("\n[1/3] 采集涨停数据...")
    r = run_script("zt_collector", date_args + ["--mode", "both"])
    if r.returncode != 0:
        print("❌ 采集失败，终止")
        sys.exit(1)

    print("\n[2/3] 计算情绪指标...")
    r = run_script("mood_engine", date_args)
    if r.returncode != 0:
        print("❌ 计算失败，终止")
        sys.exit(1)

    print("\n[3/3] 生成热力图...")
    r = run_script("heatmap_render", date_args)
    if r.returncode != 0:
        print("❌ 渲染失败")
        sys.exit(1)

    print("\n✅ 全流程完成")
    dashboard = PROJECT_DIR / "output" / "mood_dashboard.html"
    if dashboard.exists():
        print(f"📊 仪表盘: file://{dashboard}")


def cmd_pipeline(args):
    """单日流水线: 采集当日 → 更新情绪 → 重新渲染"""
    check_deps()
    date_str = args.date
    print(f"\n📅 执行单日流水线: {date_str}")

    print("[1/3] 采集涨停数据...")
    r = run_script("zt_collector", ["--start", date_str, "--end", date_str, "--mode", "both"])
    if r.returncode != 0:
        print("⚠️  采集可能已中断")

    print("\n[2/3] 更新情绪指标...")
    r = run_script("mood_engine", ["--start", date_str, "--end", date_str])
    if r.returncode != 0:
        print("⚠️  计算可能已中断")

    print("\n[3/3] 渲染最新30天热力图...")
    r = run_script("heatmap_render", ["--days", "30"])
    if r.returncode != 0:
        print("❌ 渲染失败")
        sys.exit(1)

    print(f"\n✅ 流水线完成")
    dashboard = PROJECT_DIR / "output" / "mood_dashboard.html"
    if dashboard.exists():
        print(f"📊 仪表盘: file://{dashboard}")


def main():
    parser = argparse.ArgumentParser(
        description="A股连板 × 题材热力图 数据管线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/main.py collect --start 20260501     # 采集涨停数据
  python scripts/main.py concept                      # 刷新题材缓存
  python scripts/main.py mood --start 20260520        # 计算情绪指标
  python scripts/main.py render --start 20260520      # 生成热力图
  python scripts/main.py all --start 20260501         # 全流程
  python scripts/main.py pipeline --date 20260527     # 单日流水线
""",
    )

    sub = parser.add_subparsers(dest="command", help="子命令")

    # collect
    p_collect = sub.add_parser("collect", help="采集涨停数据")
    p_collect.add_argument("--start", help="起始日期 YYYYMMDD")
    p_collect.add_argument("--end", help="结束日期 YYYYMMDD")
    p_collect.add_argument("--mode", choices=["csv", "db", "both"], default="both")

    # concept
    p_concept = sub.add_parser("concept", help="刷新题材缓存")
    p_concept.add_argument("--force", action="store_true", help="强制全量刷新")
    p_concept.add_argument("--quick", action="store_true", help="增量更新")

    # mood
    p_mood = sub.add_parser("mood", help="计算情绪指标")
    p_mood.add_argument("--start", help="起始日期 YYYYMMDD")
    p_mood.add_argument("--end", help="结束日期 YYYYMMDD")

    # render
    p_render = sub.add_parser("render", help="生成热力图")
    p_render.add_argument("--start", help="起始日期 YYYYMMDD")
    p_render.add_argument("--end", help="结束日期 YYYYMMDD")
    p_render.add_argument("--days", type=int, help="近N天")
    p_render.add_argument("--output", help="输出路径")

    # all
    p_all = sub.add_parser("all", help="全流程: 采集→情绪→热力图")
    p_all.add_argument("--start", help="起始日期 YYYYMMDD")
    p_all.add_argument("--end", help="结束日期 YYYYMMDD")

    # pipeline
    p_pipe = sub.add_parser("pipeline", help="单日流水线")
    p_pipe.add_argument("--date", required=True, help="日期 YYYYMMDD")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 切换到项目目录
    os.chdir(str(PROJECT_DIR))
    print(f"📂 项目目录: {PROJECT_DIR}")
    print(f"🐍 Python: {sys.executable}")

    commands = {
        "collect": lambda: cmd_collect(args),
        "concept": lambda: cmd_concept(args),
        "mood": lambda: cmd_mood(args),
        "render": lambda: cmd_render(args),
        "all": lambda: cmd_all(args),
        "pipeline": lambda: cmd_pipeline(args),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
