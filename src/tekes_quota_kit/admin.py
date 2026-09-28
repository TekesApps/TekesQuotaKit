"""Authenticated administration views and one-time client provisioning."""

from __future__ import annotations

import hmac
import json
import re
from datetime import datetime
from importlib.resources import files
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .core import QuotaKit
from .models import (
    Assignment,
    Client,
    Ledger,
    Level,
    Limit,
    Quota,
    Service,
    ServiceMember,
    Token,
    TokenItem,
    Usage,
)

TABLES = {
    model.__tablename__: model
    for model in (
        Client,
        Level,
        Quota,
        Limit,
        Assignment,
        Service,
        ServiceMember,
        Usage,
        Token,
        TokenItem,
        Ledger,
    )
}
PRIVATE_COLUMNS = {"key_hash", "token_hash"}
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}
SAFE_CODE = re.compile(r"^[A-Za-z0-9._-]+$")


def _asset(name: str) -> str:
    return files("tekes_quota_kit").joinpath("admin_assets", name).read_text(encoding="utf-8")


class ProvisionRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    service_code: str = Field(min_length=1, max_length=64)
    role: str = Field(pattern=r"^(issuer|provider|consumer)$")
    base_url: str = Field(min_length=8, max_length=512)
    rotate: bool = False


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value)
    ):
        raise HTTPException(status_code=400, detail="Invalid API base URL")
    return value.rstrip("/")


def _client_guide(
    client_id: str,
    key: str,
    config: ProvisionRequest,
    service: Service,
    metering_mode: str | None,
    members: list[dict],
) -> str:
    """Produce an agent-readable integration contract and the one plaintext client key."""
    base = _validated_base_url(config.base_url)
    values = {
        "schema": "tekes-quotakit-client/v1",
        "client_id": client_id,
        "tenant_id": config.tenant_id,
        "service_code": config.service_code,
        "role": config.role,
        "api_base_url": base,
        "client_key": key,
        "service_id": service.id,
        "service_kind": service.service_kind,
        "redemption_mode": service.redemption_mode,
        "quota_code": service.quota_code,
        "metering_mode": metering_mode,
        "charge_units": service.charge_units,
        "session_ttl_seconds": service.session_ttl_seconds,
        "configured_children": members,
    }
    lines = [
        f"# TekesQuotaKit client: {client_id}",
        "",
        "## Instructions for the client agent",
        "",
        "This is the complete integration contract for this one client credential.",
        "Read the configuration below and implement only the permitted flow for this role.",
        "Move `client_key` into the trusted backend's secret store or environment.",
        "Never commit this file or key, echo it in logs, or expose it to a browser or miniapp.",
        "Do not call admin APIs or use the Kit deployment's admin key or token secret.",
        "If this file is lost, ask an administrator to rotate the client; Kit stores only a hash.",
        "",
        "## Exact configuration values",
        "",
        "```json",
        json.dumps(values, ensure_ascii=False, indent=2),
        "```",
        "",
        "`client_key` is the HTTP Bearer credential. It is not a use token.",
        "A use token is returned by `redeem` or `issue` for one subject and operation.",
        "`configured_children` lists current parent-child mappings; `max_uses: null` is unlimited.",
        "Kit snapshots the mappings at redeem, so use that response's `authorized_services`",
        "when deciding which child may be used by a particular token.",
        "",
        "## Values supplied at runtime",
        "",
        "| Value | Source |",
        "| --- | --- |",
        "| `subject_id` | Positive integer user ID from the client backend's authentication |",
        "| `X-Subject-ID` | The same authenticated user ID on use or balance requests |",
        "| session `request_key` | Stable business session ID; retries reuse the same key |",
        "| attempt `request_key` | Stable ID of one child attempt; a real repeat gets a new key |",
        "| `token` | The preceding Kit redeem or issue response; retain per session |",
        "| `child_service_code` | A code from that token's `authorized_services` |",
        "| `duration_seconds` | Optional 60–86400 seconds chosen by the client backend |",
        "",
        "## Permitted flow",
        "",
    ]
    if service.quota_code is None:
        lines.append(
            "This Service has no direct Quota. A parent Service credential must redeem it."
        )
    elif metering_mode == "reported_usage":
        if config.role in {"issuer", "consumer"}:
            lines.extend(
                [
                    f"1. `POST {base}/v1/token` with the Bearer credential.",
                    "   Body: `subject_id` from authentication, the integer `service_id` above,",
                    "   and one stable business `request_key`. Save the returned token.",
                ]
            )
        if config.role in {"provider", "consumer"}:
            lines.extend(
                [
                    f"2. `POST {base}/v1/token/settle` with the Bearer credential.",
                    "   Body: the issued `token` and the actual nonnegative `consumed_units`.",
                ]
            )
    elif service.redemption_mode == "durable":
        if config.role in {"provider", "consumer"}:
            lines.extend(
                [
                    f"1. `POST {base}/v1/redeem` with the Bearer credential.",
                    "   Body: authenticated `subject_id`, the `service_code` above,",
                    "   a stable session `request_key`; optional `duration_seconds`.",
                    "   Success charges `charge_units`. Save `token` and `authorized_services`.",
                    f"2. `POST {base}/v1/use` before each child operation.",
                    "   Headers: Bearer credential and `X-Subject-ID` for the same user.",
                    "   Body: token, authorized `child_service_code`, attempt `request_key`.",
                    "   Repeated attempts use new keys. `use` never charges Quota again.",
                    f"3. `POST {base}/v1/stop` with the Bearer credential and token.",
                    "   This closes the token. If it expires first, later `use` fails.",
                ]
            )
        else:
            lines.append("Durable redeem/use/stop require a provider or consumer role.")
    elif service.redemption_mode == "instant":
        if config.role in {"provider", "consumer"}:
            lines.extend(
                [
                    f"`POST {base}/v1/redeem` with the Bearer credential.",
                    "Body: authenticated `subject_id`, the `service_code` above,",
                    "and a stable business `request_key`. Success charges one use.",
                    "The same key returns 409 `already_redeemed` with the original token.",
                ]
            )
        else:
            lines.append("Instant redeem requires a provider or consumer credential.")
    if config.role in {"issuer", "consumer"}:
        lines.append(f"`GET {base}/v1/quota` with `X-Subject-ID` reads display balance only.")
    lines.extend(
        [
            "",
            "Stop the business operation when Kit rejects admission or use.",
            "A balance query never grants permission. Never invent a subject ID or a use token.",
            "This client uses only its client key. Admin key and token secret stay with Kit.",
            "",
        ]
    )
    return "\n".join(lines)


