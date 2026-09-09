"""Typed, read-only equipment documentation discovery.

The status envelope says what a device will accept *now*. Self-documenting
gateways may additionally publish an agent guide, a proposal catalog, and
OpenAPI. These models keep those three sources distinct so callers never infer
that source code or a local SDK upgrade proves a running gateway was deployed.
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


__all__ = [
    "ActionCatalog",
    "AgentDocumentation",
    "DiscoveredAction",
    "EquipmentDiscovery",
    "OpenAPIDocument",
]
