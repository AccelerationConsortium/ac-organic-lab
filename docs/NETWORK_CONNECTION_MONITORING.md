# Printer Ethernet connection monitoring

EufyMake and Elegoo Saturn Ultra appear alongside Bambu printers on
**Utils → 3D Printers** (`/utils/printers`). There is no separate Printing
platform section. Both connection tiles are read-only and expose no print
submission or device-control actions.

## EufyMake Ethernet

The registry uses the SDK's read-only `ssh_network`
adapter. It probes from the Windows PC attached to the printer's private
Ethernet network, using an existing trusted SSH alias. There is no printer
control surface or remote service installation.

The probe runs every 60 seconds. It checks the Ethernet and Wi-Fi link states,
the Windows Internet Connection Sharing public/private adapter assignment,
the DHCP listener bound to the Ethernet address, and a fresh ICMP response
from the printer. The printer is identified by its MAC on that interface,
not a pinned DHCP address. The probe tries up to two matching neighbor entries,
prioritizing currently reachable entries over stale/permanent entries, and
checks the MAC again after ping. It does not scan the subnet. If Windows has
no matching neighbor entry yet, the tile reports unreachable until discovery
or DHCP populates it. Reconnecting the printer's Ethernet cable can renew DHCP.

`ready` means the monitored connection checks passed, not that the printer is
ready to print. `degraded` means the printer responds but a sharing/uplink/DHCP
check failed. `unknown` with `gateway_fronted: true` means the printer connection
is unreachable and participates in the dashboard's normal uptime and sustained
outage/recovery alert handling. SSH, configuration, and parsing failures are
reported as fetch errors. No observations are fabricated on failure.

The tile displays the currently responding IP and ping latency. Printer job
activity stays `unknown`; cloud connectivity and Internet NAT forwarding from
the printer are **unverified**. Wi-Fi sharing being enabled is not proof of
cloud connectivity. No credentials, packet payloads, print jobs, or printer
cloud account data are collected. Claims and execution preconditions are N/A;
`allowed_actions` is empty. The probe never changes sharing, restarts services,
or reconnects or drives hardware. All equipment operation remains subject to
[the lab contract](AGENTIC_LAB_DESIGN.md#part-i--binding-rules-normative) and
[STATUS_SPEC](STATUS_SPEC.md).

## Local configuration

Machine-specific settings live in gitignored `network-monitors.local.json` in
the repository root. `LAB_NETWORK_MONITORS_CONFIG` can select another file.
Each key is a registered equipment id. EufyMake's `ssh_network` entry requires:

- `ssh_alias`: an already-provisioned SSH config alias; batch mode and strict
  host-key checking apply. Unknown keys/credentials are never auto-accepted.
- `interface`: Windows name of the printer-facing Ethernet adapter.
- `uplink`: Windows name of the shared Wi-Fi adapter.
- `mac`: printer Ethernet MAC, six hexadecimal pairs separated by hyphens.
- `subnet`: printer Ethernet IPv4 network in CIDR notation.

Elegoo's `tcp_network` entry requires `address` (IPv4) and `port` (1–65535).
It opens and immediately closes one TCP connection every 60 seconds, sending
no printer commands. The deployed endpoint is the printer's port 3030, the
[SDCP service port](https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/blob/main/SDCP%28Smart%20Device%20Control%20Protocol%29_V3.0.0_EN.md).
A connection means that endpoint accepts TCP, not that identity, resin,
readiness or print activity has been verified. Keep the address current if
its DHCP lease changes. A timeout/refusal means `unknown`/unreachable.
The connection attempt has a three-second deadline and cancellation propagates
to the pending socket. TCP works under the dashboard's existing service
restrictions and requires no raw-socket privilege or network/firewall changes.

The adapter reads configuration on each poll. Keep this file with deployment
configuration backups, not in git. There is no public endpoint for changing
probe targets. The PowerShell program is fixed and read-only; configuration is
passed as encoded JSON data. The SSH child is terminated when a probe times
out or is cancelled, avoiding orphan processes. The adapter's seven-second
budget fits within its registry entry's eight-second aggregator deadline.

This is an in-process SDK adapter, not a lab-maintained HTTP device service;
the HTTP service documentation endpoints in EQUIP_GUIDE Step B3 do not apply.
The device itself has no claimed STATUS_SPEC or documented lab API. The
adapter produces the status envelope for the connection monitor.
