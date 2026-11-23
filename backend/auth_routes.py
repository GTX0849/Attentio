"""Sign-in, sign-out, "who am I" and account management endpoints."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import delete, select, update

from backend import accounts, auth
from backend.access import Access, Principal, SessionDep
from backend.models import Account, LoginSession
from backend.schemas import (
    AccountCreate,
    AccountOut,
    Identity,
    LoginRequest,
    LoginResponse,
    PasswordReset,
)

# One message for an unknown email and a wrong password, so a caller learns nothing about which
# accounts exist.
BAD_LOGIN = "Email or password is incorrect"
TOO_MANY = "Too many attempts. Try again in a few minutes"


def build_router(access: Access, clock: Callable[[], datetime]) -> APIRouter:
    settings = auth.auth_settings()
    limiter = auth.LoginLimiter(settings.max_failures, timedelta(minutes=settings.window_minutes))
    router = APIRouter()

    def identity_of(session: SessionDep, who: Principal) -> Identity:
        assert who.role is not None
        account = session.get(Account, who.account_id)
        assert account is not None
        return Identity(
            role=who.role,  # type: ignore[arg-type]
            email=account.email,
            display_name=accounts.display_name(session, account),
            student_register_no=account.student_register_no,
            teacher_id=account.teacher_id,
        )

    @router.post("/api/auth/login")
    def login(body: LoginRequest, session: SessionDep) -> LoginResponse:
        now = clock()
        key = accounts.normalize_email(body.email)
        if limiter.blocked(key, now):
            raise HTTPException(429, TOO_MANY, headers={"Retry-After": str(int(settings.window_minutes * 60))})
        account = accounts.find_by_email(session, key)
        if account is None:
            auth.verify_decoy(body.password)
        if account is None or not auth.verify_password(body.password, account.password_hash):
            limiter.record_failure(key, now)
            raise HTTPException(401, BAD_LOGIN)
        limiter.reset(key)

        token = auth.new_token()
        expires = now + timedelta(hours=settings.session_hours)
        session.execute(delete(LoginSession).where(LoginSession.expires_at <= now))  # tidy up old ones
        session.add(
            LoginSession(
                account_id=account.id,
                token_hash=auth.hash_token(token),
                created_at=now,
                expires_at=expires,
            )
        )
        session.commit()
        return LoginResponse(
            token=token,
            expires_at=expires,
            role=account.role,  # type: ignore[arg-type]
            email=account.email,
            display_name=accounts.display_name(session, account),
            student_register_no=account.student_register_no,
            teacher_id=account.teacher_id,
        )

    @router.get("/api/auth/me")
    def me(session: SessionDep, who: Annotated[Principal, Depends(access.signed_in)]) -> Identity:
        if who.kind == "open":
            return Identity(role="admin", email="open@attentio.test", display_name="Open access")
        return identity_of(session, who)

    @router.post("/api/auth/logout", status_code=204)
    def logout(
        session: SessionDep,
        who: Annotated[Principal, Depends(access.signed_in)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        """Revoke the token this request was made with."""
        _, _, token = (authorization or "").partition(" ")
        if token.strip():
            session.execute(
                update(LoginSession)
                .where(LoginSession.token_hash == auth.hash_token(token.strip()))
                .values(revoked=True)
            )
            session.commit()
        return Response(status_code=204)

    # ---- accounts ----

    def to_out(session: SessionDep, account: Account) -> AccountOut:
        v = accounts.view_of(session, account)
        return AccountOut(
            id=v.id,
            email=v.email,
            role=v.role,  # type: ignore[arg-type]
            student_register_no=v.student_register_no,
            teacher_id=v.teacher_id,
            linked_name=v.linked_name,
            created_at=v.created_at,
            simulated=v.simulated,
        )

    @router.post("/api/accounts", status_code=201)
    def create_account(
        body: AccountCreate,
        session: SessionDep,
        who: Annotated[Principal, Depends(access.admin_or_service)],
    ) -> AccountOut:
        """Make an account. The service key may only make simulated student and teacher accounts."""
        if who.is_service and (not body.simulated or body.role == "admin"):
            raise HTTPException(403, "The service key can only create simulated student and teacher accounts")
        try:
            account = accounts.create_account(
                session,
                email=body.email,
                password=body.password,
                role=body.role,
                now=clock(),
                student_register_no=body.student_register_no,
                teacher_id=body.teacher_id,
                simulated=body.simulated,
            )
        except accounts.AccountConflict as e:
            raise HTTPException(409, str(e)) from e
        except accounts.AccountError as e:
            raise HTTPException(422, str(e)) from e
        return to_out(session, account)

    @router.get("/api/accounts")
    def list_accounts(
        session: SessionDep, who: Annotated[Principal, Depends(access.admin)]
    ) -> list[AccountOut]:
        rows = session.scalars(select(Account)).all()
        by_id = {a.id: a for a in rows}
        return [to_out(session, by_id[v.id]) for v in accounts.views(session)]

    @router.post("/api/accounts/{account_id}/password", status_code=204)
    def reset_password(
        account_id: int,
        body: PasswordReset,
        session: SessionDep,
        who: Annotated[Principal, Depends(access.admin)],
    ) -> Response:
        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(404, f"Unknown account id: {account_id}")
        try:
            accounts.reset_password(session, account, body.password)
        except accounts.AccountError as e:
            raise HTTPException(422, str(e)) from e
        return Response(status_code=204)

    @router.delete("/api/accounts/{account_id}", status_code=204)
    def delete_account(
        account_id: int, session: SessionDep, who: Annotated[Principal, Depends(access.admin)]
    ) -> Response:
        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(404, f"Unknown account id: {account_id}")
        if account.id == who.account_id:
            raise HTTPException(409, "You cannot delete the account you are signed in with")
        accounts.delete_account(session, account)
        return Response(status_code=204)

    return router
