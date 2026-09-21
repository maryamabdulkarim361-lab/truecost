"""Production HTTP composition. No clients, tokens or network at import time."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from werkzeug.exceptions import BadRequest
from config.production import is_production, require_https_url
from services.durable_execution import DurableCore, VerifiedService, Rejected
from services.durable_store import FirestoreAtomicBackend, JobRepository
from services.durable_boundaries import DurableStartService, IntentScanner
from services.durable_dispatch import TaskConfiguration, DurableWorkerService, ScannerInvocation
from services.request_auth import AccessError


def durable_selected():
    mode = os.getenv('PIPELINE_EXECUTION_MODE', 'synchronous')
    if mode not in ('synchronous', 'durable') or (is_production() and mode != 'durable'):
        raise ValueError('Production requires durable execution')
    return mode == 'durable'


def service_config(kind):
    audience = require_https_url(os.getenv(f'DURABLE_{kind}_URL'), 'Service audience')
    principal = os.getenv(f'DURABLE_{kind}_INVOKER', '')
    import re
    if not re.fullmatch(r'[a-z][a-z0-9-]*@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com', principal):
        raise ValueError('Service invoker required')
    return principal, audience


def verify_service(request, principal, audience, verifier=None):
    """Verify Google-signed OIDC; end-user Firebase tokens are not accepted."""
    if request.method != 'POST':
        raise AccessError(405, 'POST required')
    parts = request.headers.get('Authorization', '').split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise AccessError(401, 'Service authentication required')
    try:
        if verifier is None:
            from google.oauth2.id_token import verify_oauth2_token
            from google.auth.transport.requests import Request
            import requests
            class BoundedRequest(Request):
                def __call__(self, *args, **kwargs):
                    kwargs['timeout'] = 5
                    return super().__call__(*args, **kwargs)
            with requests.Session() as session:
                session.trust_env = False
                claims = verify_oauth2_token(parts[1], BoundedRequest(session=session), audience=audience)
        else:
            claims = verifier(parts[1], audience)
        if (claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')
                or claims.get('aud') != audience or claims.get('email') != principal
                or claims.get('email_verified') is not True or not claims.get('sub')):
            raise ValueError()
        return VerifiedService(principal, audience)
    except Exception:
        raise AccessError(403, 'Service authentication rejected') from None


async def handle(kind, request, core, *, dispatcher=None, execute=None, identity=None):
    """Same handler used by private HTTP wrappers and offline/emulator tests."""
    try:
        if kind == 'start':
            from models.clarification_output import ClarificationOutput
            try:
                clarification = request.get_json(force=True).get('clarificationOutput')
                parsed = ClarificationOutput.model_validate_json(json.dumps(clarification, allow_nan=False), strict=True)
                if any(not getattr(parsed.projectBrief.location, field).strip() for field in
                       ('fullAddress','streetAddress','city','state','zipCode')):
                    raise ValueError('Location requires clarification')
                if parsed.clarificationStatus.value != 'complete' or parsed.flags.userVerificationRequired:
                    raise ValueError('Clarification requires review')
            except Exception:
                raise AccessError(400, 'Invalid clarification schema') from None
            result = await DurableStartService(core).handle(request)
            result.pop('httpStatus', None)
            return {'success': True, 'data': result}, 202
        body = request.get_json(force=True)
        if kind == 'worker':
            principal, audience = service_config('WORKER')
            result = await DurableWorkerService(core, principal=principal, audience=audience,
                execute=execute).handle(identity, body, lease_owner=uuid4().hex)
        elif kind == 'scanner':
            principal, audience = service_config('SCANNER')
            result = await ScannerInvocation(IntentScanner(core, dispatcher), principal=principal,
                audience=audience).handle(identity, body)
        else:
            raise ValueError()
        return result, 200
    except AccessError as error:
        return {'error': {'code': str(error.status), 'message': error.message}}, error.status
    except Rejected:
        return {'error': {'code': 'DURABLE_REJECTED'}}, 409
    except (ValueError, TypeError, BadRequest):
        return {'error': {'code': 'INVALID_REQUEST'}}, 400
    except Exception:
        return {'error': {'code': 'DURABLE_UNAVAILABLE'}}, 503


@asynccontextmanager
async def runtime():
    from firebase_admin import get_app
    from services.cloud_tasks_dispatcher import CloudTasksDispatcher
    from services.durable_agents import DurableAgentExecutor, real_agent
    from services.llm_service import LLMService
    if not durable_selected():
        raise ValueError('Durable execution required')
    from config.settings import settings
    from config.production import validate_production
    validate_production(settings)
    cfg = TaskConfiguration.from_environment()
    require_https_url(cfg.worker_url, 'Worker URL')
    if is_production() and cfg.queue_path.split('/')[1] != settings.firebase_project_id:
        raise ValueError('Queue project mismatch')
    worker_principal, worker_audience = service_config('WORKER')
    service_config('SCANNER')
    if cfg.worker_url != worker_audience or cfg.service_account != worker_principal:
        raise ValueError('Dispatcher and worker identity must agree')
    # Each invocation owns its async client. Never use a cached AsyncClient across loops.
    from google.cloud.firestore_v1.async_client import AsyncClient
    app = get_app()
    db = AsyncClient(project=app.project_id, credentials=app.credential.get_credential())
    dispatcher = CloudTasksDispatcher(cfg)
    core = DurableCore(JobRepository(FirestoreAtomicBackend(db)), production=is_production(), dispatcher=dispatcher)
    execute = DurableAgentExecutor(core, lambda name, storage: real_agent(name, storage, LLMService()))
    try:
        yield core, dispatcher, execute
    finally:
        await db._firestore_api.transport.close()


def http_entry(kind, request):
    """Synchronous Firebase wrapper owns precisely one event loop."""
    from firebase_functions import https_fn
    identity = None
    try:
        if kind != 'start':
            principal, audience = service_config(kind.upper())
            identity = verify_service(request, principal, audience)
        elif is_production() and request.headers.get('Origin') not in (None, os.getenv('ALLOWED_WEB_ORIGIN')):
            raise AccessError(403, 'Origin not allowed')
        if request.method == 'OPTIONS' and kind == 'start':
            return https_fn.Response('', status=204, headers={'Access-Control-Allow-Origin': os.getenv('ALLOWED_WEB_ORIGIN',''),
                'Access-Control-Allow-Headers':'Authorization,Content-Type','Access-Control-Allow-Methods':'POST'})
        async def run():
            async with runtime() as (core, dispatcher, execute):
                return await handle(kind, request, core, dispatcher=dispatcher, execute=execute, identity=identity)
        data, status = asyncio.run(run())
    except AccessError as error:
        data, status = {'error': {'code': str(error.status), 'message': error.message}}, error.status
    except Exception:
        data, status = {'error': {'code': 'DURABLE_UNAVAILABLE'}}, 503
    headers = {'Access-Control-Allow-Origin': os.getenv('ALLOWED_WEB_ORIGIN','')} if kind == 'start' else {}
    return https_fn.Response(json.dumps(data), status=status, mimetype='application/json', headers=headers)


def route_start(legacy):
    from functools import wraps
    @wraps(legacy)
    def wrapped(request):
        try:
            selected = durable_selected()
        except ValueError:
            from firebase_functions import https_fn
            return https_fn.Response('{"error":{"code":"DURABLE_CONFIGURATION_REQUIRED"}}', status=503, mimetype='application/json')
        return http_entry('start', request) if selected else legacy(request)
    return wrapped
