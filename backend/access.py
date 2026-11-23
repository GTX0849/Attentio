"""Who may call which endpoint: FastAPI dependencies that turn a request into a `Principal`.

A caller is one of:

- a signed-in account (`Authorization: Bearer <token>`), with a role and, for students and teachers,
  the register number or teacher id it is linked to;
- the service key (`X-Attentio-Key`), used by the worker and the simulator;
- "open", when the app is built with `auth_required=False` (the logic tests). Every check passes.

A missing or unknown token is 401; a valid caller who may not do this is 403. With no service key
configured the header is ignored and only an admin token passes the machine endpoints.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend import auth
from backend.models import Account, LoginSession


def get_session(request: Request) -> Iterator[Session]:
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

SERVICE_KEY_HEADER = "X-Attentio-Key"
SIGN_IN_REQUIRED = "Sign in required"
NO_ACCESS = "Your account cannot use this"


@dataclass(frozen=True)
class Principal:
    kind: Literal["account", "service", "open"]
    account_id: int | None = None
    role: str | None = None
    email: str | None = None
    student_register_no: str | None = None
    teacher_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "open" or self.role == "admin"

    @property
    def is_service(self) -> bool:
        return self.kind == "service"


def forbidden() -> HTTPException:
    return HTTPException(403, NO_ACCESS)


class Access:
    """The dependencies of one app. `required=False` makes every one of them let the caller through.

    Use them as `Depends(access.admin)` and so on; each returns the `Principal`.
    """

    def __init__(self, *, required: bool, service_key: str | None, clock: Callable[[], datetime]) -> None:
        self.required = required
        self._service_key = service_key or None
        self._clock = clock

        def caller(
            session: SessionDep,
            authorization: Annotated[str | None, Header()] = None,
            service_key: Annotated[str | None, Header(alias=SERVICE_KEY_HEADER)] = None,
        ) -> Principal:
            """Whoever is calling, or 401 when nobody valid is."""
            if not self.required:
                return Principal(kind="open")
            if self._is_service(service_key):
                return Principal(kind="service")
            principal = self._account(session, authorization)
            if principal is None:
                raise HTTPException(401, SIGN_IN_REQUIRED, headers={"WWW-Authenticate": "Bearer"})
            return principal

        Who = Annotated[Principal, Depends(caller)]

        def signed_in(who: Who) -> Principal:
            """Any account, of any role (not the service key)."""
            if who.kind == "service":
                raise forbidden()
            return who

        def admin(who: Who) -> Principal:
            """An admin account."""
            if not who.is_admin:
                raise forbidden()
            return who

        def admin_or_service(who: Who) -> Principal:
            """An admin account or the service key: the machine endpoints."""
            if not (who.is_admin or who.is_service):
                raise forbidden()
            return who

        def staff_or_service(who: Who) -> Principal:
            """An admin or a teacher account, or the service key. Students are refused."""
            if not (who.is_admin or who.is_service or who.role == "teacher"):
                raise forbidden()
            return who

        def student_self(register_no: str, who: Who) -> Principal:
            """The student whose register number is in the path, and nobody else (not even an admin)."""
            if who.kind != "open" and not (who.role == "student" and who.student_register_no == register_no):
                raise forbidden()
            return who

        def teacher_self(teacher_id: int, who: Who) -> Principal:
            """The teacher whose id is in the path, and nobody else (not even an admin)."""
            check_teacher(who, teacher_id)
            return who

        self.caller = caller
        self.signed_in = signed_in
        self.admin = admin
        self.admin_or_service = admin_or_service
        self.staff_or_service = staff_or_service
        self.student_self = student_self
        self.teacher_self = teacher_self

    def _is_service(self, supplied: str | None) -> bool:
        return bool(self._service_key and supplied and auth.same_secret(supplied, self._service_key))

    def _account(self, session: Session, authorization: str | None) -> Principal | None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        found = session.execute(
            select(LoginSession, Account)
            .join(Account, Account.id == LoginSession.account_id)
            .where(LoginSession.token_hash == auth.hash_token(token.strip()))
        ).first()
        if found is None:
            return None
        login, account = found
        if login.revoked or login.expires_at <= self._clock():
            return None
        return Principal(
            kind="account",
            account_id=account.id,
            role=account.role,
            email=account.email,
            student_register_no=account.student_register_no,
            teacher_id=account.teacher_id,
        )


def check_teacher(who: Principal, teacher_id: int, *, also_admin: bool = False, also_service: bool = False) -> None:
    """Raise 403 unless `who` is that teacher (or, when allowed, an admin or the service key)."""
    if who.kind == "open":
        return
    if who.role == "teacher" and who.teacher_id == teacher_id:
        return
    if also_admin and who.is_admin:
        return
    if also_service and who.is_service:
        return
    raise forbidden()
