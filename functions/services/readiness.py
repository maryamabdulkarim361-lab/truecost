"""Private, configuration-only readiness. Never probes LLMs or cloud services."""
import os


def check(settings):
    from config.production import validate_production, require_https_url
    from services.durable_http import durable_selected, service_config
    from services.durable_dispatch import TaskConfiguration
    result = {'configuration':False, 'dispatcher':False, 'providerCredentialPresent':False, 'pdfRuntime':False}
    try:
        validate_production(settings)
        if not durable_selected(): raise ValueError()
        require_https_url(os.getenv('PRICING_SERVICE_URL'), 'Pricing URL')
        result['configuration']=True
        cfg=TaskConfiguration.from_environment()
        principal,audience=service_config('WORKER');service_config('SCANNER')
        result['dispatcher']=(cfg.worker_url==audience and cfg.service_account==principal
            and cfg.queue_path.split('/')[1]==settings.firebase_project_id)
    except Exception:
        pass
    provider=os.getenv('LLM_PROVIDER')
    key_name={'gemini':'GEMINI_API_KEY','openai':'OPENAI_API_KEY'}.get(provider)
    result['providerCredentialPresent']=bool(key_name and os.getenv(key_name))
    try:
        import weasyprint
        result['pdfRuntime']=True
    except (ImportError,OSError):
        pass
    return {'ready':all(result.values()), 'checks':result}
