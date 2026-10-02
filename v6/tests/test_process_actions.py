"""process.* action tests. The synchronous run_capture path runs REAL local
/bin utilities (the module's documented macOS loopback fixtures) — no
network, no Windows host. The job model runs against a tmp JOBS_DIR with the
detached supervisor spawned as a local subprocess."""

from __future__ import annotations

import json
import os
import time

import actions.process as proc
import pytest

BIN_ECHO = "/bin/echo"
BIN_SLEEP = "/bin/sleep"
BIN_CAT = "/bin/cat"


@pytest.mark.skipif(not os.path.exists(BIN_ECHO), reason="posix loopback only")
def test_run_capture_echo():
    out = proc.run_capture(BIN_ECHO, ["hello", "雪"], cwd=None, stdin_text=None,
                           env=dict(os.environ), timeout_ms=10_000, capture_kb=16)
    assert out["exitCode"] == 0
    assert out["stdout"].strip() == "hello 雪"
    assert out["timedOut"] is False and out["killVerified"] is True
    assert out["stdoutTruncated"] is False
    assert isinstance(out["pid"], int) and out["pid"] > 0


@pytest.mark.skipif(not os.path.exists(BIN_SLEEP), reason="posix loopback only")
def test_run_capture_timeout_kills():
    started = time.perf_counter()
    out = proc.run_capture(BIN_SLEEP, ["30"], cwd=None, stdin_text=None,
                           env=dict(os.environ), timeout_ms=300, capture_kb=16)
    elapsed = time.perf_counter() - started
    assert out["timedOut"] is True
    assert out["killVerified"] is True
    assert elapsed < 20  # kill verified well inside the retry budget


@pytest.mark.skipif(not os.path.exists(BIN_CAT), reason="posix loopback only")
def test_run_capture_stdin_and_ring_truncation():
    out = proc.run_capture(BIN_CAT, [], cwd=None, stdin_text="x" * 4096,
                           env=dict(os.environ), timeout_ms=10_000, capture_kb=1)
    assert out["exitCode"] == 0
    assert out["stdoutTruncated"] is True
    assert len(out["stdout"]) <= 1024
    assert out["stdout"] == "x" * 1024  # tail kept


def test_run_capture_missing_exe_raises():
    with pytest.raises((FileNotFoundError, PermissionError, OSError)):
        proc.run_capture("/nonexistent/exe", [], cwd=None, stdin_text=None,
                         env=dict(os.environ), timeout_ms=1000, capture_kb=1)


def test_process_run_action_end_to_end():
    out = proc.process_run({"exe": BIN_ECHO, "args": ["hi"], "timeoutMs": 5000})
    assert out["data"]["exitCode"] == 0
    assert out["data"]["stdout"].strip() == "hi"
    assert "process.run" in out["evidence_extra"]


def test_process_run_rejects_shell_string_args():
    with pytest.raises(ValueError, match="not a shell string"):
        proc.process_run({"exe": BIN_ECHO, "args": "a b c"})


def test_process_run_quoting_note_for_stuffed_argv(tmp_path):
    out = proc.process_run({"exe": "/bin/bash", "cwd": str(tmp_path),
                            "args": ["-c", "echo a long command with ; and > metachars inside"]})
    assert "quotingNote" in out["data"]


# ---------- job model (local supervisor, tmp jobs dir) ----------

@pytest.mark.skipif(not os.path.exists(BIN_ECHO), reason="posix loopback only")
def test_job_lifecycle_start_wait_status(tmp_jobs_dir):
    out = proc.process_start({"exe": BIN_ECHO, "args": ["job-output"]})
    job_id = out["data"]["jobId"]
    assert job_id.startswith("j-") and out["data"]["supervisorPid"] > 0
    waited = proc.process_wait({"jobId": job_id, "timeoutMs": 15000, "tailKB": 4})
    assert waited["data"]["stillRunning"] is False
    assert waited["data"]["state"] == "exited"
    assert waited["data"]["exitCode"] == 0
    assert "job-output" in waited["data"]["logTail"]
    status = proc.process_status({"jobId": job_id})
    assert status["data"]["state"] == "exited"
    assert status["data"]["killed"] is False
    assert status["data"]["outputSizeBytes"] > 0


@pytest.mark.skipif(not os.path.exists(BIN_SLEEP), reason="posix loopback only")
def test_job_kill_records_killed_state(tmp_jobs_dir):
    out = proc.process_start({"exe": BIN_SLEEP, "args": ["60"]})
    job_id = out["data"]["jobId"]
    killed = proc.process_kill({"jobId": job_id, "force": True})
    assert killed["data"]["killSent"] is True
    waited = proc.process_wait({"jobId": job_id, "timeoutMs": 15000})
    assert waited["data"]["killed"] is True
    assert waited["data"]["state"] == "killed"
    assert waited["data"]["stillRunning"] is False


