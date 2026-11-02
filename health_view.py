"""How a worker is described on the System Health page. Display only."""
from __future__ import annotations

_UNKNOWN_VERSION = {"", "unknown", "none", "n/a"}


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def worker_state(worker) -> tuple[str, str]:
    """(label, tone) for a worker. One that reports no CPU and no memory use and has no known version has not
    really reported: it is Stale, not Healthy."""
    metrics = _get(worker, "metrics") or {}
    cpu, mem = _get(metrics, "cpu_percent"), _get(metrics, "memory_percent")
    version = str(_get(worker, "git_sha") or "").strip().lower()
    if cpu in (0, 0.0) and mem in (0, 0.0) and version in _UNKNOWN_VERSION:
        return "Stale", "warning"
    status = str(_get(worker, "status") or "").upper()
    if status in ("IDLE", "BUSY"):
        return status.capitalize(), "ok"
    if status == "PRESSURE":
        return "Pressure", "warning"
    return (status.capitalize() or "Unknown"), "danger"
