"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from loguru import logger

from . import __version__
from .config import ConfigManager
from .db.database import Database
from .paths import AppPaths
from .services.backup import BackupService


def _load(data_dir: str | None) -> tuple[AppPaths, ConfigManager]:
    paths = AppPaths.resolve(data_dir)
    paths.ensure()
    config = ConfigManager(paths.config_file)
    config.load()
    return paths, config


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    paths, config = _load(getattr(args, "data_dir", None))
    app = create_app(paths=paths, config=config)
    host = getattr(args, "host", None) or config.host
    port = getattr(args, "port", None) or config.port
    logger.info("Starting MoriMemory on http://{}:{}", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=config.log_level.lower(),
        access_log=False,
    )
    return 0


async def _init(data_dir: str | None) -> int:
    paths, config = _load(data_dir)
    database = Database(paths.database_path(config.database_file))
    await database.initialize()
    version = await database.schema_version()
    print(f"Database: {database.path}")
    print(f"Schema version: {version}")
    print(f"FTS5: {'enabled' if database.fts_enabled else 'unavailable'}")
    print("Service token: run `morimemory token` to print it.")
    await database.dispose()
    return 0


async def _backup(data_dir: str | None) -> int:
    paths, config = _load(data_dir)
    database = Database(paths.database_path(config.database_file))
    await database.initialize()
    try:
        service = BackupService(database, config, paths)
        path = await service.create()
        print(path)
        return 0
    finally:
        await database.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="morimemory",
        description="MoriMemory local memory engine",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the HTTP service")
    serve.add_argument("--data-dir", default=None)
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    init = subparsers.add_parser("init", help="Create or migrate the database")
    init.add_argument("--data-dir", default=None)

    token = subparsers.add_parser("token", help="Print the service token")
    token.add_argument("--data-dir", default=None)

    backup = subparsers.add_parser("backup", help="Create a ZIP backup")
    backup.add_argument("--data-dir", default=None)

    migrate = subparsers.add_parser("migrate", help="Apply pending schema migrations")
    migrate.add_argument("--data-dir", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        return _serve(args)
    if args.command == "init":
        return asyncio.run(_init(args.data_dir))
    if args.command == "migrate":
        return asyncio.run(_init(args.data_dir))
    if args.command == "token":
        _paths, config = _load(args.data_dir)
        print(config.token)
        return 0
    if args.command == "backup":
        return asyncio.run(_backup(args.data_dir))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
