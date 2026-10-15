"""Typed, read-only equipment documentation discovery.

The status envelope says what a device will accept *now*. Self-documenting
gateways may additionally publish an agent guide, a proposal catalog, and
OpenAPI. These models keep those three sources distinct so callers never infer
that source code or a local SDK upgrade proves a running gateway was deployed.

Two guide shapes exist and are kept apart rather than normalized
(EQUIP_GUIDE §1 Step B3, Compatibility):

* :class:`AgentDocumentation` -- the typed, SDK-shaped guide the liquid-handler
  gateways publish (``documentation_version`` + ``equipment_kind``). Read by
  ``EquipmentClient.discover()``.
* :class:`DeviceGuide` -- any other device's ``/docs/agent``, kept as served
  JSON. Its only structural requirement is the identity it names
  (``equipment_id``), which must be the registry entry's. Read by
  ``EquipmentClient.discover_device_guide()``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentDocumentation(BaseModel):
    model_config = ConfigDict(extra="allow")

    documentation_version: str
    equipment_kind: str
    model: str | None = None
    protocol_version: str | None = None
    scope: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    discovery: list[str] = Field(default_factory=list)
    conventions: dict[str, Any] = Field(default_factory=dict)
    agent_boundary: list[str] = Field(default_factory=list)
    python_api_coverage: dict[str, Any] = Field(default_factory=dict)


class DiscoveredAction(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str
    idempotent: bool
    args_schema: dict[str, Any] | None = None


class ActionCatalog(BaseModel):
    model_config = ConfigDict(extra="allow")

    actions: list[DiscoveredAction] = Field(default_factory=list)


class OpenAPIDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    openapi: str
    info: dict[str, Any] = Field(default_factory=dict)
    paths: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, Any] = Field(default_factory=dict)


class EquipmentDiscovery(BaseModel):
    """Results from the three optional documentation endpoints.

    Missing endpoints are named in ``unavailable``. Transport failures and
    malformed documents still raise: those are not evidence of an older
    gateway and must not be softened into a compatibility fallback.
    """

    agent_docs: AgentDocumentation | None = None
    action_catalog: ActionCatalog | None = None
    openapi: OpenAPIDocument | None = None
    unavailable: dict[str, str] = Field(default_factory=dict)


class DeviceGuide(BaseModel):
    """A device-specific ``/docs/agent`` guide, exactly as the device served it.

    ``equipment_id`` is the identity the guide names, already checked against
    the registry entry; ``document`` is the whole JSON object, unmodified. No
    field inside ``document`` is interpreted by the SDK, so a guide's schemas
    are documentation, not a validation source for proposals.
    """

    equipment_id: str
    document: dict[str, Any]


class DeviceGuideDiscovery(BaseModel):
    """The device-guide counterpart of :class:`EquipmentDiscovery`.

    Same three endpoints and the same rule: HTTP 404/405 is named in
    ``unavailable``; transport failures, malformed bodies and a guide that
    names another device raise.
    """

    guide: DeviceGuide | None = None
    action_catalog: ActionCatalog | None = None
    openapi: OpenAPIDocument | None = None
    unavailable: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "ActionCatalog",
    "AgentDocumentation",
    "DeviceGuide",
    "DeviceGuideDiscovery",
    "DiscoveredAction",
    "EquipmentDiscovery",
    "OpenAPIDocument",
]
