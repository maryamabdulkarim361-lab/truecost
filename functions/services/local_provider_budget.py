"""Opt-in, persistent HTTP ceiling for controlled local validation only.

The operator creates the counter once; missing/corrupt counters fail closed.
Never reset on service construction, retries, cancellation or process restart.
No credentials, request content or provider responses are read or stored.
"""
import os
import stat
from pathlib import Path

from config.production import is_production
from config.errors import ErrorCode, StructuredError

COUNTER = Path('/private/tmp/truecost-test-008-provider-budget.count')
LIMIT = 20


def enabled():
    return os.environ.get('TRUECOST_LOCAL_PROVIDER_BUDGET') == 'test-008'


def transport_options():
    # Absent flag preserves the original transport construction exactly.
    return ({'event_hooks': {'request': [before_request]}}
            if os.environ.get('TRUECOST_LOCAL_PROVIDER_BUDGET') else {})


def configuration_valid():
    return (enabled() and not is_production()
            and os.environ.get('USE_FIREBASE_EMULATORS') == 'true'
            and os.environ.get('FIRESTORE_EMULATOR_HOST') == '127.0.0.1:8081'
            and os.environ.get('PIPELINE_EXECUTION_MODE') == 'synchronous'
            and os.environ.get('LLM_PROVIDER') == 'gemini')


def control_state():
    """Local liveness can attest control settings without accessing credentials."""
    return {'enabled': configuration_valid(), 'maximum_requests': LIMIT}


def consume():
    if not enabled():
        if os.environ.get('TRUECOST_LOCAL_PROVIDER_BUDGET'):
            _deny()
        return
    if not configuration_valid():
        _deny()
    try:
        import fcntl  # Local macOS/Linux guard; unused production path is portable.
        fd = os.open(COUNTER, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r+b', buffering=0) as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077):
                _deny()
            value = stream.read(32)
            # Canonical counter only. Oversize or partially written state is closed.
            if value not in [str(i).encode() for i in range(LIMIT)]:
                _deny()
            stream.seek(0)
            stream.write(str(int(value) + 1).encode())
            stream.truncate()
            os.fsync(stream.fileno())
    except (ImportError, OSError, ValueError):
        _deny()


def _deny():
    raise StructuredError(ErrorCode.LLM_PROVIDER_ERROR,
                          reason='validation_budget_blocked',
                          failure_stage='provider_request') from None


async def before_request(request):
    """httpx runs this before sending every request, including redirects."""
    if enabled() and (request.url.scheme != 'https'
                      or request.url.host != 'generativelanguage.googleapis.com'
                      or not request.url.path.startswith('/v1beta/openai/')):
        _deny()
    consume()
