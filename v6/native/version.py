"""WRE v6 shared constants — protocol version, build tag, naming parameters.

Naming parameterization (2026-08-19: brand flipped CodexRemote -> WRE):
Every on-host path and scheduled-task name prefix funnels through the two
constants below. Defaults are now `C:/WRE` / `WRE`. The legacy fleet tree
`C:/CodexRemote/wre` was fully retired on 2026-08-25 (all enabled tasks
migrated to C:/WRE/wre python); it stays on disk as cold-standby rollback
and stays hardcoded-protected in actions/files.py forever.

  WRE_ROOT    on-host root directory holding jobs/, logs/, inbox/ and the
              wre tree itself. Env override: WRE_ROOT.
  TASK_PREFIX scheduled-task name prefix managed by this bridge.
              Env override: WRE_TASK_PREFIX.
  WRE_TREE    the directory containing the RUNNING rpc.py (derived from
              __file__, never hardcoded) — a sidecar deploy at
              C:/WRE/wre6/ is protected independently of the default
              C:/WRE/wre/ tree (and of the legacy C:/CodexRemote/wre/ tree).
"""

from __future__ import annotations

import os

PROTOCOL_VERSION = 6
BUILD = "v6.1"  # 2026-08-25 hardening: reap grace, killVerified, audit
#               rotation, jobs-history, scan caps, controller deadline

WRE_ROOT = os.environ.get("WRE_ROOT") or "C:/WRE"
TASK_PREFIX = os.environ.get("WRE_TASK_PREFIX") or "WRE"

# Directory of the running rpc.py (= the deployed tree root, e.g.
# C:/WRE/wre or a sidecar C:/WRE/wre6; legacy fleet: C:/CodexRemote/wre).
WRE_TREE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")

# Derived shared locations (all forward-slash, Windows-style).
JOBS_DIR = f"{WRE_ROOT}/jobs"
LOGS_DIR = f"{WRE_ROOT}/logs"
INBOX_DIR = f"{WRE_ROOT}/inbox"
AUDIT_LOG_PATH = f"{LOGS_DIR}/rpc-audit.log"

# Managed infrastructure task names (kept out of host.tasks.clean by default).
REPAIR_TASK_NAMES = (
    f"{TASK_PREFIX} Sshd Repair Logon",
    f"{TASK_PREFIX} Sshd Repair Startup",
    f"{TASK_PREFIX} Sshd Repair Watch",
)
APPLY_TASK_NAME = f"{TASK_PREFIX} WRE Apply"
