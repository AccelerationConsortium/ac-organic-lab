"""Read-only Windows Ethernet/ICS probes through an existing SSH alias.

This observes a network connection, never printer readiness or print activity.
No remote files, services, routes, firewall rules, or device controls are changed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..models import ComponentStatus, EquipmentStatus, MetricValue
from .base import AdapterResult, EquipmentAdapter, now_utc


class NetworkTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssh_alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    interface: str = Field(min_length=1)
    uplink: str = Field(min_length=1)
    mac: str = Field(pattern=r"^(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$")
    subnet: IPv4Network


class NetworkObservation(BaseModel):
    ethernet: StrictBool
    uplink: StrictBool
    sharing: StrictBool
    dhcp: StrictBool
    reachable: StrictBool
    ip: str | None
    latency_ms: int | None


# Config enters PowerShell as base64 JSON, never as executable interpolation.
# Resolve candidates by MAC on the specified interface, then require fresh ICMP
# and a matching MAC again. A permanent/stale ARP entry alone is not liveness.
_PROBE = r"""
$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'
$c=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')))
$adapter=Get-NetAdapter -Name $c.interface
$uplink=Get-NetAdapter -Name $c.uplink
$addresses=@(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4)
$dhcp=@(Get-NetUDPEndpoint -LocalPort 67 -ErrorAction SilentlyContinue | Where-Object {$_.LocalAddress -in $addresses.IPAddress}).Count -gt 0
$share=New-Object -ComObject HNetCfg.HNetShare
$public=$false; $private=$false
foreach($conn in $share.EnumEveryConnection){
  $p=$share.NetConnectionProps($conn); $s=$share.INetSharingConfigurationForINetConnection($conn)
  if($s.SharingEnabled){
    if($p.Name -eq $c.uplink -and $s.SharingConnectionType -eq 0){$public=$true}
    if($p.Name -eq $c.interface -and $s.SharingConnectionType -eq 1){$private=$true}
  }
}
$candidates=@(Get-NetNeighbor -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 | Where-Object {$_.LinkLayerAddress -eq $c.mac} | Sort-Object @{Expression={if($_.State -eq 'Reachable'){0}elseif($_.State -eq 'Permanent'){2}else{1}}} | Select-Object -First 2)
$ip=$null; $latency=$null; $reachable=$false
$ping=New-Object Net.NetworkInformation.Ping
try {
  foreach($candidate in $candidates){
    $reply=$ping.Send($candidate.IPAddress,750)
    $neighbor=Get-NetNeighbor -InterfaceIndex $adapter.ifIndex -IPAddress $candidate.IPAddress
    if($reply.Status -eq 'Success' -and $neighbor.LinkLayerAddress -eq $c.mac){
      $ip=$candidate.IPAddress; $latency=$reply.RoundtripTime; $reachable=$true; break
    }
  }
} finally {$ping.Dispose()}
[pscustomobject]@{ethernet=($adapter.Status -eq 'Up');uplink=($uplink.Status -eq 'Up');sharing=($public -and $private);dhcp=$dhcp;reachable=$reachable;ip=$ip;latency_ms=$latency} | ConvertTo-Json -Compress
"""


def probe_command(target: NetworkTarget) -> list[str]:
    config = base64.b64encode(target.model_dump_json().encode()).decode()
    script = _PROBE.replace("__CONFIG__", config)
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=3",
        target.ssh_alias,
        "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded,
    ]


def network_target_config(equipment_id: str) -> dict:
    path = Path(
        os.environ.get(
            "LAB_NETWORK_MONITORS_CONFIG",
            str(Path(__file__).resolve().parents[4] / "network-monitors.local.json"),
        )
    )
    return json.loads(path.read_text())[equipment_id]


class SshNetworkAdapter(EquipmentAdapter):
    def target(self) -> NetworkTarget:
        return NetworkTarget.model_validate(network_target_config(self.entry.id))

    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        try:
            target = self.target()
        except (OSError, ValueError, KeyError) as exc:
            return self.fail(f"Network monitor configuration: {exc}", "unconfigured")
        started = time.monotonic()
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *probe_command(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=7)
            if process.returncode:
                return self.fail(
                    "Network probe failed: " + stderr.decode(errors="replace")[-600:], "unknown"
                )
            observation = NetworkObservation.model_validate_json(stdout)
            if observation.reachable and (
                observation.ip is None or IPv4Address(observation.ip) not in target.subnet
            ):
                raise ValueError("Probe returned an address outside the configured subnet")
            return self.result(observation, int((time.monotonic() - started) * 1000))
        except asyncio.TimeoutError:
            return self.fail("SSH network probe timed out", "timeout")
        except (OSError, ValueError) as exc:
            return self.fail(f"Network probe error: {exc}", "parse_error")
        finally:
            # Also runs when the aggregator cancels this fetch at its timeout.
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()

    def result(self, observation: NetworkObservation, elapsed_ms: int) -> AdapterResult:
        checks = {
            "printer_ethernet": observation.reachable,
            "ethernet_link": observation.ethernet,
            "wifi_link": observation.uplink,
            "internet_sharing": observation.sharing,
            "dhcp": observation.dhcp,
        }
        if not observation.reachable or not observation.ethernet:
            state = "unknown"  # gateway_fronted maps this to unreachable/uptime loss.
            message = "Printer not reachable over Ethernet; check cable, power and DHCP."
        elif not all(checks.values()):
            state = "degraded"
            message = "Printer reachable; check " + ", ".join(k for k, v in checks.items() if not v)
        else:
            state = "ready"
            message = (
                "Ethernet reachable; Wi-Fi sharing and DHCP enabled. Cloud access is unverified."
            )
        metrics = {}
        if observation.ip:
            metrics["printer_ip"] = MetricValue(value=observation.ip)
        if observation.latency_ms is not None:
            metrics["ping"] = MetricValue(value=observation.latency_ms, unit="ms")
        stamp = now_utc()
        status = EquipmentStatus(
            protocol_version="1.2",
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status=state,
            message=message,
            device_time=stamp,
            activity="unknown",
            allowed_actions=[],
            metrics=metrics,
            components={
                name: ComponentStatus(connected=ok, state="up" if ok else "down")
                for name, ok in checks.items()
            },
            details={
                "monitoring_only": True,
                "scope": "network_connection",
                "printer_activity": "unknown",
                "cloud_access": "unverified",
                "poll_interval_s": 60,
            },
        )
        return AdapterResult(status=status, fetched_at=stamp, latency_ms=elapsed_ms, error=None)
