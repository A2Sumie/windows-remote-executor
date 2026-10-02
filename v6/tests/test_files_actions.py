"""file.* action tests: path normalization, guardrail canon, read/write/list/
search semantics on tmp dirs (the only writes are under pytest tmp_path —
protected-prefix checks raise BEFORE any filesystem touch)."""

from __future__ import annotations

import base64
import os
import re

import pytest
from actions import files

# ---------- _norm ----------

def test_norm_basic_forward_slashes():
    assert files._norm("C:/WRE/inbox/a.txt") == "C:" + os.sep + "WRE" + os.sep + "inbox" + os.sep + "a.txt"
    # backslashes are preserved verbatim (Windows-side semantics)
    assert files._norm("C:\\WRE\\inbox") == "C:\\WRE\\inbox"


def test_norm_strips_symmetric_quotes():
    assert files._norm('"C:/a b.txt"') == "C:" + os.sep + "a b.txt"
    assert files._norm("'C:/a.txt'") == "C:" + os.sep + "a.txt"
    # asymmetric quotes are left alone
    assert files._norm('"C:/a.txt') == '"C:' + os.sep + 'a.txt'


def test_norm_empty_raises():
    for bad in ("", "   ", '""', "  ''  "):
        with pytest.raises(ValueError, match="path is required"):
            files._norm(bad)


def test_norm_unc_verbatim():
    unc = "//wsl.localhost/Ubuntu/home/me/f.txt"
    assert files._norm(unc) == unc
    assert files._norm("//server/share/x") == "//server/share/x"


def test_norm_collapses_doubled_backslashes():
    # JSON-decoded "C:\\WRE\\inbox" (doubled backslashes) collapses to singles;
    # _norm never converts "\" — only "/" -> os.sep
    n = files._norm("C:\\\\WRE\\\\inbox")
    assert n == "C:\\WRE\\inbox"
    # a UNC head spelled "\\server" keeps its leading pair, tail collapses
    n2 = files._norm("\\\\server\\\\share")
    assert n2 == "\\\\server\\share"


# ---------- _canon_for_guard ----------

def test_canon_collapses_dot_segments_and_case():
    assert files._canon_for_guard("C:/WRE/wre/../wre/x") == "c:/wre/wre/x"
    assert files._canon_for_guard("C:\\\\WRE\\\\Wre") == "c:/wre/wre"
    assert files._canon_for_guard("//?/C:/WRE/wre") == "c:/wre/wre"
    assert files._canon_for_guard("C:/a/./b/") == "c:/a/b"


# ---------- protected path guardrail ----------

@pytest.mark.parametrize("path", [
    "C:/WRE/wre/rpc.py",
    "c:/wre/wre",                      # case-insensitive
    "C:\\WRE\\wre\\x",
    "C:/WRE/wre/../wre/y",             # dot-segment escape attempt
    "C:/CodexRemote/wre/old.py",       # legacy fleet tree stays protected
    "C:/Windows/System32/drivers/etc/hosts",
    "C:/ProgramData/ssh/sshd_config",
])
def test_protected_prefixes_rejected(path):
    with pytest.raises(ValueError, match="protected prefix"):
        files._check_protected_path(path, {})


def test_protected_allow_override():
    files._check_protected_path("C:/WRE/wre/rpc.py", {"allowProtected": True})


def test_protected_boundary_not_overbroad():
    files._check_protected_path("C:/WRE/wre2/x", {})       # sibling, not under
    files._check_protected_path("C:/WindowsX/y", {})
    files._check_protected_path("D:/anything/at/all", {})


def test_jobs_metadata_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "JOBS_DIR", str(tmp_path / "jobs"))
    with pytest.raises(ValueError, match="job metadata"):
        files._check_protected_path(str(tmp_path / "jobs" / "j-x.json"), {})
    # non-json files under jobs/ are fine (logs)
    files._check_protected_path(str(tmp_path / "jobs" / "j-x.log"), {})
    files._check_protected_path(str(tmp_path / "jobs" / "j-x.json"),
                                {"allowProtected": True})


# ---------- read/write on tmp ----------

def test_write_then_read_proof(tmp_path):
    p = str(tmp_path / "sub" / "a.txt")
    wr = files.write_text({"path": p, "text": "hello 雪\n"})
    proof = wr["data"]["proof"]
    assert proof["byteLength"] == len("hello 雪\n".encode())
    rd = files.read_text({"path": p})
    assert rd["data"]["text"] == "hello 雪\n"
    assert rd["data"]["proof"]["sha256"] == proof["sha256"]
    assert rd["stdout_text"] == "hello 雪\n"