@pytest.mark.skipif(not os.path.exists(BIN_SLEEP), reason="posix loopback only")
def test_job_server_side_timeout(tmp_jobs_dir):
    out = proc.process_start({"exe": BIN_SLEEP, "args": ["60"], "timeoutMs": 500})
    job_id = out["data"]["jobId"]
    waited = proc.process_wait({"jobId": job_id, "timeoutMs": 20000})
    assert waited["data"]["stillRunning"] is False
    assert waited["data"]["timedOut"] is True


def test_job_wait_unknown_id_raises_not_found(tmp_jobs_dir):
    with pytest.raises(FileNotFoundError):
        proc.process_wait({"jobId": "j-0000000000000000", "timeoutMs": 100})


def test_job_wait_still_running_reports(tmp_jobs_dir):
    out = proc.process_start({"exe": BIN_SLEEP, "args": ["30"]})
    job_id = out["data"]["jobId"]
    try:
        waited = proc.process_wait({"jobId": job_id, "timeoutMs": 300})
        assert waited["data"]["stillRunning"] is True
        assert waited["data"]["state"] == "running"
    finally:
        proc.process_kill({"jobId": job_id, "force": True})
        proc.process_wait({"jobId": job_id, "timeoutMs": 15000})


# ---------- reaper ----------

def test_looks_abandoned_requires_positive_evidence(tmp_jobs_dir):
    from auditlog import now_iso
    fresh = {"startedAt": now_iso(), "state": "running"}
    # no pids, fresh meta -> booting supervisor, NOT abandoned (H3)
    assert proc._looks_abandoned(fresh) is False
    dead_pid = 2**22 - 1
    while os.path.exists(f"/proc/{dead_pid}"):
        dead_pid -= 1
    abandoned = {"startedAt": now_iso(), "state": "running",
                 "supervisorPid": dead_pid, "pid": dead_pid}
    assert proc._looks_abandoned(abandoned) is True
    old = {"startedAt": "2000-01-01T00:00:00+00:00", "state": "running"}
    assert proc._looks_abandoned(old) is True  # ancient + no pids


def test_registry_reaps_dead_supervisor_meta(tmp_jobs_dir, monkeypatch):
    monkeypatch.setattr(proc, "_REGISTRY_LOADED", False)  # per-process latch
    job_id = "j-deadbeefdeadbeef"
    meta = {"jobId": job_id, "state": "running", "startedAt": "2000-01-01T00:00:00+00:00",
            "supervisorPid": None, "pid": None, "exe": BIN_SLEEP, "args": ["1"]}
    (tmp_jobs_dir / f"{job_id}.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_jobs_dir / f"{job_id}.json").write_text(json.dumps(meta))
    proc._load_registry()
    reaped = json.loads((tmp_jobs_dir / f"{job_id}.json").read_text())
    assert reaped["state"] == "exited"
    assert "reaped" in reaped.get("note", "")


def test_meta_lock_roundtrip(tmp_jobs_dir):
    (tmp_jobs_dir).mkdir(parents=True, exist_ok=True)
    job_id = "j-0123456789abcdef"
    meta = proc._update_meta(job_id, {"state": "running"}, fallback={"jobId": job_id})
    assert meta["state"] == "running"
    meta2 = proc._update_meta(job_id, {"killed": True})
    assert meta2["state"] == "running" and meta2["killed"] is True  # merge, not replace


def test_job_view_distinguishes_killed_from_exited(tmp_jobs_dir):
    from auditlog import now_iso
    (tmp_jobs_dir).mkdir(parents=True, exist_ok=True)
    job_id = "j-aaaabbbbccccdddd"
    meta = {"jobId": job_id, "state": "exited", "exitCode": None, "killed": True,
            "startedAt": now_iso(), "exe": BIN_SLEEP, "args": []}
    (tmp_jobs_dir / f"{job_id}.json").write_text(json.dumps(meta))
    view = proc._job_view(job_id)
    assert view["state"] == "killed"
    meta["killed"] = False
    (tmp_jobs_dir / f"{job_id}.json").write_text(json.dumps(meta))
    assert proc._job_view(job_id)["state"] == "exited"


# ---------- wsl loopback through WRE_WSL_EXE ----------

@pytest.mark.skipif(not os.path.exists(BIN_ECHO), reason="posix loopback only")
def test_wsl_run_via_mock_exe(monkeypatch):
    from actions import wsl
    monkeypatch.setenv("WRE_WSL_EXE", BIN_ECHO)
    out = wsl.wsl_run({"argv": ["/bin/ls", "-l"], "distro": "Ubuntu"})
    assert out["data"]["exitCode"] == 0
    assert "--exec" in out["data"]["stdout"]
    assert out["data"]["argv"][0] == BIN_ECHO


def test_wsl_ensure_wsl_errors_off_windows(monkeypatch):
    from actions import wsl
    monkeypatch.delenv("WRE_WSL_EXE", raising=False)
    if os.name != "nt":
        with pytest.raises(FileNotFoundError, match="Windows-only"):
            wsl._ensure_wsl()
