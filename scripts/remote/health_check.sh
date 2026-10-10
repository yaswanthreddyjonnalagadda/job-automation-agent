#!/usr/bin/env bash
# ==============================================================================
# Worker Health Check Script
# ==============================================================================
set -euo pipefail

cd /opt/job-automation-agent
source .venv/bin/activate

python -c "
import sys
from resource_manager import ResourceManager, HealthCategory
from worker_manager import WorkerRegistry, WorkerStatus

rm = ResourceManager()
metrics = rm.sample_metrics()
health = rm.classify_health(metrics)

print(f'System Health: {health.value}')
print(f'Memory: {metrics.memory_percent}% | Disk Free: {metrics.disk_free_gb} GB | CPU: {metrics.cpu_percent}%')

if health == HealthCategory.CRITICAL:
    print('ERROR: System in CRITICAL state.')
    sys.exit(2)
"

