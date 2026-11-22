import{r}from"./react-Dny5FRj7.js";/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const X=t=>t==null?void 0:t.replace(/([a-z0-9])([A-Z])/g,"$1-$2").toLowerCase();/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */function Y(t,e,a=[]){if(e==null)throw new Error("[lucide]: iconNode is required when icon name is used");return{name:X(t),size:24,node:e,...a.length>0?{aliases:a}:{}}}/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ee=t=>{let e="",a=!1;for(const o of t){if(o==="-"||o==="_"||o<=" "){a=e.length>0;continue}e.length===0?e+=o.toLowerCase():e+=a?o.toUpperCase():o,a=!1}return e};/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const te=t=>{const e=ee(t);return e.charAt(0).toUpperCase()+e.slice(1)};/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const z=(...t)=>{const e=new Set;for(const a of t)if(typeof a=="string")for(const o of a.split(/\s+/))o&&e.add(o);return[...e].join(" ")};/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const c={xmlns:"http://www.w3.org/2000/svg",width:24,height:24,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":2,"stroke-linecap":"round","stroke-linejoin":"round"};/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */function _(t){return t!=null}function oe(t,e={}){var m,f;const a=e.attributeNames??{},o=s=>a[s]??s,h=t.size??t.width??c.width,k=t.size??t.height??c.height,y=((m=t.aliases)==null?void 0:m.filter(s=>typeof s=="string"&&s.trim()!=="").map(s=>`lucide-${s}`))??[],u=[...t.name?[`lucide-${t.name}`]:[],...y],i=((f=e.className)==null?void 0:f.split(" ").filter(Boolean))??[],p=e.includeDefaultClasses===!1?z(...i):z("lucide",...u,...i),x=e.absoluteStrokeWidth?Number(e.strokeWidth??c["stroke-width"])*Number(t.size??t.width??c.width)/Number(e.size??e.width??c.width):e.strokeWidth??c["stroke-width"];return["svg",{...Object.entries(c).reduce((s,[d,l])=>(s[o(d)]=l,s),{}),..."color"in e&&e.color&&{[o("stroke")]:e.color},..."size"in e&&_(e.size)&&{[o("width")]:e.size,[o("height")]:e.size},..."width"in e&&_(e.width)&&{[o("width")]:e.width},..."height"in e&&_(e.height)&&{[o("height")]:e.height},[o("stroke-width")]:x,...p&&{[o("class")]:p},[o("viewBox")]:`0 0 ${h} ${k}`,...e.hasA11yProp===!1?{[o("aria-hidden")]:"true"}:{},..."attributes"in e&&e.attributes},t.node.map(s=>{const[d,l,v]=s,w=e.nonScalingStroke?{[o("vector-effect")]:"non-scaling-stroke",...l}:l;return v?[d,w,v]:[d,w]})]}/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */function ne(t,e={}){return oe(t,{...e,attributeNames:{...e.attributeNames,class:"className","stroke-width":"strokeWidth","stroke-linecap":"strokeLinecap","stroke-linejoin":"strokeLinejoin","vector-effect":"vectorEffect"}})}/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ae=t=>{if(t==null||typeof t!="object")return!1;for(const e in t)if(Object.prototype.hasOwnProperty.call(t,e)&&(e.startsWith("aria-")||e==="role"||e==="title")&&t[e]!=null)return!0;return!1},se=r.createContext({}),ie=()=>r.useContext(se),ce=r.forwardRef(({color:t,size:e,width:a,height:o,strokeWidth:h,absoluteStrokeWidth:k,nonScalingStroke:y,className:u="",children:i,iconNode:p=[],icon:x={node:p,aliases:[],size:24},...g},m)=>{const{size:f=24,strokeWidth:s=2,absoluteStrokeWidth:d=!1,nonScalingStroke:l=!1,color:v="currentColor",className:w=""}=ie()??{},O=!!i||ae(g),[K,Z,G=[]]=ne(x,{color:t??v,width:a??e??f,height:o??e??f,strokeWidth:h??s,absoluteStrokeWidth:k??d,nonScalingStroke:y??l,className:z(w,u),hasA11yProp:O,attributes:g});return r.createElement(K,{ref:m,...Z},[...G.map(([J,Q])=>r.createElement(J,Q)),...Array.isArray(i)?i:[i]])});/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */function n(t,e=[],a=[]){const o=typeof t=="string"?Y(t,e,a):t,h=r.forwardRef(({className:k,...y},u)=>r.createElement(ce,{ref:u,icon:o,className:k,...y}));return o.name&&(h.displayName=te(o.name)),h}/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const b={name:"activity",size:24,node:[["path",{d:"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2",key:"169zse"}]]};b.node;const he=n(b);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const M={name:"bell",size:24,node:[["path",{d:"M10.268 21a2 2 0 0 0 3.464 0",key:"vwvbt9"}],["path",{d:"M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326",key:"11g9vi"}]]};M.node;const de=n(M);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const C={name:"book-open",size:24,node:[["path",{d:"M12 5v16",key:"1f6ucr"}],["path",{d:"M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z",key:"1fyvmf"}]]};C.node;const le=n(C);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const $={name:"bot",size:24,node:[["path",{d:"M12 8V4H8",key:"hb8ula"}],["rect",{width:"16",height:"12",x:"4",y:"8",rx:"2",key:"enze0r"}],["path",{d:"M2 14h2",key:"vft8re"}],["path",{d:"M20 14h2",key:"4cs60a"}],["path",{d:"M15 13v2",key:"1xurst"}],["path",{d:"M9 13v2",key:"rq6x2g"}]]};$.node;const ke=n($);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const A={name:"building-complex",size:24,node:[["path",{d:"M10 12h4",key:"a56b0p"}],["path",{d:"M10 8h4",key:"1sr2af"}],["path",{d:"M14 21v-3a2 2 0 0 0-4 0v3",key:"1rgiei"}],["path",{d:"M6 10H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-2",key:"secmi2"}],["path",{d:"M6 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16",key:"16ra0t"}]],aliases:["building-2"]};A.node;const ye=n(A);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const D={name:"chart-line",size:24,node:[["path",{d:"M3 3v16a2 2 0 0 0 2 2h16",key:"c24i48"}],["path",{d:"m19 9-5 5-4-4-3 3",key:"2osh9i"}]],aliases:["line-chart"]};D.node;const ue=n(D);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const L={name:"chevrons-left",size:24,node:[["path",{d:"m11 17-5-5 5-5",key:"13zhaf"}],["path",{d:"m18 17-5-5 5-5",key:"h8a8et"}]]};L.node;const fe=n(L);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const N={name:"chevrons-right",size:24,node:[["path",{d:"m6 17 5-5-5-5",key:"xnjwq"}],["path",{d:"m13 17 5-5-5-5",key:"17xmmf"}]]};N.node;const pe=n(N);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const S={name:"circle-check",size:24,node:[["circle",{cx:"12",cy:"12",r:"10",key:"1mglay"}],["path",{d:"m16 9-5.5 5.5L8 12",key:"xofnsj"}]],aliases:["check-circle-2"]};S.node;const me=n(S);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const j={name:"house",size:24,node:[["path",{d:"M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8",key:"5wwlr5"}],["path",{d:"M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",key:"r6nss1"}]],aliases:["home"]};j.node;const ve=n(j);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const W={name:"inbox",size:24,node:[["polyline",{points:"22 12 16 12 14 15 10 15 8 12 2 12",key:"o97t9d"}],["path",{d:"M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z",key:"oot6mr"}]]};W.node;const we=n(W);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const H={name:"list-todo",size:24,node:[["path",{d:"M13 5h8",key:"a7qcls"}],["path",{d:"M13 12h8",key:"h98zly"}],["path",{d:"M13 19h8",key:"c3s6r1"}],["path",{d:"m3 17 2 2 4-4",key:"1jhpwq"}],["rect",{x:"3",y:"4",width:"6",height:"6",rx:"1",key:"cif1o7"}]]};H.node;const xe=n(H);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const B={name:"log-in",size:24,node:[["path",{d:"m10 17 5-5-5-5",key:"1bsop3"}],["path",{d:"M15 12H3",key:"6jk70r"}],["path",{d:"M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4",key:"u53s6r"}]]};B.node;const ge=n(B);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const q={name:"play",size:24,node:[["path",{d:"M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z",key:"10ikf1"}]]};q.node;const _e=n(q);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const I={name:"refresh-cw",size:24,node:[["path",{d:"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8",key:"v9h5vc"}],["path",{d:"M21 3v5h-5",key:"1q7to0"}],["path",{d:"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16",key:"3uifl3"}],["path",{d:"M8 16H3v5",key:"1cv678"}]]};I.node;const ze=n(I);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const P={name:"search",size:24,node:[["path",{d:"m21 21-4.34-4.34",key:"14j7rj"}],["circle",{cx:"11",cy:"11",r:"8",key:"4ej97u"}]]};P.node;const be=n(P);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const R={name:"settings",size:24,node:[["path",{d:"M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915",key:"1i5ecw"}],["circle",{cx:"12",cy:"12",r:"3",key:"1v7zrd"}]]};R.node;const Me=n(R);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const E={name:"timer",size:24,node:[["line",{x1:"10",x2:"14",y1:"2",y2:"2",key:"14vaq8"}],["line",{x1:"12",x2:"15",y1:"14",y2:"11",key:"17fdiu"}],["circle",{cx:"12",cy:"14",r:"8",key:"1e1u0o"}]]};E.node;const Ce=n(E);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const U={name:"user-round",size:24,node:[["circle",{cx:"12",cy:"8",r:"5",key:"1hypcn"}],["path",{d:"M20 21a8 8 0 0 0-16 0",key:"rfgkzh"}]],aliases:["user-2"]};U.node;const $e=n(U);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const V={name:"users",size:24,node:[["path",{d:"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2",key:"1yyitq"}],["path",{d:"M16 3.128a4 4 0 0 1 0 7.744",key:"16gr8j"}],["path",{d:"M22 21v-2a4 4 0 0 0-3-3.87",key:"kshegd"}],["circle",{cx:"9",cy:"7",r:"4",key:"nufk8"}]]};V.node;const Ae=n(V);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const T={name:"wallet",size:24,node:[["path",{d:"M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1",key:"18etb6"}],["path",{d:"M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4",key:"xoc0q4"}]]};T.node;const De=n(T);/**
 * @license lucide-react v1.75.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const F={name:"workflow",size:24,node:[["rect",{width:"8",height:"8",x:"3",y:"3",rx:"2",key:"by2w9f"}],["path",{d:"M7 11v4a2 2 0 0 0 2 2h4",key:"xkn7yn"}],["rect",{width:"8",height:"8",x:"13",y:"13",rx:"2",key:"1cgmvn"}]]};F.node;const Le=n(F);export{he as A,ke as B,me as C,ve as H,we as I,xe as L,_e as P,ze as R,Me as S,Ce as T,Ae as U,De as W,ue as a,Le as b,le as c,ye as d,pe as e,fe as f,be as g,de as h,ge as i,$e as j};
