"""Authenticated administration views and one-time client provisioning."""

from __future__ import annotations

import json
import re
from datetime import datetime
from importlib.resources import files
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import __version__
from .admin_auth import (
    COOKIE,
    SESSION_TTL,
    AdminAccounts,
    cookie_path,
    cookie_secure,
)
from .core import MEMBERSHIP_ROLE, QuotaKit, utc_now
from .models import (
    Assignment,
    Client,
    Ledger,
    Level,
    Limit,
    Quota,
    Service,
    ServiceMember,
    Tenant,
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


ASSET_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.(js|css|svg|woff2)$")
ASSET_TYPES = {
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "svg": "image/svg+xml",
    "woff2": "font/woff2",
}
# antd injects component styles at runtime, so styles need 'unsafe-inline'. Scripts do not.
PAGE_CSP = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
}


def _asset_bytes(name: str) -> bytes:
    return files("tekes_quota_kit").joinpath("admin_assets", name).read_bytes()


def _asset(name: str) -> str:
    return _asset_bytes(name).decode("utf-8")


class TenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # Omitted or null keeps the stored definition.
    subject_id_definition: str | None = Field(default=None, max_length=500)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class ProvisionRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    # Ignored for the `membership` role, which is not bound to a Service.
    service_code: str = Field(default="", max_length=64)
    role: str = Field(pattern=r"^(issuer|provider|consumer|membership)$")
    base_url: str = Field(min_length=8, max_length=512)
    rotate: bool = False
    # What `subject_id` means in this business system. Saved on the business system; required
    # until one is stored, then optional (the stored one is reused).
    subject_id_definition: str | None = Field(default=None, max_length=500)


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


# Both contracts carry the same rule, so the member-sync side and the Service side of a
# business system are told the same thing: one user ID scheme, chosen by the business system.
SUBJECT_ID_RULE = (
    "One positive integer per person, chosen by the business system; member sync and every "
    "Service request must send the same ID for the same person"
)
def _user_id_rule(definition: str) -> list[str]:
    return [
        "## User ID rule (read first)",
        "",
        "Every request about a user carries that user's ID as `subject_id` (in the body) or",
        "`X-Subject-ID` (in a header). Kit does not define or look up user IDs. The business",
        "system chooses which ID to use, for example the primary key of its user table. The one",
        "rule is that it is the same ID everywhere.",
        "",
        "In this business system, `subject_id` is defined as:",
        "",
        *[f"> {line}" for line in definition.splitlines()],
        "",
        "`subject_id` must be a positive integer. If the definition names a string identifier,",
        "such as a WeChat union_id or openid, send the integer ID the business system stores",
        "for that person instead, and use that same integer everywhere.",
        "",
        "Rules:",
        "",
        "- Member registration and every Service request (redeem, issue, use, settle, balance)",
        "  must send the same ID for the same person. Kit matches them by this number only.",
        "- Use exactly one kind of ID. Do not mix in an openid, phone number, member card number,",
        "  or order ID in some places. Convert any other identifier to the chosen ID before",
        "  calling Kit.",
        "- The ID must be a positive integer, stable for the life of the account, and never",
        "  reused for another person.",
        "- If the member-sync side and the Service side are different services or teams, agree",
        "  on this ID before going live.",
        "",
        "A mismatch is silent until a request is made: the user is registered as a member under",
        "one ID, the Service request arrives with another, and Kit rejects it with HTTP 409",
        "`no_level` as if the user were not a member.",
        "",
        "Check before go-live: register one test user through member sync, then make one Service",
        "request with the same `subject_id`. Success means the IDs line up; `no_level` means they",
        "do not.",
        "",
    ]


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _base_url_note(base: str) -> list[str]:
    """Spell out that a loopback `api_base_url` is the production server's, not a workstation's."""
    host = (urlsplit(base).hostname or "").lower()
    if host not in LOOPBACK_HOSTS and not host.startswith("127."):
        return []
    return [
        "",
        f"**About `api_base_url` ({base}).** This is a loopback address on the business",
        "system's production server, where Kit runs next to the business backend. Only a",
        "process on that same server can reach it. It is not the `127.0.0.1` of a developer's",
        "workstation: calling it from a laptop reaches nothing, or whatever happens to listen",
        "there. Deploy the code that uses this credential on that production server. For local",
        "development, run your own Kit instance or ask the administrator for a test environment",
        "address; do not expose the production Kit to the internet to make local calls work.",
    ]


