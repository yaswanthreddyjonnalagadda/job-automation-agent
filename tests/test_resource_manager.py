import pytest
from resource_manager import ResourceManager, HealthCategory, ResourceMetrics, WorkloadPriority


def test_resource_manager_health_classification():
    rm = ResourceManager(
        cpu_pressure_threshold=75.0,
        memory_pressure_threshold=80.0,
        memory_high_pressure_threshold=88.0,
        memory_critical_threshold=92.0,
        disk_min_free_gb=5.0,
        disk_critical_free_gb=1.5,
    )

    # 1. Healthy
    m_healthy = ResourceMetrics(cpu_percent=25.0, memory_percent=50.0, disk_free_gb=20.0)
    assert rm.classify_health(m_healthy) == HealthCategory.HEALTHY

    # 2. Light Pressure
    m_pressure = ResourceMetrics(cpu_percent=78.0, memory_percent=60.0, disk_free_gb=20.0)
    assert rm.classify_health(m_pressure) == HealthCategory.PRESSURE

    # 3. High Pressure
    m_high = ResourceMetrics(cpu_percent=89.0, memory_percent=70.0, disk_free_gb=20.0)
    assert rm.classify_health(m_high) == HealthCategory.HIGH_PRESSURE

    # 4. Critical Memory
    m_crit = ResourceMetrics(cpu_percent=30.0, memory_percent=95.0, disk_free_gb=20.0)
    assert rm.classify_health(m_crit) == HealthCategory.CRITICAL

    # 5. Critical Disk
    m_disk_crit = ResourceMetrics(cpu_percent=30.0, memory_percent=50.0, disk_free_gb=1.0)
    assert rm.classify_health(m_disk_crit) == HealthCategory.CRITICAL


def test_workload_priority_arbitration():
    rm = ResourceManager()

    # Under PRESSURE: P0 (Live app) permitted, P4 (CI) deferred
    m_pressure = ResourceMetrics(cpu_percent=80.0, memory_percent=70.0, disk_free_gb=10.0)
    can_app, _ = rm.can_schedule(WorkloadPriority.P0_ACTIVE_APPLICATION, m_pressure)
    assert can_app is True

    can_ci, reason_ci = rm.can_schedule(WorkloadPriority.P4_FULL_REGRESSION_CI, m_pressure)
    assert can_ci is False
    assert "deferring" in reason_ci.lower()

    # Under CRITICAL: P0/P1 preserved, P2+ suspended
    m_crit = ResourceMetrics(cpu_percent=30.0, memory_percent=94.0, disk_free_gb=10.0)
    can_app_crit, _ = rm.can_schedule(WorkloadPriority.P0_ACTIVE_APPLICATION, m_crit)
    assert can_app_crit is True

    can_targeted, _ = rm.can_schedule(WorkloadPriority.P3_TARGETED_TESTS, m_crit)
    assert can_targeted is False

