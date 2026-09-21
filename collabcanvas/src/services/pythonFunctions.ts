/** Python HTTP functions are separate from the Node Functions emulator. */
export function getPythonFunctionsUrl(): string {
  if (import.meta.env.PROD) {
    const projectId = import.meta.env.VITE_FIREBASE_PROJECT_ID;
    if (import.meta.env.VITE_USE_FIREBASE_EMULATORS === 'true' || !projectId || projectId === 'collabcanvas-dev' || projectId.startsWith('demo-')) {
      throw new Error('Production requires explicit non-emulator Firebase configuration');
    }
    const configured = import.meta.env.VITE_PYTHON_FUNCTIONS_URL;
    const url = new URL(configured || '');
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.port ||
        url.hostname === 'localhost' || url.hostname.endsWith('.localhost') || /^[\d.]+$/.test(url.hostname) ||
        url.hostname.includes(':') || url.hostname.includes('collabcanvas-dev') || url.pathname.includes('collabcanvas-dev')) {
      throw new Error('Production requires an explicit HTTPS Python endpoint');
    }
  }
  const project = import.meta.env.VITE_FIREBASE_PROJECT_ID || 'collabcanvas-dev';
  const override = import.meta.env.VITE_PYTHON_FUNCTIONS_URL;
  if (override) {
    const base = override.replace(/\/+$/, '').replace(/\/(start_deep_pipeline|generate_pdf)$/, '');
    const url = new URL(base);
    return url.pathname === '/' && ['localhost', '127.0.0.1'].includes(url.hostname) ? `${base}/${project}/us-central1` : base;
  }
  return import.meta.env.VITE_USE_FIREBASE_EMULATORS === 'true'
    ? `http://127.0.0.1:5003/${project}/us-central1`
    : `https://us-central1-${project}.cloudfunctions.net`;
}
