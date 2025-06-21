import hashlib
import secrets

SESSION_BYTES = 32


def create_session_tokens() -> tuple[str, str, str]:
    plain = secrets.token_urlsafe(SESSION_BYTES)
    digest = hashlib.sha256(plain.encode()).hexdigest()
    csrf = secrets.token_hex(16)

    return plain, digest, csrf
