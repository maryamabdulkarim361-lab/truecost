import { getPythonFunctionsUrl } from './pythonFunctions';
import { initializeApp } from 'firebase/app';
import type { FirebaseApp } from 'firebase/app';
import { getAuth, connectAuthEmulator } from 'firebase/auth';
import type { Auth } from 'firebase/auth';
import { getFirestore, connectFirestoreEmulator, enableNetwork, disableNetwork } from 'firebase/firestore';
import type { Firestore } from 'firebase/firestore';
import { getDatabase, connectDatabaseEmulator, goOnline, goOffline } from 'firebase/database';
import type { Database } from 'firebase/database';
import { getFunctions, connectFunctionsEmulator } from 'firebase/functions';
import type { Functions } from 'firebase/functions';
import { getStorage, connectStorageEmulator } from 'firebase/storage';
import type { FirebaseStorage } from 'firebase/storage';

// Firebase configuration from environment variables
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  databaseURL: import.meta.env.VITE_FIREBASE_DATABASE_URL,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

// Emulator configuration - VITE_USE_FIREBASE_EMULATORS enables ALL emulators
// Individual flags can override to disable specific emulators
const useEmulators = import.meta.env.VITE_USE_FIREBASE_EMULATORS === 'true';
const useAuthEmulator = import.meta.env.VITE_USE_AUTH_EMULATOR !== 'false' && useEmulators;
const useFunctionsEmulator = import.meta.env.VITE_USE_FUNCTIONS_EMULATOR !== 'false' && useEmulators;
const useFirestoreEmulator = import.meta.env.VITE_USE_FIRESTORE_EMULATOR !== 'false' && useEmulators;

// Guard against multiple emulator connections (HMR can cause this)
declare global {
  interface Window {
    __FIREBASE_EMULATORS_CONNECTED__?: boolean;
  }
}
const emulatorsAlreadyConnected = typeof window !== 'undefined' && window.__FIREBASE_EMULATORS_CONNECTED__;

// Debug: Log the config (only in development, once)
if (import.meta.env.DEV && !emulatorsAlreadyConnected) {
  console.log('🔥 Firebase Config:', {
    projectId: firebaseConfig.projectId,
    useEmulators,
    useAuthEmulator,
    useFunctionsEmulator,
    useFirestoreEmulator,
  });
}

// Fail before SDK initialization in production builds with development configuration.
if (import.meta.env.PROD) {
  getPythonFunctionsUrl();
  if (Object.values(firebaseConfig).some(value => !value)) {
    throw new Error('Complete production Firebase configuration is required');
  }
}

// Initialize Firebase app
export const app: FirebaseApp = initializeApp(firebaseConfig);

// Initialize Firebase services
export const auth: Auth = getAuth(app);
export const firestore: Firestore = getFirestore(app);
export const rtdb: Database = getDatabase(app);
export const functions: Functions = getFunctions(app, 'us-central1');
export const storage: FirebaseStorage = getStorage(app);

// Export network control functions for offline handling
export const enableFirestoreNetwork = () => enableNetwork(firestore);
export const disableFirestoreNetwork = () => disableNetwork(firestore);
export const enableRTDBNetwork = () => goOnline(rtdb);
export const disableRTDBNetwork = () => goOffline(rtdb);

// Connect to emulators (only once, guarded for HMR)
if (useEmulators && !emulatorsAlreadyConnected) {
  console.log('🔧 Connecting to Firebase Emulators...');

  try {
    // Firestore emulator (port 8081)
    if (useFirestoreEmulator) {
      connectFirestoreEmulator(firestore, 'localhost', 8081);
      console.log('  ✅ Firestore emulator (localhost:8081)');
    }

    // RTDB emulator (port 9000)
    connectDatabaseEmulator(rtdb, 'localhost', 9000);
    console.log('  ✅ RTDB emulator (localhost:9000)');

    // Storage emulator (port 9199)
    connectStorageEmulator(storage, 'localhost', 9199);
    console.log('  ✅ Storage emulator (localhost:9199)');

    // Auth emulator (port 9099)
    if (useAuthEmulator) {
      connectAuthEmulator(auth, 'http://localhost:9099', { disableWarnings: true });
      console.log('  ✅ Auth emulator (localhost:9099)');
    }

    // Functions emulator (port 5001)
    if (useFunctionsEmulator) {
      connectFunctionsEmulator(functions, 'localhost', 5001);
      console.log('  ✅ Functions emulator (localhost:5001)');
    }

    // Mark as connected to prevent re-connection on HMR
    if (typeof window !== 'undefined') {
      window.__FIREBASE_EMULATORS_CONNECTED__ = true;
    }

    console.log('🔧 Firebase Emulators connected successfully!');
  } catch (error) {
    console.error('❌ Failed to connect to Firebase Emulators:', error);
  }
}

export default app;

