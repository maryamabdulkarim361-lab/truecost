"""Cloud Tasks REST adapter using installed google-auth; lazy runtime-only ADC."""
import asyncio
import base64
from google.api_core.exceptions import AlreadyExists


class DispatchUnavailable(Exception):
    pass


class CloudTasksDispatcher:
    production_ready = True

    def __init__(self, config, session_factory=None):
        self.config, self.session_factory = config, session_factory or self._session

    @staticmethod
    def _session():
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        session = AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=5)
        session.trust_env = False
        return session

    def _enqueue(self, task_id, payload, not_before):
        try:
            built = self.config.build(task_id=task_id, payload=payload, not_before=not_before)
            task = built['task']; req = task['http_request']
            body = {'task': {'name': task['name'], 'httpRequest': {
                'httpMethod':'POST', 'url':req['url'], 'headers':req['headers'],
                'body':base64.b64encode(req['body']).decode('ascii'),
                'oidcToken':{'serviceAccountEmail':self.config.service_account,'audience':self.config.worker_url}},
                'scheduleTime':task['schedule_time'].isoformat().replace('+00:00','Z'),
                'dispatchDeadline':f"{task['dispatch_deadline']['seconds']}s"}}
            with self.session_factory() as session:
                response = session.post(f"https://cloudtasks.googleapis.com/v2/{built['parent']}/tasks",
                    json=body, timeout=5, allow_redirects=False)
                if response.status_code not in (200,201,409):
                    raise DispatchUnavailable()
        except AlreadyExists:
            return
        except Exception:
            raise DispatchUnavailable('Dispatch unavailable') from None

    async def publish(self, *, task_id, payload, not_before):
        # Join bounded in-flight HTTP on cancellation; do not leak work after loop closure.
        task = asyncio.create_task(asyncio.to_thread(self._enqueue, task_id, payload, not_before))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise
