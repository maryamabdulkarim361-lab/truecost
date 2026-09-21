import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {isProduction,productionPythonUrl} from './productionConfig';
beforeEach(()=>{
 for(const key of ['K_SERVICE','FUNCTIONS_EMULATOR','USE_FIREBASE_EMULATORS','DISABLE_OPENAI','FIRESTORE_EMULATOR_HOST','FIREBASE_AUTH_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST','STORAGE_EMULATOR_HOST','FIREBASE_DATABASE_EMULATOR_HOST']) vi.stubEnv(key,'');
 vi.stubEnv('APP_ENV','production');vi.stubEnv('GCLOUD_PROJECT','example-production');
 vi.stubEnv('PYTHON_FUNCTIONS_URL','https://us-central1-example-production.cloudfunctions.net');
 vi.stubGlobal('fetch',()=>{throw new Error('Network forbidden');});
});
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();});
it('accepts explicit production configuration',()=>{expect(isProduction()).toBe(true);expect(productionPythonUrl()).toContain('example-production');});
it.each(['FUNCTIONS_EMULATOR','USE_FIREBASE_EMULATORS','DISABLE_OPENAI','FIRESTORE_EMULATOR_HOST','FIREBASE_AUTH_EMULATOR_HOST'])('rejects %s',key=>{vi.stubEnv(key,'true');expect(()=>productionPythonUrl()).toThrow();});
it.each(['','http://127.0.0.1:5003','https://us-central1-collabcanvas-dev.cloudfunctions.net','https://example.com/collabcanvas-dev/us-central1','https://fake:fake@example.com'])('rejects unsafe endpoint %s',url=>{vi.stubEnv('PYTHON_FUNCTIONS_URL',url);expect(()=>productionPythonUrl()).toThrow();});
it('requires production project',()=>{vi.stubEnv('GCLOUD_PROJECT','collabcanvas-dev');expect(()=>productionPythonUrl()).toThrow();});
it('cannot override managed runtime with local mode',()=>{vi.stubEnv('APP_ENV','development');vi.stubEnv('K_SERVICE','fake');expect(isProduction()).toBe(true);});
