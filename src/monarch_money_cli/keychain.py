"""macOS Keychain storage via Apple's own /usr/bin/security tool. No third-party code.

The token is passed to `security` over stdin (interactive mode), never in argv,
so it doesn't show up in `ps` output or shell history.
"""
import re

# Subprocess is used only with a fixed absolute path to Apple's tool and no shell.
import subprocess  # nosec B404
import sys

SECURITY = "/usr/bin/security"  # absolute path: no PATH hijacking
SERVICE = "monarch-money-cli"
ACCOUNT = "session_token"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{20,256}$")


class KeychainError(RuntimeError):
    pass


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise KeychainError("This tool stores its token in the macOS Keychain and only runs on macOS.")


def valid_token(token: str) -> bool:
    # Strict charset also guarantees the token can't break out of the stdin command below.
    return bool(_TOKEN_RE.fullmatch(token))


def store(token: str) -> None:
    _require_macos()
    if not valid_token(token):
        raise KeychainError("Token has an unexpected format; refusing to store it.")
    cmd = f'add-generic-password -U -s {SERVICE} -a {ACCOUNT} -w "{token}"\n'
    # Fixed argv; the validated token travels via stdin, never argv.
    r = subprocess.run([SECURITY, "-i"],  # nosec B603
                       input=cmd, text=True, capture_output=True, timeout=15)
    if r.returncode != 0 or "error" in r.stderr.lower():
        raise KeychainError("Could not write to the macOS Keychain.")


def _read() -> str:
    r = subprocess.run(  # nosec B603
        [SECURITY, "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
        capture_output=True, text=True, timeout=15,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def load() -> str | None:
    if sys.platform != "darwin":
        return None
    token = _read()
    return token if valid_token(token) else None


def token_state() -> str:
    """'ok', 'missing', 'invalid' (stored value has a bad format), or 'unsupported' (not macOS)."""
    if sys.platform != "darwin":
        return "unsupported"
    token = _read()
    if not token:
        return "missing"
    return "ok" if valid_token(token) else "invalid"


def delete() -> bool:
    _require_macos()
    r = subprocess.run(  # nosec B603
        [SECURITY, "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT],
        capture_output=True, text=True, timeout=15,
    )
    return r.returncode == 0
