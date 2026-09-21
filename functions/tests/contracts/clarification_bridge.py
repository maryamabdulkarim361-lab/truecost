"""Offline cross-language consumer. stdin contains synthetic producer output only."""
import sys,json,asyncio,socket,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
def blocked(*a,**k): raise AssertionError('Network forbidden')
socket.socket.connect=blocked
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
from services.durable_http import handle
from services.durable_execution import DurableCore
from services.durable_store import JobRepository
from tests.unit.test_durable_core import MemoryBackend
from services import request_auth
from flask import Request
from werkzeug.test import EnvironBuilder
request_auth.authenticate=lambda req:'owner'
async def run():
 backend=MemoryBackend();backend.docs['projects/project']={'ownerId':'owner'}
 core=DurableCore(JobRepository(backend))
 results=[]
 for data in json.load(sys.stdin):
  req=Request(EnvironBuilder(method='POST',json=data).get_environ())
  body,status=await handle('start',req,core,execute=blocked)
  if status==202:
   accepted=body['data']; root=backend.docs['estimates/'+accepted['estimateId']]
   assert accepted['estimateId']=='est-'+hashlib.sha256(data['idempotencyKey'].encode()).hexdigest()
   assert root['userId']=='owner' and root['projectId']==data['projectId']
   assert backend.docs['durableJobs/'+accepted['jobId']]['dispatchIntent']['status']=='pending'
  results.append({'status':status,'data':body.get('data'), 'error':body.get('error',{}).get('code')})
 print(json.dumps(results))
asyncio.run(run())
