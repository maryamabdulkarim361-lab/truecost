"""Production configuration checks. Never reads credentials or contacts a service."""
import os
from urllib.parse import urlsplit


def is_production():
    # Managed Functions/Cloud Run cannot opt out by setting emulator flags.
    return os.getenv('APP_ENV') == 'production' or bool(os.getenv('K_SERVICE'))


def require_https_url(value, name):
    url = urlsplit(value or '')
    host = url.hostname or ''
    if (url.scheme != 'https' or not host or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)
            or host == 'localhost' or host.endswith('.localhost')
            or ':' in host or host.replace('.', '').isdigit()
            or 'collabcanvas-dev' in host or 'collabcanvas-dev' in url.path):
        raise ValueError(f'{name} requires an explicit production HTTPS hostname')
    return value.rstrip('/')


def validate_production(settings):
    if not is_production():
        return
    flags = ('USE_FIREBASE_EMULATORS', 'FUNCTIONS_EMULATOR', 'DISABLE_OPENAI')
    hosts = ('FIRESTORE_EMULATOR_HOST', 'FIREBASE_AUTH_EMULATOR_HOST',
             'FIREBASE_STORAGE_EMULATOR_HOST', 'STORAGE_EMULATOR_HOST',
             'FIREBASE_DATABASE_EMULATOR_HOST')
    if any(os.getenv(k, '').lower() in ('true', '1', 'yes', 'on') for k in flags) or any(os.getenv(k) for k in hosts):
        raise ValueError('Local/emulator configuration is forbidden in production')
    if not os.getenv('LLM_PROVIDER') or not os.getenv('LLM_MODEL'):
        raise ValueError('Production requires explicit LLM_PROVIDER and LLM_MODEL')
    project = settings.firebase_project_id
    if not project or project == 'collabcanvas-dev' or project.startswith('demo-'):
        raise ValueError('FIREBASE_PROJECT_ID must explicitly identify production')
    for key in ('GCLOUD_PROJECT', 'GOOGLE_CLOUD_PROJECT'):
        if os.getenv(key) and os.getenv(key) != project:
            raise ValueError('Firebase project configuration must agree')
    require_https_url(settings.a2a_base_url, 'A2A_BASE_URL')
    origin = require_https_url(os.getenv('ALLOWED_WEB_ORIGIN'), 'ALLOWED_WEB_ORIGIN')
    if urlsplit(origin).path:
        raise ValueError('ALLOWED_WEB_ORIGIN must be an origin without a path')
