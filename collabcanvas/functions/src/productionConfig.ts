/** Non-secret deployment checks; no network or credential access. */
export function isProduction(): boolean {
  return process.env.APP_ENV === 'production' || Boolean(process.env.K_SERVICE);
}
export function productionPythonUrl(): string {
  const env = process.env;
  if (['FUNCTIONS_EMULATOR','USE_FIREBASE_EMULATORS','DISABLE_OPENAI'].some(k => /^(true|1|yes|on)$/i.test(env[k] || '')) ||
      ['FIRESTORE_EMULATOR_HOST','FIREBASE_AUTH_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST','STORAGE_EMULATOR_HOST','FIREBASE_DATABASE_EMULATOR_HOST'].some(k => Boolean(env[k]))) {
    throw new Error('Local/emulator configuration is forbidden in production');
  }
  const project = env.GCLOUD_PROJECT || env.GOOGLE_CLOUD_PROJECT;
  if (!project || project === 'collabcanvas-dev' || project.startsWith('demo-')) {
    throw new Error('An explicit production Firebase project is required');
  }
  const url = new URL(env.PYTHON_FUNCTIONS_URL || '');
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.port ||
      url.hostname === 'localhost' || url.hostname.endsWith('.localhost') || /^[\d.]+$/.test(url.hostname) ||
      url.hostname.includes(':') || url.hostname.includes('collabcanvas-dev') || url.pathname.includes('collabcanvas-dev')) {
    throw new Error('PYTHON_FUNCTIONS_URL must be a production HTTPS URL');
  }
  return url.href.replace(/\/+$/, '').replace(/\/start_deep_pipeline$/, '');
}
