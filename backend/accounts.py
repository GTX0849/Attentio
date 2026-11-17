"""Accounts: the functions the API and the command line share, and the command line itself.

    python -m backend.accounts create --role admin --email admin@example.edu
    python -m backend.accounts create --role student --email asha@example.edu --student STU001
    python -m backend.accounts create --role teacher --email anita@example.edu --teacher "Dr. Anita Menon"
    python -m backend.accounts list
    python -m backend.accounts reset-password --email asha@example.edu
    python -m backend.accounts delete --email asha@example.edu

The command line talks to the database named by DATABASE_URL, like the backend, and asks for the
password with `getpass` (twice). Passwords are never printed, logged or accepted as arguments.
"""

import argparse
import getpass
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend import auth
from backend.db import init_database
from backend.models import Account, LoginSession, Student, Teacher
from shared.config import database_url

ROLES = ("student", "teacher", "admin")
MIN_PASSWORD_LENGTH = 8
MAX_EMAIL_LENGTH = 254
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    """The request cannot be done; the message is safe to show."""


class AccountConflict(AccountError):
    """The email, student or teacher already has an account."""


@dataclass(frozen=True)
class AccountView:
    """An account with the name of what it links to. Never carries the hash."""

    id: int
    email: str
    role: str
    student_register_no: str | None
    teacher_id: int | None
    linked_name: str | None
    created_at: datetime
    simulated: bool


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters")


def normalize_email(email: str) -> str:
    """The stored form: trimmed and lower-cased."""
    return email.strip().lower()


def check_email(email: str) -> str:
    """The normalized email, or AccountError when it is not a plausible address."""
    cleaned = normalize_email(email)
    if len(cleaned) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(cleaned):
        raise AccountError("Enter an email address such as name@example.edu")
    return cleaned


def find_by_email(session: Session, email: str) -> Account | None:
    return session.scalar(select(Account).where(Account.email == normalize_email(email)))


def display_name(session: Session, account: Account) -> str:
    """The student's or teacher's name when there is one, else the register number or email."""
    if account.student_register_no is not None:
        student = session.get(Student, account.student_register_no)
        return student.name if student and student.name else account.student_register_no
    if account.teacher_id is not None:
        teacher = session.get(Teacher, account.teacher_id)
        return teacher.name if teacher else account.email
    return account.email


def create_account(
    session: Session,
    *,
    email: str,
    password: str,
    role: str,
    now: datetime,
    student_register_no: str | None = None,
    teacher_id: int | None = None,
    simulated: bool = False,
) -> Account:
    """Make an account. A student's links to `student_register_no`, a teacher's to `teacher_id`;
    an admin's to nothing."""
    email = check_email(email)
    if role not in ROLES:
        raise AccountError(f"The role must be one of {', '.join(ROLES)}")
    check_password(password)
    if find_by_email(session, email) is not None:
        raise AccountConflict(f"The email {email} already has an account")

    register_no: str | None = None
    if role == "student":
        if teacher_id is not None:
            raise AccountError("A student account cannot link to a teacher")
        if not student_register_no or not student_register_no.strip():
            raise AccountError("A student account needs the student's register number")
        wanted = student_register_no.strip()
        student = session.scalar(select(Student).where(func.lower(Student.register_no) == wanted.lower()))
        if student is None:
            raise AccountError(f"No student has the register number {wanted}")
        if session.scalar(select(Account.id).where(Account.student_register_no == student.register_no)):
            raise AccountConflict(f"{student.register_no} already has an account")
        register_no = student.register_no
    elif role == "teacher":
        if student_register_no is not None:
            raise AccountError("A teacher account cannot link to a student")
        if teacher_id is None or session.get(Teacher, teacher_id) is None:
            raise AccountError("A teacher account needs an existing teacher")
        if session.scalar(select(Account.id).where(Account.teacher_id == teacher_id)):
            raise AccountConflict("That teacher already has an account")
    elif student_register_no is not None or teacher_id is not None:
        raise AccountError("An admin account links to no student or teacher")

    account = Account(
        email=email,
        role=role,
        password_hash=auth.hash_password(password),
        student_register_no=register_no,
        teacher_id=teacher_id if role == "teacher" else None,
        created_at=now,
        simulated=simulated,
    )
    session.add(account)
    session.commit()
    return account


