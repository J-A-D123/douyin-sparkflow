"""Local service authentication. Tokens are never part of exported accounts."""
import os
import secrets
from pathlib import Path


def token_path():
    default = Path(__file__).resolve().parents[2] / 'state' / 'login-api' / 'token'
    return Path(os.getenv('SPARKFLOW_LOGIN_DESKTOP_API_TOKEN_FILE') or default)


def read_login_api_token():
    token = token_path().read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise RuntimeError('Login service token is invalid')
    return token


def ensure_login_api_token():
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_login_api_token()
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(secrets.token_urlsafe(48) + '\n')
        output.flush()
        os.fsync(output.fileno())
    return read_login_api_token()
