from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .admin import create_admin_router
from .admin_auth import AdminAccounts, require_admin_factory
from .core import MEMBERSHIP_ROLE, QuotaError, QuotaKit
from .models import Client


class ClientConfig(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=32)
    tenant_id: str = Field(min_length=1, max_length=64)
    service_code: str = Field(min_length=1, max_length=64)
    role: Literal["issuer", "provider", "consumer"]


class QuotaConfig(BaseModel):
    unit_code: str = Field(min_length=1, max_length=32)
    metering_mode: Literal["per_use", "reported_usage"]


class ServiceConfig(BaseModel):
    quota_code: str | None = Field(default=None, min_length=1, max_length=64)
    service_kind: Literal["atomic", "composite"] = "atomic"
    redemption_mode: Literal["instant", "durable"] | None = None
    charge_units: int | None = Field(default=None, gt=0)
    session_ttl_seconds: int | None = Field(default=None, ge=60, le=86400)


class ServiceMemberConfig(BaseModel):
    max_uses: int | None = Field(default=None, gt=0, le=2147483647)


class LimitConfig(BaseModel):
    limit_mode: Literal["finite", "unlimited"]
    limit_value: int | None = Field(default=None, ge=0)
    period_kind: Literal["day", "week", "month", "level_term"]
    timezone: str = "Asia/Shanghai"


class MemberRequest(BaseModel):
    level_code: str = Field(min_length=1, max_length=64)
    expires_at: datetime | None = None
    renew_term: bool = False


class MemberItem(MemberRequest):
    subject_id: int = Field(gt=0, le=9223372036854775807, strict=True)


class MemberBatch(BaseModel):
    items: list[MemberItem] = Field(min_length=1, max_length=500)


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class AssignmentConfig(BaseModel):
    level_code: str = Field(min_length=1, max_length=64)
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    renew_term: bool = False


class IssueRequest(BaseModel):
    subject_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    service_id: int = Field(gt=0)
    request_key: str | None = Field(default=None, min_length=1, max_length=128)


class RedeemRequest(BaseModel):
    subject_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    service_id: int | None = Field(default=None, gt=0)
    service_code: str | None = Field(default=None, min_length=1, max_length=64)
    request_key: str | None = Field(default=None, min_length=1, max_length=128)
    duration_seconds: int | None = Field(default=None, ge=60, le=86400)


class TokenRequest(BaseModel):
    token: str = Field(min_length=1)


class SettleRequest(TokenRequest):
    consumed_units: int | None = Field(default=None, ge=0)


class BeginRequest(BaseModel):
    subject_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    service_code: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=128)
    duration_seconds: int | None = Field(default=None, ge=60, le=86400)


class UseRequest(TokenRequest):
    child_service_code: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=128)


