import {describe,it,expect,vi,afterEach} from 'vitest';
import {verifyPricingService} from './serviceIdentity';

afterEach(()=>vi.unstubAllEnvs());
function setup() {
 vi.stubEnv('PRICING_SERVICE_URL','https://pricing.example.com/comparePricesService');
 vi.stubEnv('PRICING_SERVICE_INVOKER','worker@project.iam.gserviceaccount.com');
 return {iss:'https://accounts.google.com',aud:'https://pricing.example.com/comparePricesService',
   email:'worker@project.iam.gserviceaccount.com',email_verified:true,sub:'123',iat:1,exp:9999999999};
}
describe('private pricing identity',()=> {
 it('accepts verified service only',async()=> {const c=setup(); await expect(verifyPricingService('Bearer fake',async()=>c)).resolves.toBeUndefined();});
 for(const key of ['aud','email','iss','sub']) it(`rejects wrong ${key}`,async()=> {
   const c=setup(); await expect(verifyPricingService('Bearer fake',async()=>({...c,[key]:key==='sub'?'':'wrong'}))).rejects.toThrow();
 });
 it('rejects unverified email',async()=> {const c=setup(); await expect(verifyPricingService('Bearer fake',async()=>({...c,email_verified:false}))).rejects.toThrow();});
 it('rejects missing auth before verifier',async()=> {setup(); const v=vi.fn(); await expect(verifyPricingService(undefined,v)).rejects.toThrow(); expect(v).not.toHaveBeenCalled();});
 it('rejects localhost',async()=> {setup();vi.stubEnv('PRICING_SERVICE_URL','http://127.0.0.1:5001'); await expect(verifyPricingService('Bearer fake',vi.fn())).rejects.toThrow();});
 it('rejects missing configuration',async()=> {vi.stubEnv('PRICING_SERVICE_URL',''); await expect(verifyPricingService('Bearer fake',vi.fn())).rejects.toThrow();});
});
