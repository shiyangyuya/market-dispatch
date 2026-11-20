"""CLI entry point for Market Dispatch (init-db / run / render / archive / status / vacuum)."""

from __future__ import annotations

import argparse
import sys

from src.archive import archive_old_daily_files
from src.config import load_config
from src.db import create_database
from src.pipeline import run_pipeline
from src.render import SiteRenderer
from src.time_utils import recent_day_strings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market-dispatch",
        description="Market Dispatch — Bloomberg / Reuters / WSJ 公开财经元数据聚合器",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="配置文件路径（默认依次查找 config/config.yaml 与 config/config.example.yaml）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="初始化数据库表、索引与 FTS5 触发器")

    run_p = sub.add_parser("run", help="执行完整抓取、分类、去重、翻译、要闻研判、归档与渲染")
    run_p.add_argument("--no-translate", action="store_true", help="跳过翻译步骤（调试用）")
    run_p.add_argument("--no-meta", action="store_true", help="跳过 Reuters og:description 抓取")

    sub.add_parser("render", help="不抓新数据，重新渲染近 3 天日报、根 index.html 与归档索引")
    sub.add_parser("archive", help="执行超期归档并刷新归档索引")
    sub.add_parser("status", help="查看数据库统计与最近运行批次状态")
    sub.add_parser("vacuum", help="压缩 SQLite 数据库体积")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    if args.command == "init-db":
        db = create_database(cfg)
        try:
            db.init_db()
            print(f"[OK] Database initialized ({cfg.storage.engine}): {cfg.storage.sqlite_path}")
        finally:
            db.close()
        return 0

    if args.command == "run":
        batch = run_pipeline(
            cfg,
            enable_meta=not args.no_meta,
            enable_translate=not args.no_translate,
        )
        print(
            f"[OK] Run complete in {batch.duration_sec}s | "
            f"new={batch.items_new} dup={batch.items_dup} "
            f"tx_ok={batch.translate_ok} tx_fail={batch.translate_fail} "
            f"render_ok={batch.render_ok}"
        )
        return 0 if batch.render_ok else 1

    if args.command == "render":
        db = create_database(cfg)
        try:
            db.init_db()
            renderer = SiteRenderer(cfg, db)
            info = renderer.render_all()
            print(f"[OK] Rendered {len(info['rendered_files'])} HTML files (today={info['today']})")
        finally:
            db.close()
        return 0

    if args.command == "archive":
        db = create_database(cfg)
        try:
            db.init_db()
            moved = archive_old_daily_files(
                daily_dir=cfg.paths.daily,
                archive_dir=cfg.paths.archive,
                retention_days=cfg.retention_days,
                tz_name=cfg.timezone,
            )
            renderer = SiteRenderer(cfg, db)
            renderer.render_all(archived_days_to_refresh=moved)
            print(f"[OK] Archived {len(moved)} day(s): {moved}")
        finally:
            db.close()
        return 0

    if args.command == "status":
        db = create_database(cfg)
        try:
            db.init_db()
            total = db.get_total_article_count()
            recent_days = recent_day_strings(days=cfg.retention_days, tz_name=cfg.timezone)
            print(f"Market Dispatch Status | engine={cfg.storage.engine} | total_articles={total}")
            print("Recent days:")
            for d in recent_days:
                st = db.get_day_stats(d)
                print(
                    f"  {d}: total={st.total} star={st.important} "
                    f"(markets={st.by_category['markets']}, tech={st.by_category['tech']}, "
                    f"world={st.by_category['world']}, us={st.by_category['us']}, china={st.by_category['china']})"
                )
            batches = db.get_recent_batches(limit=5)
            print("Recent batches:")
            for b in batches:
                print(
                    f"  #{b.id} {b.ran_at} | {b.duration_sec}s | new={b.items_new} dup={b.items_dup} "
                    f"tx={b.translate_ok}/{b.translate_ok + b.translate_fail} render={b.render_ok}"
                )
        finally:
            db.close()
        return 0

    if args.command == "vacuum":
        db = create_database(cfg)
        try:
            db.vacuum()
            print("[OK] Database compacted (VACUUM)")
        finally:
            db.close()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
