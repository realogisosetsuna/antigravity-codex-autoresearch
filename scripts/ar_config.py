#!/usr/bin/env python3
"""Shared path & cluster configuration loader for Auto Research Hub."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

HUB_ROOT = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
USER_HOME = Path.home().resolve()


def load_cluster_config() -> tuple[dict[str, Any], bool]:
    """Return `(config_dict, is_custom_local)`."""
    candidates = [
        Path(os.environ["AUTO_RESEARCH_CLUSTER_CONFIG"]) if os.environ.get("AUTO_RESEARCH_CLUSTER_CONFIG") else None,
        HUB_ROOT / "config" / "cluster.local.json",
        USER_HOME / ".config" / "auto-research" / "cluster.json",
    ]
    for c in candidates:
        if c and c.is_file():
            try:
                return json.loads(c.read_text(encoding="utf-8")), True
            except Exception:
                pass

    example = HUB_ROOT / "config" / "cluster.example.json"
    base: dict[str, Any] = {}
    if example.is_file():
        try:
            base = json.loads(example.read_text(encoding="utf-8"))
        except Exception:
            base = {}

    # Default single-node self-discovery when no `cluster.local.json` is configured yet
    host = socket.gethostname().split(".")[0].strip() or "local"
    base["nodes"] = {
        host: {
            "ip": "127.0.0.1",
            "user": os.environ.get("USER") or USER_HOME.name,
            "home": str(USER_HOME),
            "disks": ["/mnt/data", "/mnt/data1", "/mnt/data2", "/mnt/data3", "/mnt/data4", "/mnt/nas2", str(USER_HOME)],
        }
    }
    return base, False