def create_app(kit: QuotaKit, admin_key: str) -> FastAPI:
    if len(admin_key) < 32:
        raise ValueError("Admin key must be at least 32 characters")
    app = FastAPI(title="TekesQuotaKit", version="0.7.1")

    @app.exception_handler(QuotaError)
    async def quota_error(_request, exc: QuotaError):
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "message": exc.message, **exc.details},
        )

    def bearer(authorization: Annotated[str | None, Header()] = None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Bearer credential required")
        return authorization.removeprefix("Bearer ")

    accounts = AdminAccounts(kit)
    admin = require_admin_factory(accounts, admin_key)

    def issuer(key: Annotated[str, Depends(bearer)]) -> Client:
        return kit.client(key, "issuer")

    def membership(key: Annotated[str, Depends(bearer)]) -> Client:
        return kit.client(key, MEMBERSHIP_ROLE)

    def provider(key: Annotated[str, Depends(bearer)]) -> Client:
        return kit.client(key, "provider")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.put("/v1/admin/clients/{client_id}", dependencies=[Depends(admin)])
    def put_client(client_id: str, payload: ClientConfig) -> dict:
        if client_id != payload.client_id:
            raise HTTPException(status_code=400, detail="Client ID mismatch")
        kit.put_client(
            payload.client_id, payload.key, payload.tenant_id, payload.service_code, payload.role
        )
        return {"client_id": client_id}

    @app.delete("/v1/admin/tenants/{tenant}/clients/{client_id}", dependencies=[Depends(admin)])
    def delete_client(tenant: str, client_id: str) -> dict:
        kit.delete_client(client_id, tenant)
        return {"client_id": client_id, "deleted": True}

    @app.put("/v1/admin/tenants/{tenant}/quotas/{quota_code}", dependencies=[Depends(admin)])
    def put_quota(tenant: str, quota_code: str, payload: QuotaConfig) -> dict:
        kit.put_quota(tenant, quota_code, payload.unit_code, payload.metering_mode)
        return {"quota_code": quota_code}

    @app.put("/v1/admin/tenants/{tenant}/levels/{level_code}", dependencies=[Depends(admin)])
    def put_level(tenant: str, level_code: str) -> dict:
        kit.put_level(tenant, level_code)
        return {"level_code": level_code}

    @app.put("/v1/admin/tenants/{tenant}/services/{service_code}", dependencies=[Depends(admin)])
    def put_service(tenant: str, service_code: str, payload: ServiceConfig) -> dict:
        service_id = kit.put_service(
            tenant,
            service_code,
            payload.quota_code,
            kind=payload.service_kind,
            redemption_mode=payload.redemption_mode,
            charge_units=payload.charge_units,
            session_ttl_seconds=payload.session_ttl_seconds,
        )
        return {"service_id": service_id, "service_code": service_code}

    @app.put(
        "/v1/admin/tenants/{tenant}/services/{parent}/members/{child}",
        dependencies=[Depends(admin)],
    )
    def put_service_member(
        tenant: str, parent: str, child: str, payload: ServiceMemberConfig
    ) -> dict:
        kit.put_service_member(tenant, parent, child, payload.max_uses)
        return {
            "parent_service_code": parent,
            "child_service_code": child,
            "max_uses": payload.max_uses,
        }

    @app.delete(
        "/v1/admin/tenants/{tenant}/services/{parent}/members/{child}",
        dependencies=[Depends(admin)],
    )
    def delete_service_member(tenant: str, parent: str, child: str) -> dict:
        kit.delete_service_member(tenant, parent, child)
        return {"deleted": True}

    @app.put(
        "/v1/admin/tenants/{tenant}/levels/{level_code}/limits/{quota_code}",
        dependencies=[Depends(admin)],
    )
    def put_limit(tenant: str, level_code: str, quota_code: str, payload: LimitConfig) -> dict:
        kit.put_limit(
            tenant,
            level_code,
            quota_code,
            payload.limit_mode,
            payload.limit_value,
            payload.period_kind,
            payload.timezone,
        )
        return {"level_code": level_code, "quota_code": quota_code}

    @app.put(
        "/v1/admin/tenants/{tenant}/subjects/{subject_id}/level", dependencies=[Depends(admin)]
    )
    def assign(tenant: str, subject_id: int, payload: AssignmentConfig) -> dict:
        expiry = payload.expires_at
        if expiry is not None and expiry.tzinfo is not None:
            expiry = expiry.astimezone(UTC).replace(tzinfo=None)
        effective = payload.effective_at
        if effective is not None and effective.tzinfo is not None:
            effective = effective.astimezone(UTC).replace(tzinfo=None)
        kit.assign(
            tenant,
            subject_id,
            payload.level_code,
            expiry,
            effective_at=effective,
            renew_term=payload.renew_term,
        )
        return {"subject_id": subject_id, "level_code": payload.level_code}

    @app.post("/v1/begin")
    def begin(payload: BeginRequest, caller: Annotated[Client, Depends(provider)]) -> dict:
        return kit.begin(
            caller,
            payload.subject_id,
            payload.service_code,
            payload.request_key,
            payload.duration_seconds,
        )

    @app.post("/v1/use")
    def use(
        payload: UseRequest,
        caller: Annotated[Client, Depends(provider)],
        x_subject_id: Annotated[int, Header(gt=0, le=9223372036854775807)],
    ) -> dict:
        return kit.use(
            payload.token, caller, x_subject_id, payload.child_service_code, payload.request_key
        )

    @app.post("/v1/close")
    def close(
        payload: TokenRequest,
        caller: Annotated[Client, Depends(provider)],
        x_subject_id: Annotated[int, Header(gt=0, le=9223372036854775807)],
    ) -> dict:
        return kit.close(payload.token, caller, x_subject_id)

    @app.post("/v1/stop")
    def stop(payload: TokenRequest, caller: Annotated[Client, Depends(provider)]) -> dict:
        return kit.settle(payload.token, caller, None)

    @app.post("/v1/token")
    def issue(payload: IssueRequest, caller: Annotated[Client, Depends(issuer)]) -> dict:
        return kit.issue(caller, payload.subject_id, payload.service_id, payload.request_key)

    @app.post("/v1/redeem")
    def redeem(
        payload: RedeemRequest,
        caller: Annotated[Client, Depends(provider)],
    ) -> dict:
        if (payload.service_id is None) == (payload.service_code is None):
            raise HTTPException(status_code=400, detail="Provide exactly one Service identifier")
        service = payload.service_id if payload.service_id is not None else payload.service_code
        return kit.redeem(
            caller, payload.subject_id, service, payload.request_key, payload.duration_seconds
        )

    @app.post("/v1/token/settle")
    def settle(
        payload: SettleRequest,
        caller: Annotated[Client, Depends(provider)],
    ) -> dict:
        return kit.settle(payload.token, caller, payload.consumed_units)

    @app.post("/v1/token/refund")
    def refund(
        payload: TokenRequest,
        caller: Annotated[Client, Depends(provider)],
        x_subject_id: Annotated[int, Header(gt=0, le=9223372036854775807)],
    ) -> dict:
        return kit.refund(payload.token, caller, x_subject_id)

    @app.post("/v1/token/status")
    def token_status(
        payload: TokenRequest,
        caller: Annotated[Client, Depends(provider)],
        x_subject_id: Annotated[int, Header(gt=0, le=9223372036854775807)],
    ) -> dict:
        return kit.token_status(payload.token, caller, x_subject_id)

    @app.get("/v1/quota")
    def balance(
        x_subject_id: Annotated[int, Header(gt=0, le=9223372036854775807)],
        caller: Annotated[Client, Depends(issuer)],
    ) -> dict:
        return kit.balance(caller, x_subject_id)

    @app.get("/v1/tokens/unsettled")
    def unsettled(caller: Annotated[Client, Depends(provider)]) -> list[dict]:
        return kit.unsettled(caller)

    # Member sync: a `membership` client keeps the member list of its own business system.
    @app.put("/v1/members/{subject_id}")
    def put_member(
        subject_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
        payload: MemberRequest,
        caller: Annotated[Client, Depends(membership)],
    ) -> dict:
        kit.assign(
            caller.tenant_id,
            subject_id,
            payload.level_code,
            _utc_naive(payload.expires_at),
            renew_term=payload.renew_term,
        )
        return {"subject_id": subject_id, "member": kit.membership(caller.tenant_id, subject_id)}

    @app.get("/v1/members/{subject_id}")
    def get_member(
        subject_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
        caller: Annotated[Client, Depends(membership)],
    ) -> dict:
        return {"subject_id": subject_id, "member": kit.membership(caller.tenant_id, subject_id)}

    @app.delete("/v1/members/{subject_id}")
    def end_member(
        subject_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
        caller: Annotated[Client, Depends(membership)],
    ) -> dict:
        return {"subject_id": subject_id, "ended": kit.end_membership(caller.tenant_id, subject_id)}

    @app.post("/v1/members/batch")
    def put_members(payload: MemberBatch, caller: Annotated[Client, Depends(membership)]) -> dict:
        results = []
        for item in payload.items:
            try:
                kit.assign(
                    caller.tenant_id,
                    item.subject_id,
                    item.level_code,
                    _utc_naive(item.expires_at),
                    renew_term=item.renew_term,
                )
                results.append({"subject_id": item.subject_id, "ok": True})
            except QuotaError as exc:
                results.append(
                    {
                        "subject_id": item.subject_id,
                        "ok": False,
                        "code": exc.code,
                        "message": exc.message,
                    }
                )
        failed = sum(1 for r in results if not r["ok"])
        return {"total": len(results), "failed": failed, "results": results}

    app.include_router(create_admin_router(kit, accounts, admin))
    return app
