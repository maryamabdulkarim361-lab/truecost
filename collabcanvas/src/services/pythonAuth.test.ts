import {afterEach,expect,it,vi} from 'vitest';
import {auth} from './firebase';
import {pythonAuthHeaders} from './pythonAuth';
import {generatePDF} from './pdfService';
afterEach(()=>vi.unstubAllGlobals());
it('requires a signed-in user without network',async()=>{
 Object.assign(auth,{currentUser:null});vi.stubGlobal('fetch',vi.fn());
 await expect(pythonAuthHeaders()).rejects.toThrow('Sign in required');
 expect((await generatePDF('e',false)).success).toBe(false);expect(fetch).not.toHaveBeenCalled();
});
it('sanitizes token acquisition failures',async()=>{
 Object.assign(auth,{currentUser:{getIdToken:async()=>{throw new Error('unsafe authentication detail');}}});
 await expect(pythonAuthHeaders()).rejects.toThrow('Authentication unavailable');
});
it('PDF sends a bearer header without logging it',async()=>{
 Object.assign(auth,{currentUser:{getIdToken:async()=> 'offline-only-token'}});
 const logs=vi.spyOn(console,'log').mockImplementation(()=>{});
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({success:true,pdf_url:'offline.pdf'})})));
 await generatePDF('e',false);
 const header=(vi.mocked(fetch).mock.calls[0][1]!.headers as Record<string,string>).Authorization;
 expect(header.startsWith('Bearer ')).toBe(true);
 expect(JSON.stringify(logs.mock.calls).includes('offline-only-token')).toBe(false);logs.mockRestore();
});
