"""Read-only, allowlisted proxy for equipment API documentation.

Remote browsers cannot use registry URLs such as ``127.0.0.1:8012`` and must
not call equipment services directly.  This router exposes only documentation
paths declared on an equipment registry entry.  It never proxies control
methods or arbitrary device paths.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import re
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from lab_skills.registry import DocumentationEndpoint, EquipmentEntry


_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
_DOCUMENT_TIMEOUT_SECONDS = 15.0
_SAFE_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
# llms.txt indexes use ordinary inline Markdown links. Only their destinations
# are adapted; document prose and links outside the registry are never executed.
_INDEX_LINK = re.compile(r"(\[[^\[\]\n]+\]\()(<[^<>\s()\[\]]+>|[^<>\s()\[\]]+)(\))")


def _index_links(text: str, entry: EquipmentEntry, document: DocumentationEndpoint) -> str:
    """Make registered upstream links relative to the proxied index URL.

    Older devices advertise internal absolute or root-relative links. Restrict
    adaptation to exact registered resources on this service; never fetch links
    or infer permission to expose status, control, or other device paths.
    """
    base = entry.base_url.rstrip("/")
    index_url = f"{base}{document.path}"
    destinations = {f"{base}{doc.path}": doc.path for doc in entry.documentation}

    def replace(match: re.Match) -> str:
        destination = match[2].removeprefix("<").removesuffix(">")
        resolved = urlsplit(urljoin(index_url, destination))
        path = destinations.get(resolved._replace(fragment="").geturl())
        if path is None:
            return match[0]
        relative = posixpath.relpath(path, posixpath.dirname(document.path))
        if resolved.fragment:
            relative += f"#{resolved.fragment}"
        return f"{match[1]}{relative}{match[3]}"

    return _INDEX_LINK.sub(replace, text)


async def _fetch_document(
    client: httpx.AsyncClient, entry: EquipmentEntry, document: DocumentationEndpoint,
) -> Response:
    accept = {"markdown": "text/markdown", "text": "text/plain"}.get(
        document.kind, "application/json",
    )
    # Build a standalone request: client defaults, cookie jars and incoming
    # viewer/edge identity must never become credentials for a public read.
    outgoing = httpx.Request(
        "GET", f"{entry.base_url.rstrip('/')}{document.path}",
        headers={"Accept": accept, "Accept-Encoding": "identity"},
        extensions={"timeout": httpx.Timeout(_DOCUMENT_TIMEOUT_SECONDS).as_dict()},
    )
    upstream = await client.send(outgoing, stream=True, auth=None, follow_redirects=False)
    try:
        if 300 <= upstream.status_code < 400:
            raise HTTPException(502, "Documentation upstream returned a redirect")
        # Avoid allocating an arbitrarily large decoded chunk from compressed
        # upstream data. Public docs must honor the requested identity encoding.
        if upstream.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(502, "Documentation upstream returned unsupported encoding")
        media_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        is_json = media_type == "application/json" or (
            media_type.startswith("application/") and media_type.endswith("+json")
        )
        is_text = media_type in {"text/plain", "text/markdown"}
        if upstream.status_code == 204:
            return Response(status_code=204, headers=_SAFE_HEADERS)
        if not (
            (upstream.is_error and (is_json or is_text))
            or (document.kind in {"openapi", "json"} and is_json)
            or (document.kind in {"markdown", "text"} and is_text)
        ):
            raise HTTPException(502, "Documentation upstream returned an unexpected media type")
        length = upstream.headers.get("content-length")
        if length is not None:
            try:
                declared_size = int(length)
            except ValueError as exc:
                raise HTTPException(502, "Documentation upstream returned invalid Content-Length") from exc
            if declared_size < 0 or declared_size > _MAX_DOCUMENT_BYTES:
                raise HTTPException(502, "Documentation upstream exceeded the response size limit")
        content = bytearray()
        async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
            if len(content) + len(chunk) > _MAX_DOCUMENT_BYTES:
                raise HTTPException(502, "Documentation upstream exceeded the response size limit")
            content.extend(chunk)
        try:
            text = content.decode("utf-8")
            if is_json:
                json.loads(text)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise HTTPException(502, "Documentation upstream returned invalid UTF-8 or JSON") from exc
        if upstream.is_success and document.path == "/llms.txt" and is_text:
            try:
                text = _index_links(text, entry, document)
            except ValueError as exc:
                raise HTTPException(502, "Documentation index contains an invalid URL") from exc
        return Response(
            content=text, status_code=upstream.status_code,
            media_type="application/json" if is_json else "text/plain",
            headers=_SAFE_HEADERS,
        )
    finally:
        await upstream.aclose()


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
        service without credentials. Safe error responses, including an older
        deployment's 404, are preserved. Text is displayed as inert plain text.
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
            response = get_swagger_ui_html(
                openapi_url=f"{proxy_root}{openapi_document.path}",
                title=f"{entry.name} API documentation",
                swagger_ui_parameters={"supportedSubmitMethods": []},
            )
            response.headers.update(_SAFE_HEADERS)
            return response

        # Never share the control client's cookies, credentials, or connections.
        # Tests may inject a separate documentation client with MockTransport.
        client = getattr(request.app.state, "documentation_client", None)
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(_DOCUMENT_TIMEOUT_SECONDS),
                trust_env=False,
            )

        try:
            return await asyncio.wait_for(
                _fetch_document(client, entry, document), timeout=_DOCUMENT_TIMEOUT_SECONDS,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Timed out reading {document.path} from {equipment_id!r}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not read {document.path} from {equipment_id!r}",
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    return router
