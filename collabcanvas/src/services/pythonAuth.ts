import { auth } from './firebase';

export async function pythonAuthHeaders(): Promise<Record<string, string>> {
  if (!auth.currentUser) throw new Error('Sign in required');
  try {
    const token = await auth.currentUser.getIdToken();
    if (!token) throw new Error();
    return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
  } catch {
    throw new Error('Authentication unavailable');
  }
}