def create_admin_router(kit: QuotaKit, admin_key: str) -> APIRouter:
    router = APIRouter()

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not hmac.compare_digest(supplied, admin_key):
            raise HTTPException(status_code=401, detail="Invalid admin credential")

    @router.get("/admin", response_class=HTMLResponse)
    def page() -> HTMLResponse:
        return HTMLResponse(
            _asset("app.html"),
            headers={
                **NO_STORE,
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self' blob:; base-uri 'none'; form-action 'none'"
                ),
            },
        )

    @router.get("/admin/app.js")
    def script() -> Response:
        return Response(_asset("app.js"), media_type="text/javascript", headers=NO_STORE)

    @router.get("/admin/app.css")
    def stylesheet() -> Response:
        return Response(_asset("app.css"), media_type="text/css", headers=NO_STORE)

    @router.get("/v1/admin/tables", dependencies=[Depends(authenticate)])
    def table_names() -> dict:
        return {"tables": list(TABLES)}

    @router.get("/v1/admin/tables/{name}", dependencies=[Depends(authenticate)])
    def table_rows(
        name: str,
        tenant: str = Query(min_length=1, max_length=64),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        model = TABLES.get(name)
        if model is None:
            raise HTTPException(status_code=404, detail="Unknown table")
        columns = [
            column.name for column in model.__table__.columns if column.name not in PRIVATE_COLUMNS
        ]
        with kit.sessions() as db:
            query = select(model)
            if model is TokenItem:
                query = query.join(Token, Token.id == TokenItem.token_id).where(
                    Token.tenant_id == tenant
                )
            else:
                query = query.where(model.tenant_id == tenant)
            count = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
            rows = db.scalars(query.order_by(model.id.desc()).offset(offset).limit(limit)).all()
        return {
            "table": name,
            "columns": columns,
            "total": count,
            "offset": offset,
            "rows": [
                {
                    column: (value.isoformat() + "Z" if isinstance(value, datetime) else value)
                    for column in columns
                    if (value := getattr(row, column)) is not None
                }
                for row in rows
            ],
        }

    @router.post("/v1/admin/clients/{client_id}/provision", dependencies=[Depends(authenticate)])
    def provision(client_id: str, config: ProvisionRequest) -> Response:
        if not all(
            SAFE_CODE.fullmatch(value)
            for value in (client_id, config.tenant_id, config.service_code)
        ):
            raise HTTPException(
                status_code=400,
                detail="Codes may contain letters, numbers, dot, dash, underscore",
            )
        base = _validated_base_url(config.base_url)
        key, service = kit.provision_client(
            client_id,
            config.tenant_id,
            config.service_code,
            config.role,
            rotate=config.rotate,
        )
        config.base_url = base
        with kit.sessions() as db:
            quota = db.scalar(
                select(Quota).where(
                    Quota.tenant_id == config.tenant_id,
                    Quota.quota_code == service.quota_code,
                )
            )
            metering_mode = quota.metering_mode if quota else None
            members = [
                {
                    "child_service_code": row.child_service_code,
                    "max_uses": row.max_uses,
                }
                for row in db.scalars(
                    select(ServiceMember)
                    .where(
                        ServiceMember.tenant_id == config.tenant_id,
                        ServiceMember.parent_service_code == config.service_code,
                    )
                    .order_by(ServiceMember.child_service_code)
                )
            ]
        guide = _client_guide(client_id, key, config, service, metering_mode, members)
        return Response(
            guide,
            media_type="text/markdown; charset=utf-8",
            headers={
                **NO_STORE,
                "Content-Disposition": f'attachment; filename="{client_id}-quotakit.md"',
            },
        )

    return router
