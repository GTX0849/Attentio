"""Demo account emails for the simulator, on the reserved `.test` domain (they can never be real)."""

import re

DEMO_DOMAIN = "classpulse.test"
TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "shri", "smt", "sri"}


def staff_local_part(name: str) -> str:
    """"Dr. Anita Menon" -> "anita.menon": titles dropped, first and last name, lower case, letters and digits only."""
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    words = [w for w in words if w.strip(".").lower() not in TITLES]
    cleaned = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in words]
    cleaned = [w for w in cleaned if w]
    if not cleaned:
        return ""
    return cleaned[0] if len(cleaned) == 1 else f"{cleaned[0]}.{cleaned[-1]}"


def staff_email(name: str) -> str:
    """"Dr. Anita Menon" -> "anita.menon@classpulse.test"."""
    return f"{staff_local_part(name)}@{DEMO_DOMAIN}"


def student_email(register_no: str) -> str:
    """"SIM-A01" -> "sim-a01@classpulse.test"."""
    return f"{register_no.strip().lower()}@{DEMO_DOMAIN}"
