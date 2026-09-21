"""Production checks are offline and use configuration names/fake values only."""
from types import SimpleNamespace
import pytest
from config.production import validate_production, require_https_url, is_production

@pytest.fixture(autouse=True)
def environment(monkeypatch, block_network):
    for key in ('K_SERVICE','APP_ENV','USE_FIREBASE_EMULATORS','FUNCTIONS_EMULATOR','DISABLE_OPENAI',
                'FIRESTORE_EMULATOR_HOST','FIREBASE_AUTH_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST',
                'STORAGE_EMULATOR_HOST','FIREBASE_DATABASE_EMULATOR_HOST','GCLOUD_PROJECT','GOOGLE_CLOUD_PROJECT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('LLM_PROVIDER','gemini')
    monkeypatch.setenv('LLM_MODEL','offline-model')
    monkeypatch.setenv('ALLOWED_WEB_ORIGIN','https://app.example.com')


def config():
    return SimpleNamespace(firebase_project_id='example-production',a2a_base_url='https://us-central1-example-production.cloudfunctions.net')


def test_valid():
    validate_production(config())

@pytest.mark.parametrize('key', ['USE_FIREBASE_EMULATORS','FUNCTIONS_EMULATOR','DISABLE_OPENAI','FIRESTORE_EMULATOR_HOST','FIREBASE_AUTH_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST'])
def test_reject_local(monkeypatch,key):
    monkeypatch.setenv(key,'true')
    with pytest.raises(ValueError): validate_production(config())

@pytest.mark.parametrize('url', ['', 'http://localhost:5001', 'https://127.0.0.1', 'https://localhost', 'https://us-central1-collabcanvas-dev.cloudfunctions.net','https://example.com/collabcanvas-dev/us-central1', 'https://example.com?token=fake','https://fake:fake@example.com'])
def test_invalid_endpoint(url):
    with pytest.raises(ValueError): require_https_url(url,'A2A_BASE_URL')

@pytest.mark.parametrize('project',[None,'collabcanvas-dev','demo-test'])
def test_reject_project(project):
    value=config();value.firebase_project_id=project
    with pytest.raises(ValueError): validate_production(value)


def test_managed_runtime_cannot_opt_out(monkeypatch):
    monkeypatch.setenv('APP_ENV','development');monkeypatch.setenv('K_SERVICE','fake-service')
    assert is_production()


def test_local_preserved(monkeypatch):
    monkeypatch.setenv('APP_ENV','development');monkeypatch.setenv('USE_FIREBASE_EMULATORS','true')
    validate_production(SimpleNamespace())

@pytest.mark.asyncio
async def test_private_a2a_identity(monkeypatch):
    import httpx
    from google.oauth2 import id_token
    from services.a2a_client import A2AClient
    called=[]
    def token(request,audience):
        called.append(audience)
        return 'fake-offline-identity'
    monkeypatch.setattr(id_token,'fetch_id_token',token)
    real_client=httpx.AsyncClient
    async def receive(request):
        assert request.headers['Authorization']=='Bearer fake-offline-identity'
        return httpx.Response(200,json={'result':{'status':'completed'}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda:real_client(transport=httpx.MockTransport(receive)))
    client=A2AClient(base_url='https://us-central1-example-production.cloudfunctions.net')
    await client.send_task('timeline',{})
    assert called==['https://us-central1-example-production.cloudfunctions.net/a2a_timeline']

def test_production_browser_origin_rejected_before_auth(monkeypatch):
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request
    from services.request_auth import user_endpoint
    def forbidden(*args, **kwargs):
        pytest.fail('Untrusted origin must not reach authentication or handler')
    monkeypatch.setattr('services.request_auth.authenticate', forbidden)
    handler=user_endpoint('read')(forbidden)
    request=Request(EnvironBuilder(method='POST',headers={'Origin':'https://untrusted.example'}).get_environ())
    assert handler(request).status_code==403


def test_production_private_identity_failure_does_not_send(monkeypatch):
    import asyncio
    import httpx
    from google.oauth2 import id_token
    from services.a2a_client import A2AClient
    def fail(*args): raise RuntimeError('fake-sensitive-message')
    def forbidden(*args,**kwargs): pytest.fail('No anonymous fallback allowed')
    monkeypatch.setattr(id_token,'fetch_id_token',fail)
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    with pytest.raises(Exception) as caught:
        asyncio.run(A2AClient(base_url='https://example.com').send_task('timeline',{}))
    assert 'fake-sensitive-message' not in str(caught.value)
