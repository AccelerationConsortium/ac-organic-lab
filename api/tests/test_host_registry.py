"""``hosts.yaml`` loader tests — and the drift gate that is the point of it.

Phase 1 of extracting the host inventory out of Python literals: the YAML
exists and is loaded by :mod:`app.host_registry`, but nothing reads it at
runtime yet. ``app.ssh_console.SSH_HOSTS`` and ``app.hosts.HOST_ALIASES``
remain the live values.

That only stays safe while the two cannot diverge, so the tests below assert
the committed ``hosts.yaml`` reconstructs **exactly** those Python objects —
same hosts in the same order, every field equal, every ssh profile equal, and
every alias accounted for in both directions (no address the YAML is missing,
no address the YAML invented). Edit one side without the other and this file
fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.host_registry import HostsConfig, load_hosts
from app.hosts import HOST_ALIASES
from app.ssh_console import SSH_HOSTS

REPO_ROOT = Path(__file__).resolve().parents[2]
HOSTS_YAML = REPO_ROOT / "hosts.yaml"


@pytest.fixture(scope="module")
def config() -> HostsConfig:
    return load_hosts(HOSTS_YAML)


# -- Loader -----------------------------------------------------------------


def test_loads_committed_inventory(config: HostsConfig) -> None:
    ids = [h.id for h in config.hosts]
    assert "orchestration" in ids
    assert "cytation-pc" in ids
    assert "lle-pi" in ids
    assert config.by_id("gaia") is not None
    assert config.by_id("no-such-host") is None


def test_default_path_finds_the_repo_root_file() -> None:
    """No argument and no env var walks up from the module, exactly like
    ``load_registry`` / ``load_platforms``."""
    assert load_hosts().model_dump() == load_hosts(HOSTS_YAML).model_dump()


def test_env_var_overrides_the_default_path(monkeypatch) -> None:
    monkeypatch.setenv("LAB_HOSTS_PATH", str(HOSTS_YAML))
    assert [h.id for h in load_hosts().hosts] == [h.id for h in SSH_HOSTS]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_hosts(tmp_path / "nope.yaml")


def test_unknown_profile_id_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "hosts.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "ssh_profiles": [
                    {"id": "shell", "label": "Shell", "description": "…"}
                ],
                "hosts": [
                    {
                        "id": "h",
                        "label": "H",
                        "kind": "Linux server",
                        "hostname": "h",
                        "ssh": {
                            "user": "u",
                            "target": "h",
                            "shell": "bash",
                            "profiles": ["tmux"],
                        },
                        "note": "…",
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="unknown ssh profile"):
        load_hosts(bad)


def test_unknown_network_type_is_rejected(tmp_path: Path) -> None:
    doc = yaml.safe_load(HOSTS_YAML.read_text())
    doc["hosts"][0]["networks"] = [{"type": "carrier_pigeon", "address": "1.2.3.4"}]
    bad = tmp_path / "hosts.yaml"
    bad.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="Invalid host inventory"):
        load_hosts(bad)


# -- The drift gate ---------------------------------------------------------


def test_hosts_round_trip_to_the_python_literals(config: HostsConfig) -> None:
    """Every ``SshHost`` field, host for host and in the same order."""
    assert [h.id for h in config.hosts] == [h.id for h in SSH_HOSTS]

    for entry, ssh_host in zip(config.hosts, SSH_HOSTS):
        assert entry.label == ssh_host.label, ssh_host.id
        assert entry.kind == ssh_host.kind, ssh_host.id
        assert entry.group == ssh_host.group, ssh_host.id
        assert entry.hostname == ssh_host.hostname, ssh_host.id
        assert entry.note == ssh_host.note, ssh_host.id
        assert entry.ssh.user == ssh_host.user, ssh_host.id
        assert entry.ssh.target == ssh_host.target, ssh_host.id
        assert entry.ssh.shell == ssh_host.shell, ssh_host.id


def test_ssh_profiles_round_trip_to_the_python_literals(config: HostsConfig) -> None:
    """The profiles each host offers: same ids in the same order (the first
    is the default), and the same label / args / description behind each."""
    for entry, ssh_host in zip(config.hosts, SSH_HOSTS):
        assert entry.ssh.profiles == [p.id for p in ssh_host.profiles], ssh_host.id
        for declared, literal in zip(
            config.profiles_for(entry), ssh_host.profiles, strict=True
        ):
            assert declared.label == literal.label, (ssh_host.id, literal.id)
            assert tuple(declared.args) == literal.args, (ssh_host.id, literal.id)
            assert declared.description == literal.description, (
                ssh_host.id,
                literal.id,
            )


def test_every_declared_profile_is_used(config: HostsConfig) -> None:
    """``ssh_profiles`` is a shared table, not a junk drawer: an entry no host
    references is either a typo or a leftover."""
    used = {p for host in config.hosts for p in host.ssh.profiles}
    assert {p.id for p in config.ssh_profiles} == used


def test_networks_reconstruct_host_aliases_exactly(config: HostsConfig) -> None:
    """Both directions at once: the aliases derived from every host's
    ``networks`` (address + magicdns) equal ``HOST_ALIASES``, so neither a
    missing address nor an invented one survives."""
    assert config.aliases() == HOST_ALIASES


def test_no_host_alias_address_is_missing_from_the_yaml(config: HostsConfig) -> None:
    """The same claim per host, spelled out so a failure names the address."""
    for host_id, aliases in HOST_ALIASES.items():
        entry = config.by_id(host_id)
        assert entry is not None, f"{host_id} in HOST_ALIASES but not in hosts.yaml"
        assert set(aliases) - entry.aliases() == set(), host_id


def test_the_yaml_invents_no_address(config: HostsConfig) -> None:
    for entry in config.hosts:
        expected = set(HOST_ALIASES.get(entry.id, frozenset()))
        assert entry.aliases() - expected == set(), entry.id


def test_every_alias_is_classified_and_addressable(config: HostsConfig) -> None:
    """A network entry is only useful if its type matches its address family
    — the classification is what a later phase will route on."""
    prefixes = {
        "tailscale": ("100.64.",),
        "lab_switch": ("192.168.254.",),
        "campus_wifi": ("172.31.",),
    }
    for entry in config.hosts:
        for network in entry.networks:
            if network.type == "loopback":
                assert network.address in {"localhost", "127.0.0.1"}, entry.id
                continue
            assert network.address.startswith(prefixes[network.type]), (
                entry.id,
                network.address,
            )


# -- Hygiene ----------------------------------------------------------------


def test_the_file_carries_no_secret_material() -> None:
    """``hosts.yaml`` is committed. Key material, key paths and the names of
    credential-bearing environment variables stay in the service user's
    ``~/.ssh/config`` and ``.env`` — never here."""
    text = HOSTS_YAML.read_text()
    for marker in (
        "PRIVATE KEY",
        "ssh-ed25519 AAAA",
        "ssh-rsa AAAA",
        "id_ed25519",
        "id_rsa",
        ".pem",
        "IdentityFile",
        "_SECRET",
        "_TOKEN",
        "_PASSWORD",
        "password",
    ):
        assert marker not in text, f"{marker!r} must not appear in hosts.yaml"