def test_write_text_rejects_non_string_text(tmp_path):
    with pytest.raises((ValueError, AttributeError, TypeError)):
        files.write_text({"path": str(tmp_path / "x"), "text": {"not": "text"}})


def test_read_missing_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        files.read_text({"path": str(tmp_path / "nope")})


def test_read_offset_and_tail(tmp_path):
    p = tmp_path / "log.txt"
    p.write_bytes(b"0123456789")
    rd = files.read_text({"path": str(p), "offset": 4})
    assert rd["data"]["text"] == "456789"
    assert rd["data"]["nextOffset"] == 10
    rd2 = files.read_text({"path": str(p), "tail": 3})
    assert rd2["data"]["text"] == "789"
    assert rd2["data"]["tailBytes"] == 3
    with pytest.raises(ValueError, match="mutually exclusive"):
        files.read_text({"path": str(p), "offset": 1, "tail": 1})


def test_read_offset_with_max_bytes(tmp_path):
    p = tmp_path / "log.txt"
    p.write_bytes(b"0123456789")
    rd = files.read_text({"path": str(p), "offset": 2, "maxBytes": 3})
    assert rd["data"]["text"] == "234"
    assert rd["data"]["nextOffset"] == 5


def test_read_maxbytes_stat_before_read(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 4096)
    with pytest.raises(ValueError, match="exceeds maxBytes"):
        files.read_text({"path": str(p), "maxBytes": 1024})
    rd = files.read_text({"path": str(p), "maxBytes": 8192})
    assert rd["data"]["bytes"] == 4096


def test_read_partial_proof_defaults_off(tmp_path):
    p = tmp_path / "log.txt"
    p.write_bytes(b"0123456789")
    assert "proof" not in files.read_text({"path": str(p), "tail": 2})["data"]
    assert "proof" in files.read_text(
        {"path": str(p), "tail": 2, "includeProof": True})["data"]


def test_read_include_base64_opt_in(tmp_path):
    p = tmp_path / "b.txt"
    p.write_bytes(b"AB")
    rd = files.read_text({"path": str(p), "includeBase64": True})
    assert rd["data"]["base64"] == base64.b64encode(b"AB").decode()
    assert "base64" not in files.read_text({"path": str(p)})["data"]


def test_mkdir_and_delete_tree(tmp_path):
    d = str(tmp_path / "a" / "b" / "c")
    files.mkdir({"path": d})
    assert os.path.isdir(d)
    out = files.delete_tree({"path": str(tmp_path / "a")})
    assert out["data"]["deleted"] is True
    out2 = files.delete_tree({"path": str(tmp_path / "a")})
    assert out2["data"]["existed"] is False


