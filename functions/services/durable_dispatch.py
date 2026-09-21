"""Cloud Tasks payload and authenticated invocation contracts only; no RPCs."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import ipaddress
import os
import re
from urllib.parse import urlsplit
from services.durable_execution import Envelope, Rejected, VerifiedService, Worker, identifier


@dataclass(frozen=True)
class TaskConfiguration:
    queue_path: str
    worker_url: str
    service_account: str

    def __post_init__(self):
        if not re.fullmatch(r'projects/[a-z][a-z0-9-]+/locations/[a-z0-9-]+/queues/[A-Za-z0-9_-]+', self.queue_path):
            raise Rejected('Queue configuration required')
        url = urlsplit(self.worker_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.port not in (None,443)
                or url.hostname in {'localhost','127.0.0.1','::1'}):
            raise Rejected('Private worker URL configuration required')
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            pass
        else:
            raise Rejected('Private worker hostname required')
        if not re.fullmatch(r'[a-z][a-z0-9-]*@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com', self.service_account):
            raise Rejected('Dispatch service identity required')

    @classmethod
    def from_environment(cls):
        return cls(os.getenv('DURABLE_QUEUE_PATH',''), os.getenv('DURABLE_WORKER_URL',''),
                   os.getenv('DURABLE_DISPATCH_SERVICE_ACCOUNT',''))

    def build(self, *, task_id, payload, not_before):
        identifier(task_id)
        envelope = wire_envelope(payload)
        if envelope.operation_id != f'{envelope.job_id}-r{envelope.revision}' or not re.fullmatch(
                re.escape(envelope.operation_id) + r'-p[0-9]+', task_id):
            raise Rejected('Task identity mismatch')
        return {'parent': self.queue_path, 'task': {
            'name': f'{self.queue_path}/tasks/{task_id}',
            'http_request': {'http_method':'POST', 'url':self.worker_url,
                'headers': {'Content-Type':'application/json'},
                'body':json.dumps(payload, sort_keys=True, separators=(',',':')).encode(),
                'oidc_token':{'service_account_email':self.service_account, 'audience':self.worker_url}},
            'schedule_time':datetime.fromtimestamp(not_before, timezone.utc),
            'dispatch_deadline':{'seconds':390}}}


def wire_envelope(body):
    if not isinstance(body, dict) or set(body) != {'jobId','revision','operationId','generation'}:
        raise Rejected('Identifier-only worker envelope required')
    return Envelope.parse(body)


def verify_identity(identity, principal, audience):
    if (not isinstance(identity, VerifiedService) or identity.principal != principal
            or identity.audience != audience):
        raise Rejected('Service identity rejected')


class DurableWorkerService:
    """Private HTTP adapter contract. IAM verifier supplies VerifiedService."""
    def __init__(self, core, *, principal, audience, execute):
        self.core, self.execute = core, execute
        self.worker = Worker(core, allowed_principal=principal, audience=audience)

    async def handle(self, identity, body, *, lease_owner):
        verify_identity(identity, self.worker.principal, self.worker.audience)
        envelope = wire_envelope(body)
        job = await self.core.repo.get(envelope.job_id)
        if (not job or job['status'] in {'final','failed'} or job['revision'] != envelope.revision
                or job['generation'] != envelope.generation
                or job['dispatchIntent']['operationId'] != envelope.operation_id):
            return {'status':'rejected'}  # ACK obsolete delivery; never run an agent.
        await self.worker.handle(identity, body, lease_owner=lease_owner, execute=self.execute)
        return {'status':'handled'}


class ScannerInvocation:
    """External scheduler invokes this bounded page; it must retain the cursor.

    No Scheduler resource or daemon is created. Auth verifier is adapter-owned.
    """
    def __init__(self, scanner, *, principal, audience):
        if not principal or not audience:
            raise Rejected('Scanner identity required')
        self.scanner, self.principal, self.audience = scanner, principal, audience

    async def handle(self, identity, body):
        verify_identity(identity, self.principal, self.audience)
        if not isinstance(body, dict) or set(body) - {'cursor','limit'}:
            raise Rejected('Invalid scanner request')
        cursor, limit = body.get('cursor'), body.get('limit',25)
        if cursor is not None:
            identifier(cursor)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise Rejected('Invalid scanner page')
        return await self.scanner.run_page(after=cursor, limit=limit)
