"""Load config/config.yaml, with environment variables taking priority."""
from __future__ import annotations

import os
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_OVERRIDES = {
    "DATABASE_URL": ("database", "url"),
    "ARCHIVE_URL_TEMPLATE": ("sources", "archive_url_template"),
    "DOWNLOAD_DIR": ("paths", "download_dir"),
}


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = pathlib.Path(path or os.environ.get("ETL_CONFIG", ROOT / "config" / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[section][key] = os.environ[env]
    return cfg