def views(session: Session) -> list[AccountView]:
    """Every account: admins first, then teachers, then students, each by email."""
    rank = {"admin": 0, "teacher": 1, "student": 2}
    rows = session.scalars(select(Account).order_by(Account.email)).all()
    out = [view_of(session, a) for a in rows]
    return sorted(out, key=lambda v: rank[v.role])


def view_of(session: Session, account: Account) -> AccountView:
    return AccountView(
        id=account.id,
        email=account.email,
        role=account.role,
        student_register_no=account.student_register_no,
        teacher_id=account.teacher_id,
        linked_name=display_name(session, account) if account.role != "admin" else None,
        created_at=account.created_at,
        simulated=account.simulated,
    )


def reset_password(session: Session, account: Account, password: str) -> None:
    """Set a new password and end every session the account has."""
    check_password(password)
    account.password_hash = auth.hash_password(password)
    session.execute(delete(LoginSession).where(LoginSession.account_id == account.id))
    session.commit()


def delete_account(session: Session, account: Account) -> None:
    session.execute(delete(LoginSession).where(LoginSession.account_id == account.id))
    session.delete(account)
    session.commit()


# ---- command line --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.accounts",
        description="Create and manage ClassPulse accounts. The password is asked for, never given as an option.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create an account")
    create.add_argument("--role", required=True, choices=ROLES)
    create.add_argument("--email", required=True, help="the email the person signs in with")
    create.add_argument("--teacher", help="the teacher's name as in the timetable (teacher accounts)")
    create.add_argument("--student", help="the student's register number, e.g. STU001 (student accounts)")
    sub.add_parser("list", help="list accounts (never the passwords)")
    reset = sub.add_parser("reset-password", help="set a new password and end the account's sessions")
    reset.add_argument("--email", required=True)
    remove = sub.add_parser("delete", help="delete an account")
    remove.add_argument("--email", required=True)
    return parser


def _ask_new_password(read_password: Callable[[str], str]) -> str:
    first = read_password("Password: ")
    check_password(first)
    if read_password("Password again: ") != first:
        raise AccountError("The two passwords do not match")
    return first


def _account(session: Session, email: str) -> Account:
    account = find_by_email(session, email)
    if account is None:
        raise AccountError(f"No account has the email {normalize_email(email)}")
    return account


def run(
    args: argparse.Namespace,
    session: Session,
    read_password: Callable[[str], str],
    out: TextIO,
    now: datetime,
) -> None:
    if args.command == "create":
        teacher_id: int | None = None
        if args.role == "teacher":
            if not args.teacher:
                raise AccountError("A teacher account needs --teacher with the teacher's name")
            teacher = session.scalar(
                select(Teacher).where(func.lower(Teacher.name) == args.teacher.strip().lower())
            )
            if teacher is None:
                raise AccountError(f"No teacher is named {args.teacher}. Upload the timetable first")
            teacher_id = teacher.id
        password = _ask_new_password(read_password)
        account = create_account(
            session,
            email=args.email,
            password=password,
            role=args.role,
            now=now,
            student_register_no=args.student,
            teacher_id=teacher_id,
        )
        print(f"Created {account.role} account {account.email}", file=out)
    elif args.command == "list":
        rows = views(session)
        if not rows:
            print("No accounts yet", file=out)
        for v in rows:
            linked = f"  {v.linked_name}" if v.linked_name else ""
            print(f"{v.email}  {v.role}{'  simulated' if v.simulated else ''}{linked}", file=out)
    elif args.command == "reset-password":
        account = _account(session, args.email)
        reset_password(session, account, _ask_new_password(read_password))
        print(f"New password set for {account.email}", file=out)
    elif args.command == "delete":
        account = _account(session, args.email)
        delete_account(session, account)
        print(f"Deleted account {account.email}", file=out)


def main(
    argv: Sequence[str] | None = None,
    *,
    url: str | None = None,
    read_password: Callable[[str], str] = getpass.getpass,
    out: TextIO | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Run one command. `url`, `read_password` and `out` can be replaced, for tests."""
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        engine = init_database(url or database_url())
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    try:
        with Session(engine) as session:
            run(args, session, read_password, out, now())
    except AccountError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
