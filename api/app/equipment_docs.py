"""Read-only, allowlisted proxy for equipment API documentation.

Remote browsers cannot use registry URLs such as ``127.0.0.1:8012`` and must
not call equipment services directly.  This router exposes only documentation
paths declared on an equipment registry entry.  It never proxies control
methods or arbitrary device paths.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html

from .control import _AUTH_FALLBACK_STATUS, _device_auth_candidates


def build_equipment_docs_router() -> APIRouter:
    router = APIRouter(prefix="/api/equipment", tags=["equipment-docs"])

    @router.get(
        "/{equipment_id}/documentation/{document_path:path}",
        summary="Read configured equipment documentation",
        response_class=Response,
    )
    async def equipment_documentation(
        equipment_id: str,
        document_path: str,
        request: Request,
    ) -> Response:
        """Serve one registry-allowlisted, read-only equipment document.

        Swagger is rendered by the dashboard against the equipment's proxied
        OpenAPI JSON. Other configured documents are fetched from the running
        service. If an older deployment returns 404, that status and body are
        preserved so the reference never claims a capability is available.
        """

        registry = request.app.state.registry
        entry = registry.by_id(equipment_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Unknown equipment id: {equipment_id}")
        if entry.adapter != "http" or not entry.base_url:
            raise HTTPException(
                status_code=404,
                detail=f"Equipment {equipment_id!r} has no HTTP documentation surface",
            )

        source_path = f"/{document_path.lstrip('/')}"
        document = next((doc for doc in entry.documentation if doc.path == source_path), None)
        if document is None:
            raise HTTPException(
                status_code=404,
                detail=f"Documentation path {source_path!r} is not registered for {equipment_id!r}",
            )

        proxy_root = f"/api/equipment/{quote(equipment_id, safe='')}/documentation"
        if document.kind == "swagger":
            openapi_document = next(
                (doc for doc in entry.documentation if doc.kind == "openapi"),
                None,
            )
            if openapi_document is None:
                raise HTTPException(
                    status_code=503,
                    detail=f"No OpenAPI document is registered for {equipment_id!r}",
                )
            return get_swagger_ui_html(
                openapi_url=f"{proxy_root}{openapi_document.path}",
                title=f"{entry.name} API documentation",
                swagger_ui_parameters={"supportedSubmitMethods": []},
            )

        client = getattr(request.app.state, "control_client", None)
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(entry.poll_timeout_seconds),
                trust_env=False,
            )

        try:
            upstream = None
            for headers in _device_auth_candidates(request, entry):
                upstream = await client.get(
                    f"{entry.base_url.rstrip('/')}{document.path}",
                    headers={"Accept": "application/json", **headers},
                )
                if upstream.status_code not in _AUTH_FALLBACK_STATUS:
                    break
            assert upstream is not None
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Timed out reading {document.path} from {equipment_id!r}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not read {document.path} from {equipment_id!r}: {exc}",
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        response_headers: dict[str, str] = {}
        content_type = upstream.headers.get("content-type")
        if content_type:
            response_headers["content-type"] = content_type
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return router
