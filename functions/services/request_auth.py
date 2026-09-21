"""User endpoint authentication. No emulator bypass and no credential logging."""
from functools import wraps
from contextvars import ContextVar
import json
import os
from config.production import is_production
from firebase_admin import auth, firestore
from firebase_functions import https_fn

_verified_uid = ContextVar('verified_request_uid', default=None)

class AccessError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

def authenticate(req):
    header = req.headers.get('Authorization', '')
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise AccessError(401, 'Authentication required')
    try:
        claims = auth.verify_id_token(parts[1])
        uid = claims.get('uid')
        if not isinstance(uid, str) or not uid:
            raise ValueError()
        return uid
    except Exception:
        raise AccessError(401, 'Invalid authentication') from None

def current_uid():
    uid = _verified_uid.get()
    if not uid:
        raise AccessError(401, 'Authentication required')
    return uid

def document(collection, identity):
    if not isinstance(identity, str) or not identity or '/' in identity:
        raise AccessError(400, 'Invalid resource identifier')
    snapshot = firestore.client().collection(collection).document(identity).get()
    return snapshot.to_dict() if snapshot.exists else None

def authorize(operation, uid, data, *, read_document=None):
    read_document = document if read_document is None else read_document
    if data.get('userId') is not None and data['userId'] != uid:
        raise AccessError(403, 'Not authorized')
    if operation == 'start':
        project = data.get('projectId')
        if project:
            resource = read_document('projects', project)
            if not resource:
                raise AccessError(404, 'Resource not found')
            editor = any(isinstance(c, dict) and c.get('userId') == uid and c.get('role') == 'editor'
                         for c in resource.get('collaborators', []))
            if resource.get('ownerId') != uid and not editor:
                raise AccessError(403, 'Not authorized')
        clarification = data.get('clarificationOutput')
        estimate_id = clarification.get('estimateId') if isinstance(clarification, dict) else None
        if estimate_id:
            existing = read_document('estimates', estimate_id)
            if existing:
                if existing.get('userId') != uid:
                    raise AccessError(403, 'Not authorized')
                raise AccessError(409, 'Estimate already exists')
    else:
        identity = (data.get('estimate_id') or data.get('project_id')) if operation == 'pdf' else data.get('estimateId')
        resource = read_document('estimates', identity)
        if not resource:
            raise AccessError(404, 'Resource not found')
        # Estimate ownership is userId, matching existing estimate rules.
        if resource.get('userId') != uid:
            raise AccessError(403, 'Not authorized')

def user_endpoint(operation):
    def decorate(handler):
        @wraps(handler)
        def protected(req):
            origin = req.headers.get('Origin')
            if is_production() and origin and origin != os.getenv('ALLOWED_WEB_ORIGIN'):
                return https_fn.Response('Origin not allowed', status=403)
            if req.method == 'OPTIONS':
                return handler(req)
            try:
                uid = authenticate(req)
                if req.method != 'POST':
                    raise AccessError(405, 'Method not allowed')
                try:
                    data = req.get_json(force=True)
                except Exception:
                    raise AccessError(400, 'Invalid request') from None
                if not isinstance(data, dict):
                    raise AccessError(400, 'Invalid request')
                authorize(operation, uid, data)
            except AccessError as error:
                return https_fn.Response(json.dumps({'success':False,'error':{
                    'code':str(error.status),'message':error.message}}), status=error.status,
                    mimetype='application/json', headers={'Access-Control-Allow-Origin': os.getenv('ALLOWED_WEB_ORIGIN', '') if is_production() else '*'})
            except Exception:
                return https_fn.Response(json.dumps({'success':False,'error':{
                    'code':'503','message':'Authorization unavailable'}}),status=503,mimetype='application/json')
            token = _verified_uid.set(uid)
            try:
                return handler(req)
            finally:
                _verified_uid.reset(token)
        return protected
    return decorate
