/** Google service identity only; never Firebase browser identity or shared keys. */
import {OAuth2Client, TokenPayload} from 'google-auth-library';

export function pricingIdentityConfig(): {audience:string; principal:string} {
  const audience=process.env.PRICING_SERVICE_URL || '';
  const principal=process.env.PRICING_SERVICE_INVOKER || '';
  const url=new URL(audience);
  if(url.protocol!=='https:' || url.username || url.password || url.search || url.hash ||
    url.port || url.hostname==='localhost' || url.hostname.endsWith('.localhost') ||
    /^[\d.]+$/.test(url.hostname) || url.hostname.includes(':') || audience.includes('collabcanvas-dev') ||
    !/^[a-z][a-z0-9-]*@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com$/.test(principal)) {
    throw new Error('Pricing service configuration required');
  }
  return {audience,principal};
}

export async function verifyPricingService(header:unknown,
  verify?: (token:string,audience:string)=>Promise<TokenPayload|undefined>): Promise<void> {
  const {audience,principal}=pricingIdentityConfig();
  if(typeof header!=='string' || !/^Bearer \S+$/i.test(header)) throw new Error('Service authentication required');
  const token=header.split(' ')[1];
  const verifier=verify || (async (idToken:string, target:string)=> {
    const client=new OAuth2Client();
    client.transporter.defaults={timeout:5000,retry:false};
    return (await client.verifyIdToken({idToken,audience:target})).getPayload();
  });
  try {
    const claims=await verifier(token,audience);
    if(!claims || !['https://accounts.google.com','accounts.google.com'].includes(claims.iss) ||
       claims.aud!==audience || claims.email!==principal || claims.email_verified!==true || !claims.sub) {
      throw new Error();
    }
  } catch { throw new Error('Service authentication rejected'); }
}
