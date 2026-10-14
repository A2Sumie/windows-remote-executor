"""Regression checks for stopped sshd recovery and bounded state transitions."""

from types import SimpleNamespace
import pytest

from win32 import service
from win32 import scheduled_tasks
from actions import host
import rpc


STOPPED, START_PENDING, STOP_PENDING, RUNNING = 1, 2, 3, 4
CONSTANTS = SimpleNamespace(SERVICE_STOPPED=STOPPED, SERVICE_START_PENDING=START_PENDING,
                            SERVICE_STOP_PENDING=STOP_PENDING, SERVICE_RUNNING=RUNNING)


class ServiceDouble:
    def __init__(self, state=STOPPED, *, stop_finishes=True, start_finishes=True):
        self.state = state
        self.stop_finishes = stop_finishes
        self.start_finishes = start_finishes
        self.calls = []

    def QueryServiceStatus(self, name):
        return (0, self.state, 0, 0, 0, 0, 0)

    def StopService(self, name):
        self.calls.append("stop")
        if self.state == STOPPED:
            raise RuntimeError("1062: service has not been started")
        self.state = STOPPED if self.stop_finishes else STOP_PENDING

    def StartService(self, name):
        self.calls.append("start")
        self.state = RUNNING if self.start_finishes else START_PENDING


def test_stopped_service_is_started_without_stop(monkeypatch):
    double = ServiceDouble()
    monkeypatch.setattr(service, "_import_win32serviceutil", lambda: (double, CONSTANTS))
    result = service.restart_service_safe("sshd", timeout_seconds=1)
    assert result["action"] == "started"
    assert double.calls == ["start"]
    assert double.state == RUNNING


def test_running_service_stops_then_starts(monkeypatch):
    double = ServiceDouble(RUNNING)
    monkeypatch.setattr(service, "_import_win32serviceutil", lambda: (double, CONSTANTS))
    result = service.restart_service_safe("sshd", timeout_seconds=1)
    assert result["action"] == "restarted"
    assert double.calls == ["stop", "start"]


def test_stop_timeout_does_not_claim_success_or_try_start(monkeypatch):
    double = ServiceDouble(RUNNING, stop_finishes=False)
    monkeypatch.setattr(service, "_import_win32serviceutil", lambda: (double, CONSTANTS))
    result = service.restart_service_safe("sshd", timeout_seconds=0)
    assert result["action"] == "restart.error"
    assert double.calls == ["stop"]


def test_start_timeout_does_not_claim_success(monkeypatch):
    double = ServiceDouble(start_finishes=False)
    monkeypatch.setattr(service, "_import_win32serviceutil", lambda: (double, CONSTANTS))
    result = service.restart_service_safe("sshd", timeout_seconds=0)
    assert result["action"] == "restart.error"
    assert double.calls == ["start"]


def test_existing_healthy_repair_tasks_are_not_recreated(monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "_repair_task_matches", lambda *args: True)
    calls = []
    monkeypatch.setattr(scheduled_tasks, "create_task", lambda spec: calls.append(spec))
    result = scheduled_tasks.ensure_repair_tasks(expected_listen="100.64.0.1")
    assert not calls
    assert len(result["kept"]) == 3
    assert result["errors"] == []


def test_repair_watch_uses_a_repeating_time_trigger(monkeypatch):
    monkeypatch.setattr(scheduled_tasks, "_repair_task_matches", lambda *args: False)
    calls = []
    monkeypatch.setattr(scheduled_tasks, "create_task", lambda spec: calls.append(spec))
    scheduled_tasks.ensure_repair_tasks(expected_listen="100.64.0.1")
    watch = next(spec for spec in calls if spec["name"].endswith("Watch"))
    assert watch["trigger"] == "interval"
    assert watch["interval_minutes"] == 10
    trigger = SimpleNamespace(Repetition=SimpleNamespace(Interval=""), StartBoundary="")
    definition = SimpleNamespace(Triggers=SimpleNamespace(Create=lambda kind: trigger))
    scheduled_tasks._set_trigger(definition, "interval", None, 10)
    assert trigger.StartBoundary
    assert trigger.Repetition.Interval == "PT10M"


def test_scheduled_repair_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr(host, "_repair", lambda payload: {"data": {"steps": ["sshd.restart.error:1062"]}})
    assert rpc._run_repair_sshd([]) == 1


def test_scheduled_repair_started_service_exits_success(monkeypatch):
    monkeypatch.setattr(host, "_repair", lambda payload: {"data": {"steps": ["sshd.started"]}})
    assert rpc._run_repair_sshd([]) == 0


@pytest.mark.parametrize("step", ["sshd.recovery.error:denied", "repair_tasks.error:invalid trigger",
                                 "sshd_config.error:denied"])
def test_required_repair_configuration_failure_exits_nonzero(monkeypatch, step):
    monkeypatch.setattr(host, "_repair", lambda payload: {"data": {"steps": [step]}})
    assert rpc._run_repair_sshd([]) == 1


def test_service_startup_recovery_is_idempotent_and_closes_handles(monkeypatch):
    config = {1: False, 2: {"ResetPeriod": 0, "Actions": [], "Command": None, "RebootMsg": None},
              3: False}
    changes, closed = [], []

    def update(handle, level, value):
        changes.append(level)
        config[level] = value

    api = SimpleNamespace(
        SC_MANAGER_CONNECT=1, SERVICE_QUERY_CONFIG=1, SERVICE_CHANGE_CONFIG=2, SERVICE_START=4,
        SERVICE_AUTO_START=2, SC_ACTION_RESTART=1,
        SERVICE_CONFIG_DELAYED_AUTO_START_INFO=1, SERVICE_CONFIG_FAILURE_ACTIONS=2,
        SERVICE_CONFIG_FAILURE_ACTIONS_FLAG=3,
        OpenSCManager=lambda *args: "manager", OpenService=lambda *args: "service",
        QueryServiceConfig=lambda handle: (0, 2), QueryServiceConfig2=lambda handle, level: config[level],
        ChangeServiceConfig2=update, CloseServiceHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(service, "_import_win32serviceutil", lambda: (None, api))
    first = service.ensure_auto_recovery_safe("sshd")
    second = service.ensure_auto_recovery_safe("sshd")
    assert first["ensured"] and second["ensured"]
    assert changes == [1, 2, 3] and second["changed"] == []
    assert config[2]["Actions"] == [(1, 60000)] * 3
    assert closed == ["service", "manager", "service", "manager"]
