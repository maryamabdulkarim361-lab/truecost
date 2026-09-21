"""Lazy ADC OIDC acquisition for an explicitly configured private service."""
import asyncio
from config.production import require_https_url


def _token(audience):
    from google.oauth2.id_token import fetch_id_token
    from google.auth.transport.requests import Request
    import requests
    class BoundedRequest(Request):
        def __call__(self, *args, **kwargs):
            kwargs['timeout'] = 5
            return super().__call__(*args, **kwargs)
    with requests.Session() as session:
        session.trust_env = False
        return fetch_id_token(BoundedRequest(session=session), audience)


async def service_headers(audience, acquire=None):
    target = require_https_url(audience, 'Service audience')
    task = asyncio.create_task(asyncio.to_thread(acquire or _token, target))
    try:
        token = await asyncio.shield(task)
        if not isinstance(token, str) or not token:
            raise ValueError()
        return {'Authorization': 'Bearer ' + token, 'Content-Type':'application/json'}
    except asyncio.CancelledError:
        try: await task
        except Exception: pass
        raise
    except Exception:
        raise RuntimeError('Service identity unavailable') from None
