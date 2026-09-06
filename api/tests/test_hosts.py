"""Host inventory grouping tests (`GET /api/hosts`).

The Computers-and-Servers page renders what `group_hosts` derives from
`equipment.yaml` + the SSH host whitelist — nothing is hand-synced anymore, so
these tests pin the derivation rules: hostname/alias matching (FQDN vs short
label, loopback and tailnet IPs onto gaia), role classification (ops /
service / equipment), port-vs-path parsing, and the `other_hosts` fallback for
machines outside the whitelist (the device Pis).
"""

from __future__ import annotations

from pathlib import Path

from lab_skills import Registry, load_registry
from lab_skills.registry import EquipmentEntry

from app.hosts import group_hosts

REPO_ROOT = Path(__file__).resolve().parents[2]


def _entry(entry_id: str, kind: str, base_url: str | None, **kwargs) -> EquipmentEntry:
    return EquipmentEntry(
        id=entry_id,
        name=kwargs.pop("name", entry_id),
        kind=kind,
        adapter=kwargs.pop("adapter", "http"),
        base_url=base_url,
        **kwargs,
    )


def _by_id(payload: dict, host_id: str) -> dict:
    return next(h for h in payload["hosts"] if h["id"] == host_id)


def test_groups_services_onto_whitelisted_hosts_by_alias_and_label():
    registry = Registry(
        equipment=[
            # FQDN base_url matches the short-hostname host and vice versa.
            _entry(
                "xarm_translocation",
                "robot_arm",
                "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000",
                name="UFactory xArm5",
            ),
            # Loopback and the tailnet IP are both gaia (the aggregator host).
            _entry("kasa_tapo_gateway", "other", "http://127.0.0.1:8002"),
            _entry("analytica_db", "other", "http://100.64.254.6:8010"),
            # The hostops agent entry classifies as role "ops".
            _entry(
                "hostops_cytation_pc",
                "other",
                "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8060",
            ),
            # No base_url (mock placeholder awaiting hardware) -> omitted.
            _entry("env_storage", "environmental_sensor", None, adapter="mock"),
        ]
    )
    payload = group_hosts(registry)

    cytation = _by_id(payload, "cytation-pc")
    roles = {s["id"]: s["role"] for s in cytation["services"]}
    assert roles == {"xarm_translocation": "equipment", "hostops_cytation_pc": "ops"}
    xarm = cytation["services"][0]
    assert xarm["port"] == 8000
    assert xarm["base_url"] == "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000"

    gaia = _by_id(payload, "gaia")
    assert [s["id"] for s in gaia["services"]] == ["kasa_tapo_gateway", "analytica_db"]
    assert all(s["role"] == "service" for s in gaia["services"])

    # Every whitelisted host appears even with nothing matched onto it.
    assert _by_id(payload, "uplc-pc")["services"] == []
    # Device hosts are always listed (they are machines, with or without a
    # registry service); what must be empty is the anonymous remainder.
    assert [g for g in payload["other_hosts"] if not g.get("id")] == []


def test_pathonly_urls_and_unlisted_hosts():
    registry = Registry(
        equipment=[
            # Edge path, no explicit port: port None, path carries the label.
            _entry(
                "hermes_web", "other", "http://100.64.254.6/hermes/", adapter="mock"
            ),
            # Device Pis: whitelisted, so they land in other_hosts *named* —
            # the fume hood by tailnet-IP alias, the doser by hostname.
            _entry("fume_hood_actuator", "fume_hood", "http://100.64.254.100:5000"),
            _entry(
                "dose_every_well",
                "solid_doser",
                "http://sdl2-pi5-minicnc.tail6a1dd7.ts.net:8000",
            ),
            # A hostname no machine claims stays anonymous, at the end.
            _entry("mystery_box", "other", "http://192.0.2.7:9000"),
        ]
    )
    payload = group_hosts(registry)

    hermes = _by_id(payload, "gaia")["services"][0]
    assert hermes["port"] is None
    assert hermes["path"] == "/hermes/"
    assert hermes["adapter"] == "mock"

    named = {g["id"]: g for g in payload["other_hosts"] if g.get("id")}
    assert [s["id"] for s in named["fumehood-pi"]["services"]] == ["fume_hood_actuator"]
    assert named["fumehood-pi"]["label"] == "Fume Hood Actuator"
    assert [s["id"] for s in named["doser-pi"]["services"]] == ["dose_every_well"]
    pi = named["fumehood-pi"]["services"][0]
    assert (pi["id"], pi["role"], pi["port"]) == ("fume_hood_actuator", "equipment", 5000)

    # Unclaimed hostnames keep the old anonymous shape and sort after the
    # named device hosts.
    anonymous = [g for g in payload["other_hosts"] if not g.get("id")]
    assert [g["hostname"] for g in anonymous] == ["192.0.2.7"]
    assert payload["other_hosts"][-1] is anonymous[0]


