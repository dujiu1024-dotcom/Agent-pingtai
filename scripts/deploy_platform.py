#!/usr/bin/env python3
"""Repeatable bootstrap for the existing platform infrastructure Compose stack.

This deliberately deploys only the PostgreSQL, Redis and MinIO services defined
in deploy/compose.yaml. The repository does not currently provide a production
application image/composition entrypoint.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = ROOT / "deploy"
ENV_EXAMPLE = DEPLOY_DIR / ".env.example"
ENV_FILE = DEPLOY_DIR / ".env"
COMPOSE_FILE = DEPLOY_DIR / "compose.yaml"
REQUIRED = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
)
PLACEHOLDERS = ("REPLACE_WITH_", "CHANGEME", "TODO", "TBD", "<")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ValueError(f"{path}:{line_number}: invalid environment variable name")
        values[key] = value
    return values


def initialize_env(*, source: Path = ENV_EXAMPLE, target: Path = ENV_FILE) -> bool:
    """Create a private .env with cryptographically generated passwords.

    Returns False without modifying an existing file.
    """
    if target.exists():
        return False
    values = parse_env(source)
    for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "MINIO_ROOT_PASSWORD"):
        if key in values:
            values[key] = secrets.token_urlsafe(36)
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    return True


def validate_env(path: Path = ENV_FILE) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"missing {path}; run with --init-only to create it")
    values = parse_env(path)
    missing = [key for key in REQUIRED if not values.get(key, "").strip()]
    if missing:
        raise ValueError("missing required settings: " + ", ".join(missing))
    for key in REQUIRED:
        value = values[key]
        if any(marker in value.upper() for marker in PLACEHOLDERS):
            raise ValueError(f"{key} still contains a placeholder")
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            path.chmod(0o600)
    return values


def compose_command(*args: str) -> list[str]:
    return [
        "docker", "compose",
        "--project-directory", str(ROOT),
        "--env-file", str(ENV_FILE),
        "-f", str(COMPOSE_FILE),
        *args,
    ]


def deploy(
    *,
    check_only: bool = False,
    no_pull: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    validate_env()
    if not COMPOSE_FILE.is_file():
        raise ValueError(f"missing Compose definition: {COMPOSE_FILE}")
    runner(compose_command("config", "--quiet"), cwd=ROOT, check=True, text=True)
    if check_only:
        return
    if not no_pull:
        runner(compose_command("pull"), cwd=ROOT, check=True, text=True)
    runner(
        compose_command("up", "-d", "--wait", "--remove-orphans"),
        cwd=ROOT,
        check=True,
        text=True,
    )
    runner(compose_command("ps"), cwd=ROOT, check=True, text=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-only", action="store_true", help="create deploy/.env with random passwords, then stop")
    parser.add_argument("--check", action="store_true", help="validate configuration and Compose without changing services")
    parser.add_argument("--no-pull", action="store_true", help="skip image pulls")
    args = parser.parse_args(argv)
    try:
        if not ENV_FILE.exists():
            created = initialize_env()
            if created:
                print(f"Created {ENV_FILE} with generated passwords (mode 0600). Review it before deployment.")
                if args.init_only:
                    return 0
            else:
                raise ValueError(f"could not initialize {ENV_FILE}")
        elif args.init_only:
            print(f"{ENV_FILE} already exists; leaving it unchanged.")
            return 0
        deploy(check_only=args.check, no_pull=args.no_pull)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"deployment failed: {exc}", file=sys.stderr)
        return 1
    print("Infrastructure deployment completed; verify service health with docker compose ps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
