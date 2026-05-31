"""Small security and path-boundary helpers shared by the Flask backend."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Iterable


def truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_loopback_addr(remote_addr: str | None) -> bool:
    remote = str(remote_addr or "").strip()
    if not remote:
        return False
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return remote.lower() == "localhost"


def path_is_under(path_obj: str | Path, root_obj: str | Path) -> bool:
    path = Path(path_obj).resolve()
    root = Path(root_obj).resolve()
    return path == root or root in path.parents


def resolve_allowed_path_roots(env_name: str, defaults: Iterable[str | Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    raw = str(os.getenv(env_name, "")).strip()
    items = raw.split(os.pathsep) if raw else []
    for item in [*items, *list(defaults or [])]:
        if item in (None, ""):
            continue
        try:
            resolved = Path(item).expanduser().resolve()
        except Exception:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)