def test_committed_registry_groups_cleanly():
    """The real equipment.yaml: every http entry with a base_url lands on a
    host group, the hostops agents classify as ops on their PCs, and gaia
    absorbs the loopback + tailnet-IP services."""
    registry = load_registry(REPO_ROOT / "equipment.yaml")
    payload = group_hosts(registry)

    grouped = {s["id"] for h in payload["hosts"] for s in h["services"]} | {
        s["id"] for g in payload["other_hosts"] for s in g["services"]
    }
    expected = {e.id for e in registry.equipment if e.base_url}
    assert grouped == expected

    cytation = _by_id(payload, "cytation-pc")
    ops = [s for s in cytation["services"] if s["role"] == "ops"]
    assert [s["id"] for s in ops] == ["hostops_cytation_pc"]
    assert ops[0]["port"] == 8060
    uplc_ops = [s for s in _by_id(payload, "uplc-pc")["services"] if s["role"] == "ops"]
    assert [s["id"] for s in uplc_ops] == ["hostops_uplc_pc"]

    gaia_ids = {s["id"] for s in _by_id(payload, "gaia")["services"]}
    assert {"kasa_tapo_gateway", "pypoe_web", "analytica_db"} <= gaia_ids
    # Whitelisted machines never leak into the unlisted group.
    unlisted_hosts = {g["hostname"] for g in payload["other_hosts"]}
    assert not unlisted_hosts & {"127.0.0.1", "localhost", "100.64.254.6"}
    assert not any(h.startswith("sdl2-pc-") for h in unlisted_hosts)


def test_gibbie_pc_groups_its_bench_monitor_and_hostops_by_name_and_lab_switch_ip():
    registry = Registry(
        equipment=[
            # The bench monitor's per-device envelopes, all served by the Gibbie PC.
            _entry("gibbie_ur_arm", "robot_arm", "http://sdl2-pc-04.tail6a1dd7.ts.net:8070", name="UR-3e Arm (Gibbie)"),
            _entry("gibbie_server", "other", "http://sdl2-pc-04.tail6a1dd7.ts.net:8070"),
            # The host-ops agent slot, registered ahead of the install.
            _entry("hostops_gibbie_pc", "other", "http://sdl2-pc-04.tail6a1dd7.ts.net:8060"),
            # The same machine under its lab-switch address (HOST_ALIASES).
            _entry("gibbie_flex", "liquid_handler", "http://192.168.254.79:8070"),
        ]
    )
    payload = group_hosts(registry)
    gibbie = _by_id(payload, "gibbie-pc")
    assert gibbie["kind"] == "Windows PC"
    assert [s["id"] for s in gibbie["services"]] == [
        "gibbie_ur_arm", "gibbie_server", "hostops_gibbie_pc", "gibbie_flex",
    ]
    roles = {s["id"]: s["role"] for s in gibbie["services"]}
    assert roles["hostops_gibbie_pc"] == "ops"
    assert roles["gibbie_ur_arm"] == "equipment" and roles["gibbie_server"] == "service"
    # Device hosts are always listed (they are machines, with or without a
    # registry service); what must be empty is the anonymous remainder.
    assert [g for g in payload["other_hosts"] if not g.get("id")] == []


def test_lle_pc_groups_by_tailnet_lab_switch_and_campus_addresses():
    registry = Registry(
        equipment=[
            _entry("lle_hplc", "hplc", "http://sdl2-pc-00-lle.tail6a1dd7.ts.net:8090"),
            _entry("lle_balance", "other", "http://192.168.254.5:8090/devices/lle_balance/status"),
            _entry("lle_easymax", "other", "http://172.31.35.241:8090"),
            _entry("hostops_lle_pc", "other", "http://100.64.254.13:8060"),
        ]
    )
    payload = group_hosts(registry)
    lle = _by_id(payload, "lle-pc")
    assert [s["id"] for s in lle["services"]] == ["lle_hplc", "lle_balance", "lle_easymax", "hostops_lle_pc"]
    assert {s["id"]: s["role"] for s in lle["services"]}["hostops_lle_pc"] == "ops"
    # Device hosts are always listed (they are machines, with or without a
    # registry service); what must be empty is the anonymous remainder.
    assert [g for g in payload["other_hosts"] if not g.get("id")] == []
