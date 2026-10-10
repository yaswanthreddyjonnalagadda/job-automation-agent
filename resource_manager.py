"""Resource manager for zero-cost remote worker and local offload.

Monitors system CPU, memory, disk, and browser processes to enforce safe
operational thresholds and prioritize live applications over background/test tasks.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class HealthCategory(str, Enum):
    HEALTHY = "HEALTHY"
    PRESSURE = "PRESSURE"
    HIGH_PRESSURE = "HIGH_PRESSURE"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"


class WorkloadPriority(int, Enum):
    P0_ACTIVE_APPLICATION = 0       # Real in-flight job application
    P1_HANDOFF_SESSION = 1           # CAPTCHA / MFA / human review waiting session
    P2_RECOVERY_DIAGNOSTICS = 2      # Forensic diagnostics & recovery reconciliation
    P3_TARGETED_TESTS = 3            # Small focused local test executions
    P4_FULL_REGRESSION_CI = 4        # Heavy test suites / full regression
    P5_BACKGROUND_CLEANUP = 5        # Retention cleanup / analytics


@dataclass
class ResourceMetrics:
    timestamp: float = field(default_factory=time.time)
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    available_memory_mb: float = 0.0
    disk_free_gb: float = 0.0
    chromium_process_count: int = 0
    browser_context_count: int = 0
    active_jobs_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "available_memory_mb": round(self.available_memory_mb, 1),
            "disk_free_gb": round(self.disk_free_gb, 2),
            "chromium_process_count": self.chromium_process_count,
            "browser_context_count": self.browser_context_count,
            "active_jobs_count": self.active_jobs_count,
        }


class ResourceManager:
    """Monitors system pressure and arbitrates scheduling decisions."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        cpu_pressure_threshold: float = 75.0,
        cpu_high_pressure_threshold: float = 88.0,
        memory_pressure_threshold: float = 80.0,
        memory_high_pressure_threshold: float = 88.0,
        memory_critical_threshold: float = 92.0,
        disk_min_free_gb: float = 5.0,
        disk_critical_free_gb: float = 1.5,
    ):
        self.base_dir = base_dir or Path(__file__).resolve().parent
        self.cpu_pressure_threshold = cpu_pressure_threshold
        self.cpu_high_pressure_threshold = cpu_high_pressure_threshold
        self.memory_pressure_threshold = memory_pressure_threshold
        self.memory_high_pressure_threshold = memory_high_pressure_threshold
        self.memory_critical_threshold = memory_critical_threshold
        self.disk_min_free_gb = disk_min_free_gb
        self.disk_critical_free_gb = disk_critical_free_gb
        self._last_metrics: Optional[ResourceMetrics] = None

    def sample_metrics(
        self,
        browser_contexts: int = 0,
        active_jobs: int = 0,
    ) -> ResourceMetrics:
        """Sample current system metrics safely using psutil or os fallbacks."""
        cpu_pct = 0.0
        mem_pct = 0.0
        avail_mem_mb = 0.0
        chrom_procs = 0

        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            mem_pct = mem.percent
            avail_mem_mb = mem.available / (1024 * 1024)
            for proc in psutil.process_iter(["name"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if "chromium" in name or "chrome" in name:
                        chrom_procs += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            # Fallback if psutil not installed in environment
            pass

        # Free disk space
        disk_free_gb = 0.0
        try:
            total, used, free = shutil.disk_usage(str(self.base_dir))
            disk_free_gb = free / (1024 * 1024 * 1024)
        except Exception:
            disk_free_gb = 10.0

        metrics = ResourceMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_pct,
            memory_percent=mem_pct,
            available_memory_mb=avail_mem_mb,
            disk_free_gb=disk_free_gb,
            chromium_process_count=chrom_procs,
            browser_context_count=browser_contexts,
            active_jobs_count=active_jobs,
        )
        self._last_metrics = metrics
        return metrics

    def classify_health(self, metrics: Optional[ResourceMetrics] = None) -> HealthCategory:
        """Classifies system health based on the latest resource metrics."""
        m = metrics or self._last_metrics or self.sample_metrics()

        if m.memory_percent >= self.memory_critical_threshold or m.disk_free_gb <= self.disk_critical_free_gb:
            return HealthCategory.CRITICAL

        if (
            m.memory_percent >= self.memory_high_pressure_threshold
            or m.cpu_percent >= self.cpu_high_pressure_threshold
            or m.disk_free_gb <= (self.disk_critical_free_gb * 1.5)
        ):
            return HealthCategory.HIGH_PRESSURE

        if (
            m.memory_percent >= self.memory_pressure_threshold
            or m.cpu_percent >= self.cpu_pressure_threshold
            or m.disk_free_gb <= self.disk_min_free_gb
        ):
            return HealthCategory.PRESSURE

        return HealthCategory.HEALTHY

    def can_schedule(
        self,
        priority: WorkloadPriority | int,
        metrics: Optional[ResourceMetrics] = None,
    ) -> Tuple[bool, str]:
        """Determines whether a new task with given priority can safely be scheduled.
        
        Live applications (P0) and handoff sessions (P1) are strictly protected.
        Heavy CI/regression (P4) and cleanup (P5) are deferred under pressure.
        """
        p_val = int(priority)
        health = self.classify_health(metrics)

        if health == HealthCategory.HEALTHY:
            return True, "System healthy; task eligible for scheduling."

        if health == HealthCategory.PRESSURE:
            if p_val >= WorkloadPriority.P4_FULL_REGRESSION_CI:
                return False, f"System under PRESSURE; deferring P{p_val} heavy/background task."
            return True, "System under light PRESSURE; high-priority task permitted."

        if health == HealthCategory.HIGH_PRESSURE:
            if p_val >= WorkloadPriority.P3_TARGETED_TESTS:
                return False, f"System under HIGH_PRESSURE; non-critical P{p_val} task blocked."
            return True, "System under HIGH_PRESSURE; only active app / handoff permitted."

        if health == HealthCategory.CRITICAL:
            if p_val > WorkloadPriority.P1_HANDOFF_SESSION:
                return False, "System in CRITICAL state; all non-essential workloads suspended."
            return True, "CRITICAL state; preserving active application / handoff session."

        return False, "System in unknown or OFFLINE state."

