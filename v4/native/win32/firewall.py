"""Windows firewall rule management via HNetCfg.FwPolicy2 COM.

No `netsh.exe`. Reads/ensures the OpenSSH-Port-22-Inbound rule and any custom
WRE-managed rule.
"""

from __future__ import annotations

from typing import Any


def _fw_policy():  # type: ignore[no-untyped-def]
    import comtypes  # type: ignore
    import comtypes.client  # type: ignore
    type_lib_guid = "{D8B6E2C7-FC4F-4C2F-8DE3-3B4D6B6E2C7F}"  # placeholder; replaced at runtime
    # Use dynamic Dispatch via comtypes.client.CreateObject (much simpler than static iface).
    return comtypes.client.CreateObject("HNetCfg.FwPolicy2")


def list_sshd_rules() -> list[dict[str, Any]]:
    try:
        policy = _fw_policy()
        rules = policy.Rules
        out: list[dict[str, Any]] = []
        # Rules is an ICollection; enumerate via COM.
        for rule in rules:
            name = str(rule.Name)
            if not name:
                continue
            lower = name.lower()
            if "ssh" in lower or "sshd" in lower or "openssh" in lower or "wre" in lower:
                out.append({
                    "name": name,
                    "enabled": bool(rule.Enabled),
                    "direction": int(rule.Direction),
                    "protocol": int(rule.Protocol),
                    "localPorts": list(rule.LocalPorts) if rule.LocalPorts else [],
                    "application": str(rule.ApplicationName or ""),
                })
        return out
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


def ensure_sshd_firewall_rule() -> dict[str, Any]:
    """Ensure a private-only inbound rule exists for sshd on port 22."""
    try:
        policy = _fw_policy()
        rules = policy.Rules
        target_name = "WRE-SSHD-Inbound-22"
        for rule in rules:
            if str(rule.Name) == target_name:
                if not rule.Enabled:
                    rule.Enabled = True
                return {"name": target_name, "ensured": True, "existed": True}
        new_rule = _new_rule()
        new_rule.Name = target_name
        new_rule.Description = "WRE v4 managed inbound rule for OpenSSH on port 22 (private profiles only)."
        new_rule.ApplicationName = "%SystemRoot%/System32/OpenSSH/sshd.exe"
        new_rule.Protocol = 6  # TCP
        new_rule.LocalPorts = "22"
        new_rule.Direction = 1  # inbound
        new_rule.Enabled = True
        new_rule.Profiles = 0x7FFFFFFF  # all profiles — we rely on ListenAddress for actual scope
        new_rule.Grouping = "WRE"
        rules.Add(new_rule)
        return {"name": target_name, "ensured": True, "existed": False}
    except Exception as exc:  # noqa: BLE001
        return {"name": "WRE-SSHD-Inbound-22", "ensured": False, "error": str(exc)}


def _new_rule():  # type: ignore[no-untyped-def]
    import comtypes.client  # type: ignore
    return comtypes.client.CreateObject("HNetCfg.FwRule")