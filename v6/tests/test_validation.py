"""Structured-arguments contract tests.

The bridge's core rule: "Structured arguments only; never shell strings."
The validation layer must REJECT shell-string-shaped input explicitly:
  - process.* args must be a JSON array (a string is refused with guidance)
  - process.* exe must be an absolute path (no PATH search)
  - wsl.run takes argv XOR shell
  - every registered action ships a JSON Schema + help entry
"""

from __future__ import annotations

import actions.process as proc
import pytest
import rpc as native_rpc
from actions import wsl
from schemas import HELP, PAYLOAD_SCHEMAS

# ---------- process args/exe validation ----------

def test_args_shell_string_rejected():
    with pytest.raises(ValueError, match="not a shell string"):
        proc._validate_args("echo hi & dir")


@pytest.mark.parametrize("bad", [
    "ls -la",
    "cmd /c echo hi",
    "/d,/c,echo",
    "a;rm -rf /",
])
def test_args_string_forms_rejected(bad):
    with pytest.raises(ValueError):
        proc._validate_args(bad)


def test_args_array_accepted_and_coerced():
    assert proc._validate_args(["-X", "utf8", 1, True]) == ["-X", "utf8", "1", "True"]
    assert proc._validate_args(None) == []
    assert proc._validate_args([]) == []


def test_args_non_list_non_string_rejected():
    with pytest.raises(ValueError, match="JSON array"):
        proc._validate_args({"a": 1})
    with pytest.raises(ValueError):
        proc._validate_args(42)


def test_exe_required():
    with pytest.raises(ValueError, match="requires payload.exe"):
        proc._validate_exe(None)
    with pytest.raises(ValueError):
        proc._validate_exe("   ")


@pytest.mark.skipif(proc.os.name == "nt", reason="POSIX branch on the controller")
def test_exe_relative_rejected_posix():
    with pytest.raises(ValueError, match="not an absolute path"):
        proc._validate_exe("echo")
    with pytest.raises(ValueError):
        proc._validate_exe("../bin/echo")
    assert proc._validate_exe("/bin/echo") == "/bin/echo"


def test_timeout_validation():
    assert proc._validate_timeout({}) == proc.DEFAULT_TIMEOUT_MS
    assert proc._validate_timeout({"timeoutMs": 5000}) == 5000
    with pytest.raises(ValueError, match="positive"):
        proc._validate_timeout({"timeoutMs": 0})
    with pytest.raises(ValueError):
        proc._validate_timeout({"timeoutMs": -10})
    with pytest.raises(ValueError):
        proc._validate_timeout({"timeoutMs": "soon"})


def test_job_id_shape():
    assert proc._check_job_id({"jobId": "j-0123456789abcdef"}) == "j-0123456789abcdef"
    for bad in ("", "j-123", "j-0123456789abcdefX", "j-zzzzzzzzzzzzzzzz",
                "j-0123456789ABCDEF", "../../etc", "j-0123456789abcde\nf"):
        with pytest.raises(ValueError):
            proc._check_job_id({"jobId": bad})


# ---------- wsl argv assembly ----------

def test_wsl_argv_form():
    args = wsl.build_argv({"argv": ["/bin/ls", "-l"], "distro": "Ubuntu",
                           "user": "me", "winCwd": "C:/work"})
    assert args == ["-d", "Ubuntu", "-u", "me", "--cd", "C:/work",
                    "--exec", "/bin/ls", "-l"]


def test_wsl_shell_form():
    args = wsl.build_argv({"shell": "echo hi"})
    assert args == ["--exec", "bash", "-lc", "echo hi"]


def test_wsl_argv_xor_shell():
    with pytest.raises(ValueError, match="not both"):
        wsl.build_argv({"argv": ["ls"], "shell": "ls"})
    with pytest.raises(ValueError, match="requires"):
        wsl.build_argv({})


def test_wsl_argv_must_be_array():
    with pytest.raises(ValueError, match="JSON array"):
        wsl.build_argv({"argv": "ls -la"})


def test_wsl_env_always_injects_utf8(monkeypatch):
    monkeypatch.delenv("WSL_UTF8", raising=False)
    env = wsl.build_env({})
    assert env["WSL_UTF8"] == "1"
    env2 = wsl.build_env({"env": {"FOO": "bar", "WSL_UTF8": "0"}})
    assert env2["FOO"] == "bar"


def test_wsl_list_output_parsing():
    table = (
        "  NAME            STATE           VERSION\n"
        "* Ubuntu-22.04    Running         2\n"
        "  Debian          Stopped         2\n"
        "  legacy          Stopped         1\n"
    )
    distros = wsl.parse_list_output(table)
    assert distros[0] == {"name": "Ubuntu-22.04", "state": "Running",
                          "default": True, "wslVersion": 2}
    assert distros[1]["name"] == "Debian" and distros[1]["default"] is False
    assert distros[2]["wslVersion"] == 1
    assert wsl.parse_list_output("") == []
    assert wsl.parse_list_output("garbage\nlines") == []


def test_wsl_utf16_listing_decode():
    raw = "N\0A\0M\0E\0\n\0U\0b\0u\0n\0t\0u\0"
    decoded = wsl._decode_wsl_listing(raw)
    assert "NAME" in decoded and "Ubuntu" in decoded


# ---------- schema/help self-description completeness ----------

def test_every_action_has_schema_and_help():
    for name in native_rpc.ACTIONS:
        assert name in PAYLOAD_SCHEMAS, f"missing schema for {name}"
        assert name in HELP, f"missing help for {name}"
        entry = HELP[name]
        assert entry.get("summary") and entry.get("identity") in ("ssh-user", "system-path")


def test_no_schema_without_action():
    for name in PAYLOAD_SCHEMAS:
        assert name in native_rpc.ACTIONS, f"schema for unregistered {name}"


def test_action_surface_is_the_documented_32():
    expected = {
        "host.capabilities", "host.info", "host.probe", "host.guard",
        "host.repair", "host.policy", "system.help",
        "host.tasks.list", "host.tasks.detail", "host.tasks.clean",
        "host.tasks.apply", "host.task.create", "host.task.update",
        "host.task.run", "host.task.delete",
        "file.writeText", "file.readText", "file.mkdir", "file.deleteTree",
        "file.copy", "file.putBinary", "file.list", "file.search",
        "process.run", "process.start", "process.wait", "process.status",
        "process.kill", "wsl.run", "wsl.list", "wsl.wslpath", "wsl.status",
    }
    assert set(native_rpc.ACTIONS) == expected


def test_process_schemas_declare_args_as_array():
    for action in ("process.run", "process.start"):
        props = PAYLOAD_SCHEMAS[action]["properties"]
        assert props["args"]["type"] == "array"
        assert props["args"]["items"] == {"type": "string"}
        assert "exe" in PAYLOAD_SCHEMAS[action]["required"]


def test_shell_wrapper_notes():
    note = proc._shell_wrapper_note("C:/Windows/System32/cmd.exe", ["/d", "/c", "echo hi"])
    assert "LAST command" in note
    assert proc._shell_wrapper_note("/bin/echo", ["hi"]) == ""
    assert proc._shell_wrapper_note("bash", []) == ""
    comp = proc._composite_arg_note("/bin/bash", ["-c", "echo a very long string with ; and > inside"])
    assert "stuffed into one element" in comp
    assert proc._composite_arg_note("/bin/echo", ["plain", "args"]) == ""
