"""Sign-in endpoints: login, me, logout, expiry and the login rate limit (sign-in enforced)."""

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text

from tests.auth_helpers import PASSWORD, add_account, add_student, add_teacher, bearer, db, sign_in
from tests.conftest import FakeClock

BAD = {"detail": "Email or password is incorrect"}
ADMIN = "admin@example.test"
ASHA = "asha@example.test"


def login(client: TestClient, email: str, password: str) -> Response:
    resp: Response = client.post("/api/auth/login", json={"email": email, "password": password})
    return resp


def test_a_student_signs_in_with_email_and_password_and_gets_their_role_and_register_number(
    secure_client: TestClient,
) -> None:
    add_student(secure_client, "STU002", "Asha Rao")
    add_account(secure_client, ASHA, "student", student="STU002")
    body = sign_in(secure_client, ASHA)
    assert body["role"] == "student"
    assert body["email"] == ASHA
    assert body["display_name"] == "Asha Rao"
    assert body["student_register_no"] == "STU002"
    assert body["teacher_id"] is None
    assert len(body["token"]) >= 40
    assert PASSWORD not in str(body)


def test_a_teacher_signs_in_with_the_linked_teacher_id(secure_client: TestClient) -> None:
    teacher_id = add_teacher(secure_client, "Dr. Anita Menon")
    add_account(secure_client, "anita.menon@example.test", "teacher", teacher_id=teacher_id)
    body = sign_in(secure_client, "anita.menon@example.test")
    assert body["role"] == "teacher"
    assert body["teacher_id"] == teacher_id
    assert body["display_name"] == "Dr. Anita Menon"
    assert body["student_register_no"] is None


def test_the_email_is_stored_lower_cased_and_matched_ignoring_case(secure_client: TestClient) -> None:
    add_account(secure_client, "Admin@Example.TEST", "admin")
    with db(secure_client) as session:
        assert session.execute(text("SELECT email FROM accounts")).scalar_one() == ADMIN
    assert sign_in(secure_client, "ADMIN@example.test")["email"] == ADMIN