def _client_guide(
    client_id: str,
    key: str,
    config: ProvisionRequest,
    service: Service,
    metering_mode: str | None,
    members: list[dict],
    subject_id_definition: str,
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
        "subject_id_definition": subject_id_definition,
        "subject_id_rule": SUBJECT_ID_RULE,
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
        *_user_id_rule(subject_id_definition),
        "## Exact configuration values",
        "",
        "```json",
        json.dumps(values, ensure_ascii=False, indent=2),
        "```",
        *_base_url_note(base),
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
        "## Prerequisite: the user must be a member",
        "",
        "Kit only admits a `subject_id` that holds an active Level in this business system, and",
        "matches it to member registration by the ID rule above.",
        "Any other user is rejected with HTTP 409 `no_level`; treat that as \"not a member\".",
        "This client cannot add members. The business system keeps the member list in Kit",
        "through its separate member-sync client (role `membership`).",
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


def _download(client_id: str, guide: str) -> Response:
    return Response(
        guide,
        media_type="text/markdown; charset=utf-8",
        headers={
            **NO_STORE,
            "Content-Disposition": f'attachment; filename="{client_id}-quotakit.md"',
        },
    )


def _subject_id_definition(kit: QuotaKit, tenant: str, supplied: str | None) -> str:
    """Return the business system's definition, setting it from `supplied` the first time.

    Every contract of a business system quotes the same definition, so the member-sync side
    and the Service side are told the same thing about user IDs. Once set, it changes only
    through PUT /v1/admin/tenants/{tenant} (the console's 业务系统 page), never by issuing.
    """
    text = (supplied or "").strip()
    now = utc_now()
    with kit.sessions.begin() as db:
        row = db.scalar(select(Tenant).where(Tenant.tenant_id == tenant))
        stored = row.subject_id_definition if row is not None else None
        if stored and text and text != stored:
            raise HTTPException(
                status_code=409,
                detail="用户 ID 定义已设定，签发时不能修改。请到“用户与接入 → 业务系统”修改",
            )
        if text and not stored:
            if row is None:
                row = Tenant(tenant_id=tenant, name=tenant, created_at=now, updated_at=now)
                db.add(row)
            row.subject_id_definition, row.updated_at = text, now
            stored = text
    if not stored:
        raise HTTPException(
            status_code=400,
            detail="请填写用户 ID 定义：说明 subject_id 对应业务系统里的哪个整数 ID",
        )
    return stored


def _levels(kit: QuotaKit, tenant: str) -> list[dict]:
    """Levels of a business system with the Limits each grants, for the member-sync contract."""
    with kit.sessions() as db:
        limits = db.scalars(select(Limit).where(Limit.tenant_id == tenant)).all()
        return [
            {
                "level_code": code,
                "limits": [
                    {
                        "quota_code": limit.quota_code,
                        "limit_mode": limit.limit_mode,
                        "limit_value": limit.limit_value,
                        "period_kind": limit.period_kind,
                        "timezone": limit.timezone,
                    }
                    for limit in limits
                    if limit.level_code == code
                ],
            }
            for code in db.scalars(
                select(Level.level_code).where(Level.tenant_id == tenant).order_by(Level.level_code)
            )
        ]


def _membership_guide(
    client_id: str,
    key: str,
    tenant_id: str,
    base_url: str,
    levels: list[dict],
    subject_id_definition: str,
) -> str:
    """Integration contract for a member-sync client, which maintains subject Levels."""
    base = _validated_base_url(base_url)
    values = {
        "schema": "tekes-quotakit-membership/v1",
        "client_id": client_id,
        "tenant_id": tenant_id,
        "role": "membership",
        "api_base_url": base,
        "client_key": key,
        "subject_id_definition": subject_id_definition,
        "subject_id_rule": SUBJECT_ID_RULE,
        "levels": levels,
    }
    example_expiry = "2026-11-08T00:00:00+08:00"
    lines = [
        f"# TekesQuotaKit member sync: {client_id}",
        "",
        "## Instructions for the client agent",
        "",
        "This is the complete contract for one member-sync credential. With it the business",
        "system keeps Kit's member list in step with its own memberships. Kit admits a user to",
        "a Service only while the user holds an active Level here.",
        "Move `client_key` into the trusted backend's secret store or environment.",
        "Never commit this file or key, echo it in logs, or expose it to a browser or miniapp.",
        "This credential can only manage members of this business system. It cannot call",
        "Services and cannot change rules.",
        "",
        *_user_id_rule(subject_id_definition),
        "## Exact configuration values",
        "",
        "```json",
        json.dumps(values, ensure_ascii=False, indent=2, default=str),
        "```",
        *_base_url_note(base),
        "",
        "`levels` lists the Level codes you may assign and the Limits each one grants.",
        "",
        "## What to send",
        "",
        "Send only users who hold a membership, not every user. `subject_id` follows the user",
        "ID rule above: the same ID the Service clients send for that person.",
        "`expires_at` is ISO 8601 with a timezone offset, for example",
        f"`{example_expiry}`; omit it for a membership without an end date.",
        "",
        "`renew_term` marks a real purchase or renewal. Set it to `true` on those events. It",
        "starts a new membership term and lets `expires_at` change. Use `false` when you only",
        "re-send the current state, for example during reconciliation. If a `false` request",
        "returns 400 `term_change_requires_renewal`, the expiry differs from Kit's, which means",
        "a renewal happened; send it again with `true`.",
        "",
        "Every call takes `Authorization: Bearer <client_key>`.",
        "",
        "## 1. Initial import, once at go-live",
        "",
        f"`POST {base}/v1/members/batch`, at most 500 items per call:",
        "",
        "```json",
        json.dumps(
            {
                "items": [
                    {
                        "subject_id": 42,
                        "level_code": levels[0]["level_code"] if levels else "member",
                        "expires_at": example_expiry,
                        "renew_term": True,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "The response is `{\"total\": n, \"failed\": k, \"results\": [...]}`, one result per",
        "item with `ok`, and `code` and `message` when it failed. Items are applied one by one,",
        "so a failure does not undo the others. Fix and resend only the failed items.",
        "",
        "## 2. On membership purchase or renewal",
        "",
        f"`PUT {base}/v1/members/{{subject_id}}`",
        "",
        "```json",
        json.dumps(
            {
                "level_code": levels[0]["level_code"] if levels else "member",
                "expires_at": example_expiry,
                "renew_term": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "The response returns the member's current Level, start, and expiry.",
        "",
        "## 3. On refund, cancellation, or account deletion",
        "",
        f"`DELETE {base}/v1/members/{{subject_id}}` ends the membership now. The response",
        "`ended` is `true` if a membership was active and `false` if there was none, so the",
        "call is safe to repeat. History is kept. A membership that simply expires needs no call.",
        "",
        "## 4. Checking one member",
        "",
        f"`GET {base}/v1/members/{{subject_id}}` returns `member: null` when the user has no",
        "active Level, otherwise the Level, start, and expiry. Use it for reconciliation.",
        "",
        "## Errors",
        "",
        "| Status | Code | Meaning |",
        "| --- | --- | --- |",
        "| 401 | `unauthorized` | Wrong key, or the key is not a member-sync credential |",
        "| 404 | `unknown_level` | `level_code` is not one of the Levels above |",
        "| 400 | `invalid_expiry` | `expires_at` is not after now |",
        "| 400 | `term_change_requires_renewal` | Expiry changed without `renew_term: true` |",
        "| 422 | (validation) | Malformed body or `subject_id` |",
        "",
        "Retry on network errors with the same body; all calls are safe to repeat.",
        "",
    ]
    return "\n".join(lines)


def create_admin_router(kit: QuotaKit, accounts: AdminAccounts, require_admin) -> APIRouter:
    router = APIRouter()
    authenticate = require_admin

    @router.get("/admin", response_class=HTMLResponse)
    def page() -> HTMLResponse:
        return HTMLResponse(_asset("index.html"), headers={**NO_STORE, **PAGE_CSP})

    @router.get("/admin/assets/{name}")
    def asset(name: str) -> Response:
        if not ASSET_NAME.fullmatch(name):
            raise HTTPException(status_code=404)
        try:
            body = _asset_bytes(f"assets/{name}")
        except FileNotFoundError:
            raise HTTPException(status_code=404) from None
        media_type = ASSET_TYPES[name.rsplit(".", 1)[1]]
        # Hashed file names are immutable; the page itself is never cached.
        return Response(
            body,
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/v1/admin/session/login")
    def login(payload: LoginRequest, request: Request, response: Response) -> dict:
        if request.headers.get("X-Admin-Request") != "1":
            raise HTTPException(status_code=403, detail="缺少请求保护头")
        path = cookie_path(request)
        token = accounts.login(payload.username.strip(), payload.password)
        response.set_cookie(
            COOKIE,
            token,
            max_age=int(SESSION_TTL.total_seconds()),
            path=path,
            secure=cookie_secure(request),
            httponly=True,
            samesite="strict",
        )
        response.headers.update(NO_STORE)
        return {"data": {**accounts.session_user(token), "via": "session", "version": __version__}}

    @router.get("/v1/admin/session")
    # `require_admin` is a closure, which postponed annotations cannot resolve inside
    # Annotated[...]; a default-argument dependency avoids that.
    def session(user: dict = Depends(require_admin)) -> dict:  # noqa: B008
        # The console shows the version that is actually running.
        return {"data": {**user, "version": __version__}}

    @router.post("/v1/admin/session/logout")
    def logout(
        request: Request,
        response: Response,
        user: dict = Depends(require_admin),  # noqa: B008
    ) -> dict:
        if user["via"] == "session":
            accounts.logout(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path=cookie_path(request))
        return {"data": {"status": "logged_out"}}

    @router.get("/v1/admin/tenants", dependencies=[Depends(authenticate)])
    def tenants() -> dict:
        """Registered business systems, plus tenant IDs that only appear in data."""
        with kit.sessions() as db:
            rows = {t.tenant_id: t for t in db.scalars(select(Tenant))}
            registered = {code: row.name for code, row in rows.items()}
            found = set(registered)
            for model in (Level, Quota, Service, Client, Assignment):
                found.update(db.scalars(select(model.tenant_id).distinct()))
        items = [
            {
                "tenant_id": t,
                "name": registered.get(t),
                "registered": t in registered,
                "subject_id_definition": rows[t].subject_id_definition if t in rows else None,
            }
            for t in sorted(found)
        ]
        return {"tenants": sorted(found), "items": items}

    @router.get(
        "/v1/admin/tenants/{tenant}/subjects/{subject_id}/usage",
        dependencies=[Depends(authenticate)],
    )
    def subject_usage(tenant: str, subject_id: int) -> dict:
        return kit.subject_usage(tenant, subject_id)

    @router.get("/v1/admin/tenants/{tenant}/overview", dependencies=[Depends(authenticate)])
    def overview(tenant: str) -> dict:
        """Counts for the console header. Members are people with an active Level now, not
        assignment rows, which keep history and grow with every renewal or level change."""
        now = utc_now()
        with kit.sessions() as db:

            def count(model) -> int:
                return db.scalar(
                    select(func.count()).select_from(model).where(model.tenant_id == tenant)
                )

            members = db.scalar(
                select(func.count(func.distinct(Assignment.subject_id))).where(
                    Assignment.tenant_id == tenant,
                    Assignment.effective_at <= now,
                    (Assignment.expires_at.is_(None) | (Assignment.expires_at > now)),
                )
            )
            return {
                "services": count(Service),
                "levels": count(Level),
                "clients": count(Client),
                "active_members": members,
            }

    @router.put("/v1/admin/tenants/{tenant}", dependencies=[Depends(authenticate)])
    def register_tenant(tenant: str, payload: TenantRequest) -> dict:
        if len(tenant) > 64 or not SAFE_CODE.fullmatch(tenant):
            raise HTTPException(
                status_code=400,
                detail="代码只能使用字母、数字和 . _ -，最多 64 个字符",
            )
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="请填写业务系统名称")
        now = utc_now()
        with kit.sessions.begin() as db:
            row = db.scalar(select(Tenant).where(Tenant.tenant_id == tenant))
            if row is None:
                row = Tenant(tenant_id=tenant, name=name, created_at=now, updated_at=now)
                db.add(row)
            else:
                row.name, row.updated_at = name, now
            if payload.subject_id_definition is not None:
                row.subject_id_definition = payload.subject_id_definition.strip() or None
            definition = row.subject_id_definition
        return {"tenant_id": tenant, "name": name, "subject_id_definition": definition}

    @router.get("/v1/admin/tables", dependencies=[Depends(authenticate)])
    def table_names() -> dict:
        return {"tables": list(TABLES)}

    @router.get("/v1/admin/tables/{name}", dependencies=[Depends(authenticate)])
    def table_rows(
        name: str,
        request: Request,
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
        # Any other query parameter is an exact-match filter on a visible column, e.g.
        # ?subject_id=42&quota_code=door_open_count.
        filters = []
        for key, raw in request.query_params.items():
            if key in {"tenant", "limit", "offset"}:
                continue
            if key not in columns:
                raise HTTPException(status_code=400, detail=f"Cannot filter by {key}")
            column = model.__table__.columns[key]
            kind = column.type.python_type
            if kind is int:
                if not raw.lstrip("-").isdigit():
                    raise HTTPException(status_code=400, detail=f"{key} must be an integer")
                value = int(raw)
            elif kind is str:
                value = raw
            else:
                raise HTTPException(status_code=400, detail=f"Cannot filter by {key}")
            filters.append(getattr(model, key) == value)
        with kit.sessions() as db:
            query = select(model)
            if model is TokenItem:
                query = query.join(Token, Token.id == TokenItem.token_id).where(
                    Token.tenant_id == tenant
                )
            else:
                query = query.where(model.tenant_id == tenant)
            for condition in filters:
                query = query.where(condition)
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
        codes = [client_id, config.tenant_id]
        if config.role != MEMBERSHIP_ROLE:
            codes.append(config.service_code)
        if not all(SAFE_CODE.fullmatch(value) for value in codes):
            raise HTTPException(
                status_code=400,
                detail="Codes may contain letters, numbers, dot, dash, underscore",
            )
        base = _validated_base_url(config.base_url)
        definition = _subject_id_definition(kit, config.tenant_id, config.subject_id_definition)
        key, service = kit.provision_client(
            client_id,
            config.tenant_id,
            config.service_code,
            config.role,
            rotate=config.rotate,
        )
        config.base_url = base
        if service is None:
            levels = _levels(kit, config.tenant_id)
            return _download(
                client_id, _membership_guide(
                    client_id, key, config.tenant_id, base, levels, definition
                ),
            )
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
        return _download(
            client_id, _client_guide(
                client_id, key, config, service, metering_mode, members, definition
            ),
        )

    return router