def test_copy_file(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("data")
    dst = str(tmp_path / "deep" / "d.txt")
    out = files.copy({"source": str(src), "destination": dst})
    assert out["data"]["proof"]["byteLength"] == 4
    with pytest.raises(FileNotFoundError):
        files.copy({"source": str(tmp_path / "nope"), "destination": dst})


# ---------- putBinary ----------

def test_putbinary_simple_and_chunked(tmp_path):
    p = str(tmp_path / "f.bin")
    out = files.put_binary({"path": p, "base64": base64.b64encode(b"AB").decode()})
    assert out["data"]["proof"]["byteLength"] == 2

    p2 = str(tmp_path / "g.bin")
    r1 = files.put_binary({"path": p2, "base64": base64.b64encode(b"AB").decode(),
                           "chunkIndex": 0, "totalChunks": 2})
    assert r1["data"] == {"receivedChunks": 1, "totalChunks": 2,
                          "receivedBytes": 2, "complete": False}
    r2 = files.put_binary({"path": p2, "base64": base64.b64encode(b"CD").decode(),
                           "chunkIndex": 1, "totalChunks": 2})
    assert r2["data"]["complete"] is True
    with open(p2, "rb") as fh:
        assert fh.read() == b"ABCD"
    assert not os.path.exists(p2 + ".wre-part")


def test_putbinary_chunk_validation(tmp_path):
    p = str(tmp_path / "f.bin")
    b64 = base64.b64encode(b"x").decode()
    with pytest.raises(ValueError, match="integers"):
        files.put_binary({"path": p, "base64": b64, "chunkIndex": "0",
                          "totalChunks": 1})
    with pytest.raises(ValueError, match="invalid chunkIndex"):
        files.put_binary({"path": p, "base64": b64, "chunkIndex": 1,
                          "totalChunks": 1})
    with pytest.raises(ValueError, match="missing"):
        files.put_binary({"path": p, "base64": b64, "chunkIndex": 1,
                          "totalChunks": 2})  # no staging file yet
    with pytest.raises(ValueError, match="base64 is required"):
        files.put_binary({"path": p})


def test_putbinary_rejects_bad_base64(tmp_path):
    import binascii
    with pytest.raises((binascii.Error, ValueError)):
        files.put_binary({"path": str(tmp_path / "f"), "base64": "!!!not-base64!!!"})


def test_putbinary_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_PUTBINARY_MAX_BYTES", 4)
    b64 = base64.b64encode(b"12345").decode()
    with pytest.raises(ValueError, match="MB|cap|exceeds"):
        files.put_binary({"path": str(tmp_path / "f"), "base64": b64})


# ---------- list / search caps ----------

def test_list_dir_basic_and_pattern(tmp_path):
    (tmp_path / "a.log").write_text("1")
    (tmp_path / "b.txt").write_text("2")
    (tmp_path / "sub").mkdir()
    out = files.list_dir({"path": str(tmp_path), "pattern": "*.log"})
    names = [e["name"] for e in out["data"]["entries"]]
    assert names == ["a.log"]
    assert out["data"]["truncated"] is False


def test_list_dir_max_entries_truncates(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}").write_text("x")
    out = files.list_dir({"path": str(tmp_path), "maxEntries": 2})
    assert out["data"]["count"] == 2 and out["data"]["truncated"] is True


def test_list_dir_recursive_scan_cap(tmp_path):
    for i in range(3):
        d = tmp_path / f"d{i}"
        d.mkdir()
        (d / "x").write_text("y")
    out = files.list_dir({"path": str(tmp_path), "recursive": True, "maxScan": 1})
    assert out["data"]["truncated"] is True
    assert out["data"]["dirsScanned"] == 1


def test_search_requires_a_criterion(tmp_path):
    with pytest.raises(ValueError, match="required"):
        files.search({"root": str(tmp_path)})


def test_search_by_name_and_content(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 42\n")
    (tmp_path / "b.txt").write_text("nothing here\n")
    by_name = files.search({"root": str(tmp_path), "nameGlob": "*.py"})
    assert [m["name"] for m in by_name["data"]["matches"]] == ["a.py"]
    by_content = files.search({"root": str(tmp_path), "contentRegex": "return \\d+"})
    assert by_content["data"]["matches"][0]["line"] == 2
    # path alias for root
    assert files.search({"path": str(tmp_path), "nameGlob": "*.py"})["data"]["count"] == 1


def test_search_max_files_cap(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.log").write_text("x")
    out = files.search({"root": str(tmp_path), "nameGlob": "*.nomatch", "maxFiles": 2})
    assert out["data"]["truncated"] is True
    assert out["data"]["filesExamined"] == 2


def test_search_missing_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        files.search({"root": str(tmp_path / "nope"), "nameGlob": "*"})


def test_search_bad_regex_surfaces_as_value_error(tmp_path):
    with pytest.raises(re.error):
        files.search({"root": str(tmp_path), "contentRegex": "([unclosed"})


# ---------- WSL UNC helpers ----------

def test_parse_wsl_unc():
    assert files._parse_wsl_unc("//wsl.localhost/Ubuntu/home/me") == ("Ubuntu", "/home/me")
    assert files._parse_wsl_unc("//wsl$/Debian/tmp/x") == ("Debian", "/tmp/x")
    assert files._parse_wsl_unc("//wsl.localhost/") is None
    assert files._parse_wsl_unc("//wsl.localhost/OnlyDistro") is None
    assert files._parse_wsl_unc("C:/plain/path") is None
    assert files._is_wsl_unc("//WSL.LOCALHOST/Ubuntu/x") is True
    assert files._is_wsl_unc("//other/x") is False


# ---------- _TailBuffer / _RingLog ----------

def test_tailbuffer_ring_semantics():
    import actions.process as proc
    buf = proc._TailBuffer(8)
    buf.append(b"01234")
    buf.append(b"56789")     # total 10 > cap 8 -> keeps last 8
    assert buf.bytes() == b"23456789"
    assert buf.total == 10
    buf.append(b"abcdefghij")  # single oversized chunk keeps its own tail
    assert buf.bytes() == b"cdefghij"
    buf2 = proc._TailBuffer(4)
    buf2.append(b"")
    assert buf2.bytes() == b"" and buf2.total == 0


def test_ringlog_compacts(tmp_path):
    import actions.process as proc
    log = proc._RingLog(str(tmp_path / "j.log"), cap=8)
    for _ in range(10):
        log.write(b"01234567")   # 80 bytes written; file stays bounded
    log.close()
    size = os.path.getsize(tmp_path / "j.log")
    assert size <= 16  # 2x cap at compaction time, then bounded tail