def test_an_unknown_email_and_a_wrong_password_give_the_same_401(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    wrong_password = login(secure_client, ADMIN, "not-the-password")
    unknown = login(secure_client, "nobody@example.test", PASSWORD)
    for resp in (wrong_password, unknown):
        assert resp.status_code == 401
        assert resp.json() == BAD


def test_the_login_body_takes_only_email_and_password(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    assert secure_client.post("/api/auth/login", json={"email": ADMIN}).status_code == 422
    # a role sent along is ignored: the role comes from the account
    resp = secure_client.post("/api/auth/login", json={"email": ADMIN, "password": PASSWORD, "role": "student"})
    assert resp.status_code == 200 and resp.json()["role"] == "admin"


def test_the_password_and_hash_never_appear_in_the_database_or_a_response(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin", password="plain-secret-123")
    with db(secure_client) as session:
        stored = session.execute(text("SELECT password_hash FROM accounts")).scalar_one()
    assert "plain-secret-123" not in stored and stored.startswith("scrypt$")
    token = sign_in(secure_client, ADMIN, "plain-secret-123")["token"]
    with db(secure_client) as session:
        rows = session.execute(text("SELECT token_hash FROM sessions")).scalars().all()
    assert rows and token not in rows  # only a digest of the token is kept
    listing = secure_client.get("/api/accounts", headers=bearer(token))
    assert "plain-secret-123" not in listing.text and "scrypt" not in listing.text


def test_me_returns_the_current_identity(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    token = sign_in(secure_client, ADMIN)["token"]
    resp = secure_client.get("/api/auth/me", headers=bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {
        "role": "admin",
        "email": ADMIN,
        "display_name": ADMIN,
        "student_register_no": None,
        "teacher_id": None,
    }
    assert secure_client.get("/api/auth/me").status_code == 401


def test_logout_revokes_the_token(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    token = sign_in(secure_client, ADMIN)["token"]
    other = sign_in(secure_client, ADMIN)["token"]
    assert secure_client.post("/api/auth/logout", headers=bearer(token)).status_code == 204
    assert secure_client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert secure_client.get("/api/auth/me", headers=bearer(other)).status_code == 200  # only that session


def test_a_session_expires_after_twelve_hours(secure_client: TestClient, clock: FakeClock) -> None:
    add_account(secure_client, ADMIN, "admin")
    token = sign_in(secure_client, ADMIN)["token"]
    clock.advance(hours=11, minutes=59)
    assert secure_client.get("/api/auth/me", headers=bearer(token)).status_code == 200
    clock.advance(minutes=2)
    assert secure_client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_five_failures_in_five_minutes_lock_the_email_with_429(
    secure_client: TestClient, clock: FakeClock
) -> None:
    add_account(secure_client, ADMIN, "admin")
    for _ in range(5):
        assert login(secure_client, ADMIN, "wrong-password").status_code == 401
    blocked = login(secure_client, ADMIN, PASSWORD)  # even the right password is refused now
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Too many attempts. Try again in a few minutes"}
    assert login(secure_client, "someone.else@example.test", "wrong-password").status_code == 401

    clock.advance(minutes=5, seconds=1)
    assert login(secure_client, ADMIN, PASSWORD).status_code == 200


def test_unknown_emails_are_limited_too_so_the_limit_does_not_reveal_accounts(
    secure_client: TestClient,
) -> None:
    for _ in range(5):
        login(secure_client, "ghost@example.test", "x")
    assert login(secure_client, "GHOST@example.test", "x").status_code == 429


def test_a_successful_sign_in_clears_the_failure_count(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    for _ in range(4):
        login(secure_client, ADMIN, "wrong-password")
    assert login(secure_client, ADMIN, PASSWORD).status_code == 200
    for _ in range(4):
        login(secure_client, ADMIN, "wrong-password")
    assert login(secure_client, ADMIN, PASSWORD).status_code == 200


def test_resetting_a_password_ends_the_old_sessions_and_changes_the_password(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    add_student(secure_client, "STU002")
    add_account(secure_client, ASHA, "student", student="STU002")
    admin = sign_in(secure_client, ADMIN)["token"]
    student = sign_in(secure_client, ASHA)["token"]
    accounts = secure_client.get("/api/accounts", headers=bearer(admin)).json()
    student_id = next(a["id"] for a in accounts if a["email"] == ASHA)
    resp = secure_client.post(
        f"/api/accounts/{student_id}/password", json={"password": "a-brand-new-password"}, headers=bearer(admin)
    )
    assert resp.status_code == 204
    assert secure_client.get("/api/auth/me", headers=bearer(student)).status_code == 401
    assert login(secure_client, ASHA, PASSWORD).status_code == 401
    assert login(secure_client, ASHA, "a-brand-new-password").status_code == 200


def test_admin_creates_lists_and_deletes_accounts_and_cannot_delete_their_own(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    add_student(secure_client, "STU002", "Asha Rao")
    admin = bearer(sign_in(secure_client, ADMIN)["token"])

    body = {"email": ASHA, "password": "initial-password", "role": "student", "student_register_no": "STU002"}
    created = secure_client.post("/api/accounts", json=body, headers=admin)
    assert created.status_code == 201
    assert created.json()["linked_name"] == "Asha Rao" and "password" not in created.text
    assert secure_client.post("/api/accounts", json={**body, "email": "ASHA@example.test"}, headers=admin).status_code == 409
    for bad in (
        {**body, "email": "not-an-email"},  # not an email address
        {**body, "email": "x@example.test", "password": "short"},  # too short
        {**body, "email": "y@example.test", "student_register_no": None},  # a student needs a link
        {**body, "email": "z@example.test", "student_register_no": "NOPE"},  # unknown student
    ):
        assert secure_client.post("/api/accounts", json=bad, headers=admin).status_code == 422

    listing = secure_client.get("/api/accounts", headers=admin).json()
    assert [a["email"] for a in listing] == [ADMIN, ASHA]
    mine = next(a["id"] for a in listing if a["email"] == ADMIN)
    assert secure_client.delete(f"/api/accounts/{mine}", headers=admin).status_code == 409
    theirs = next(a["id"] for a in listing if a["email"] == ASHA)
    assert secure_client.delete(f"/api/accounts/{theirs}", headers=admin).status_code == 204
    assert login(secure_client, ASHA, "initial-password").status_code == 401


def test_a_session_does_not_survive_its_account_being_deleted(secure_client: TestClient) -> None:
    add_account(secure_client, ADMIN, "admin")
    add_account(secure_client, "second.admin@example.test", "admin")
    first = bearer(sign_in(secure_client, ADMIN)["token"])
    second = bearer(sign_in(secure_client, "second.admin@example.test")["token"])
    accounts = secure_client.get("/api/accounts", headers=first).json()
    target = next(a["id"] for a in accounts if a["email"] == "second.admin@example.test")
    secure_client.delete(f"/api/accounts/{target}", headers=first)
    assert secure_client.get("/api/auth/me", headers=second).status_code == 401
