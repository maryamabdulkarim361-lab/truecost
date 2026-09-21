const dns=require('node:dns');const net=require('node:net');
const local=h=>!h||['localhost','127.0.0.1','::1','[::1]','0.0.0.0'].includes(h);
const lookup=dns.lookup;dns.lookup=function(host,...args){if(!local(host))throw new Error('External networking prohibited');return lookup.call(this,host,...args);};
const connect=net.Socket.prototype.connect;net.Socket.prototype.connect=function(...args){
 let a=args[0];if(Array.isArray(a))a=a[0];
 const host=typeof a==='object'?a.host:(typeof args[1]==='string'?args[1]:undefined);
 if(!local(host))throw new Error('External networking prohibited');return connect.apply(this,args);
};
