"""Password hashing, tokens, the login rate limit and staff local parts: plain functions, no database."""

from datetime import UTC, datetime, timedelta

from backend import auth
from shared.emails import staff_email, staff_local_part, student_email

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def test_a_password_verifies_against_its_own_hash() -> None:
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)


def test_a_wrong_password_fails() -> None:
    stored = auth.hash_password("correct horse")
    assert not auth.verify_password("correct horses", stored)
    assert not auth.verify_password("", stored)


def test_the_salt_differs_per_hash_so_equal_passwords_store_differently() -> None:
    first = auth.hash_password("same password")
    second = auth.hash_password("same password")
    assert first != second
    assert auth.verify_password("same password", first)
    assert auth.verify_password("same password", second)


def test_the_stored_value_does_not_contain_the_password() -> None:
    stored = auth.hash_password("hunter2-hunter2")
    assert "hunter2" not in stored
    assert stored.startswith("scrypt$")


def test_a_damaged_or_foreign_stored_value_never_verifies() -> None:
    assert not auth.verify_password("x", "")
    assert not auth.verify_password("x", "plain-text")
    assert not auth.verify_password("x", "md5$1$2$3$4$5")
    assert not auth.verify_password("x", "scrypt$16384$8$1$not-hex$not-hex")


def test_tokens_are_random_and_stored_only_as_a_hash() -> None:
    first, second = auth.new_token(), auth.new_token()
    assert first != second and len(first) >= 40
    digest = auth.hash_token(first)
    assert digest != first and first not in digest
    assert digest == auth.hash_token(first)
    assert digest != auth.hash_token(second)


def test_the_login_limit_blocks_after_five_failures_and_lifts_after_the_window() -> None:
    limiter = auth.LoginLimiter(max_failures=5, window=timedelta(minutes=5))
    for i in range(5):
        assert not limiter.blocked("a@x.test", T0 + timedelta(seconds=i))
        limiter.record_failure("a@x.test", T0 + timedelta(seconds=i))
    assert limiter.blocked("a@x.test", T0 + timedelta(seconds=10))
    assert not limiter.blocked("b@x.test", T0 + timedelta(seconds=10))  # per email
    assert limiter.blocked("a@x.test", T0 + timedelta(minutes=4))
    assert not limiter.blocked("a@x.test", T0 + timedelta(minutes=5, seconds=5))


def test_the_login_limit_is_case_insensitive_and_a_success_clears_it() -> None:
    limiter = auth.LoginLimiter(max_failures=2, window=timedelta(minutes=5))
    limiter.record_failure("Asha@X.test", T0)
    limiter.record_failure("asha@x.test", T0)
    assert limiter.blocked("ASHA@x.test", T0)
    limiter.reset("asha@x.test")
    assert not limiter.blocked("Asha@x.test", T0)


def test_staff_local_parts_drop_titles_and_join_first_and_last_name() -> None:
    assert staff_local_part("Dr. Anita Menon") == "anita.menon"
    assert staff_local_part("Prof. Rahul Bhat") == "rahul.bhat"
    assert staff_local_part("Ms. Farah Siddiqui") == "farah.siddiqui"
    assert staff_local_part("Dr Meera Iyer") == "meera.iyer"
    assert staff_local_part("Dr. Rao") == "rao"
    assert staff_local_part("Anita Kumari Menon") == "anita.menon"
    assert staff_local_part("  Dr.  Sandeep   O'Neil ") == "sandeep.oneil"


def test_demo_emails_are_on_the_reserved_test_domain() -> None:
    assert staff_email("Dr. Anita Menon") == "anita.menon@classpulse.test"
    assert student_email("SIM-A01") == "sim-a01@classpulse.test"
