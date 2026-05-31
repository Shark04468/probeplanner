/**
 * Web Worker source code (serialized as Blob URL).
 *
 * Contains self-contained versions of:
 *   - Vector math
 *   - Trajectory builder with channel-aware coverage
 *   - Smart candidate generator (centroid + pair + grid strategies)
 *   - Greedy + B&B optimizer with LP relaxation
 *   - Coverage boost
 *
 * All brain region data and probe presets are passed in via the message payload.
 */

export function getWorkerSource() {
  return `
/* ═══════ VECTOR MATH ═══════ */
const v3={
  sub:(a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]],
  add:(a,b)=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]],
  dot:(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2],
  scale:(a,s)=>[a[0]*s,a[1]*s,a[2]*s],
  norm:(a)=>Math.sqrt(a[0]*a[0]+a[1]*a[1]+a[2]*a[2]),
  normalize:(a)=>{const n=Math.sqrt(a[0]*a[0]+a[1]*a[1]+a[2]*a[2]);return n<1e-12?[0,0,0]:[a[0]/n,a[1]/n,a[2]/n];},
};
function segSegDist(P0,P1,Q0,Q1){
  const u=v3.sub(P1,P0),vv=v3.sub(Q1,Q0),w0=v3.sub(P0,Q0);
  const a=v3.dot(u,u),b=v3.dot(u,vv),c=v3.dot(vv,vv),d=v3.dot(u,w0),e=v3.dot(vv,w0);
  const D=a*c-b*b,EPS=1e-12;
  let sN,sD=D,tN,tD=D;
  if(D<EPS){sN=0;sD=1;tN=e;tD=c;}
  else{sN=b*e-c*d;tN=a*e-b*d;
    if(sN<0){sN=0;tN=e;tD=c;}else if(sN>sD){sN=sD;tN=e+b;tD=c;}}
  if(tN<0){tN=0;if(-d<0)sN=0;else if(-d>a)sN=sD;else{sN=-d;sD=a;}}
  else if(tN>tD){tN=tD;const tmp=-d+b;if(tmp<0)sN=0;else if(tmp>a)sN=sD;else{sN=tmp;sD=a;}}
  const s=Math.abs(sN)<EPS?0:sN/sD,t=Math.abs(tN)<EPS?0:tN/tD;
  return v3.norm(v3.sub(v3.add(w0,v3.scale(u,s)),v3.scale(vv,t)));
}
function ptSegDist(P,A,B){
  const u=v3.sub(B,A),w=v3.sub(P,A),lsq=v3.dot(u,u);
  if(lsq<1e-12)return v3.norm(w);
  const t=Math.max(0,Math.min(1,v3.dot(w,u)/lsq));
  return v3.norm(v3.sub(P,v3.add(A,v3.scale(u,t))));
}

/* ═══════ COORDINATE HELPERS ═══════ */
const BREGMA_UM=[5400,440,5700];
function regionToXYZ(reg){return[5400+reg.ap*1000,440+reg.dv*1000,5700+reg.ml*1000];}

function makeTrajectory(ap_mm,ml_mm,theta_deg,phi_deg,depthUm=10000,skullTiltDeg=0){
  const tiltRad=skullTiltDeg*Math.PI/180;
  const dvCorrUm=-ap_mm*Math.tan(tiltRad)*1000;
  const entry=[5400-ap_mm*1000, 440+dvCorrUm, 5700+ml_mm*1000];
  const th=theta_deg*Math.PI/180, ph=phi_deg*Math.PI/180;
  const dir=[Math.sin(th)*Math.cos(ph),Math.cos(th),Math.sin(th)*Math.sin(ph)];
  const tip=[entry[0]+dir[0]*depthUm,entry[1]+dir[1]*depthUm,entry[2]+dir[2]*depthUm];
  return{entry,tip,dir,theta_deg,phi_deg,ap_mm,ml_mm,depthUm,skullTiltDeg,
    entryDvCorr_mm:(-dvCorrUm/1000)};
}

/* ═══════ CHANNEL-AWARE COVERAGE ═══════ */
function computeChannelCoverage(traj,targetKeys,BRAIN_REGIONS,probeConfig){
  const dirN=v3.normalize(traj.dir);
  const {channelsPerShank=384,electrodePitchUm=15,electrodeColumns=2,shanks=1,shankPitchUm=250}=probeConfig||{};
  const electrodesPerCol=Math.ceil(channelsPerShank/electrodeColumns);

  // Simplified electrode positions (along main axis from tip upward)
  const electrodePositions=[];
  for(let ei=0;ei<electrodesPerCol;ei++){
    const distFromTip=ei*electrodePitchUm;
    const pos=v3.sub(traj.tip,v3.scale(dirN,distFromTip));
    electrodePositions.push({pos,depthUm:traj.depthUm-distFromTip});
  }

  // Region radii and DV spans (hardcoded subset for worker)
  const RADII={VISp:700,VISl:550,VISpm:450,VISal:500,VISrl:450,LGd:400,SCsg:500,SCm:450,MRN:350,
    CA1:600,CA3:450,DG:500,ENTl:550,RSP:600,LP:400,VPM:400,VPL:450,MD:350,PO:350,
    MOp:700,MOs:650,SSp:900,PFC:500,ACA:400,PL:400,CP:1100,ACB:500,GPe:500};
  const DV_SPANS={VISp:0.80,VISl:0.70,VISpm:0.60,VISal:0.65,VISrl:0.55,LGd:0.65,SCsg:0.50,SCm:0.55,
    MRN:0.45,CA1:0.50,CA3:0.40,DG:0.55,ENTl:0.70,RSP:0.65,LP:0.55,VPM:0.50,VPL:0.55,
    MD:0.45,PO:0.40,MOp:0.85,MOs:0.75,SSp:1.00,PFC:0.70,ACA:0.55,PL:0.50,CP:1.40,ACB:0.60,GPe:0.60};

  const coveredKeys=[];
  const perRegion={};
  for(const key of targetKeys){
    const reg=BRAIN_REGIONS[key]; if(!reg){perRegion[key]={covered:false,channelCount:0,score:0};continue;}
    const centroid=regionToXYZ(reg);
    const radiusUm=RADII[key]||500;
    const dvSpanMm=DV_SPANS[key]||0.6;
    const semiDV=dvSpanMm*1000/2, semiAP=radiusUm, semiML=radiusUm;

    let channelCount=0;
    for(const elec of electrodePositions){
      const dx=(elec.pos[0]-centroid[0])/semiAP;
      const dy=(elec.pos[1]-centroid[1])/semiDV;
      const dz=(elec.pos[2]-centroid[2])/semiML;
      if(dx*dx+dy*dy+dz*dz<=1.0) channelCount++;
    }

    // Also check centroid distance as fallback
    const centroidDist=ptSegDist(centroid,traj.entry,traj.tip);
    const covered=channelCount>=5||(channelCount>0&&centroidDist<=radiusUm*1.2);
    // Also accept pure centroid proximity for backward compat
    const centroidCovered=centroidDist<=radiusUm*1.3;

    const finalCovered=covered||centroidCovered;
    const targetCh=Math.max(10,Math.round(dvSpanMm*1000/(electrodePitchUm||15)));
    // qualityFactor: penalise trajectories that only graze the region peripherally
    const qualityFactor=finalCovered?Math.max(0.1,1-centroidDist/(radiusUm*2)):0;
    const rawScore=finalCovered?Math.min(1.0,Math.max(channelCount/targetCh,centroidCovered?0.3:0)):0;
    const score=rawScore*qualityFactor;

    perRegion[key]={covered:finalCovered,channelCount,score:Math.round(score*1000)/1000,
      qualityFactor:Math.round(qualityFactor*100)/100,
      centroidDistUm:Math.round(centroidDist)};
    if(finalCovered) coveredKeys.push(key);
  }

  return{perRegion,coveredKeys};
}

/* ═══════ COLLISION CHECK ═══════ */
function trajsCollide(a,b,minClearance,minEntry=3500){
  if(v3.norm(v3.sub(a.entry,b.entry))<minEntry)return true;
  return segSegDist(a.entry,a.tip,b.entry,b.tip)<minClearance;
}

/* ═══════ CANDIDATE GENERATION — THREE STRATEGIES ═══════ */
function generateCandidates(targetKeys,params,constraintModes,BRAIN_REGIONS,ANATOMY,probeConfig,onProgress){
  const{maxAngle=15,depthUm=10000,bilateral=false,skullTiltDeg=0,gridSpacing=600}=params;
  const gridMm=Math.max(gridSpacing/1000,0.8);

  const all=[];

  // ── Strategy 1: Centroid seeds ──
  const angles=[[0,0]];
  for(let th=5;th<=maxAngle;th+=5) for(let ph=0;ph<360;ph+=45) angles.push([th,ph]);
  const offsets=[[0,0],[0.3,0],[-0.3,0],[0,0.3],[0,-0.3],[0.15,0.15],[-0.15,0.15]];
  for(const key of targetKeys){
    const reg=BRAIN_REGIONS[key]; if(!reg)continue;
    const idealAP=-reg.ap, idealML=reg.ml;
    for(const[dap,dml]of offsets){
      const ap=idealAP+dap, ml=idealML+dml;
      if(constraintModes?.lambdaBoundary==='enforce'&&ap<ANATOMY.lambda_ap_mm)continue;
      for(const[theta,phi]of angles){
        if(theta>maxAngle)continue;
        const traj=makeTrajectory(ap,ml,theta,phi,depthUm,skullTiltDeg);
        if(constraintModes?.brainBounds==='enforce'){
          if(traj.dir[1]*depthUm/1000>ANATOMY.max_dv_mm)continue;
          if(ap+traj.dir[0]*depthUm/1000<ANATOMY.tip_post_limit_mm)continue;
        }
        traj._source='centroid_'+key;
        all.push(traj);
      }
    }
  }
  if(onProgress)onProgress(15);

  // ── Strategy 2: Multi-region pair lines ──
  const centroids={};
  for(const k of targetKeys){const r=BRAIN_REGIONS[k];if(r)centroids[k]=regionToXYZ(r);}
  const keys=Object.keys(centroids);
  for(let i=0;i<keys.length;i++){
    for(let j=i+1;j<keys.length;j++){
      const ca=centroids[keys[i]],cb=centroids[keys[j]];
      let dir,shallow; if(ca[1]<cb[1]){dir=v3.sub(cb,ca);shallow=ca;}else{dir=v3.sub(ca,cb);shallow=cb;}
      if(Math.abs(dir[1])<200)continue;
      const dirN=v3.normalize(dir);
      const theta=Math.acos(Math.min(1,Math.abs(dirN[1])))*180/Math.PI;
      if(theta>maxAngle)continue;
      if(Math.abs(dirN[1])<0.1)continue;
      const tToSkull=(BREGMA_UM[1]-shallow[1])/dirN[1];
      const entryUm=v3.add(shallow,v3.scale(dirN,tToSkull));
      const ap_mm=(BREGMA_UM[0]-entryUm[0])/1000;
      const ml_mm=(entryUm[2]-BREGMA_UM[2])/1000;
      if(constraintModes?.lambdaBoundary==='enforce'&&ap_mm<ANATOMY.lambda_ap_mm)continue;
      const phi=(Math.atan2(dirN[2],dirN[0])*180/Math.PI+360)%360;
      const depth=Math.min(v3.norm(v3.sub(cb,entryUm))+1000,depthUm);
      const traj=makeTrajectory(ap_mm,ml_mm,theta,phi,depth,skullTiltDeg);
      traj._source='pair_'+keys[i]+'_'+keys[j];
      traj._priority='high';
      all.push(traj);
      // Perturbations
      for(const dth of[-3,3])for(const dph of[-15,15]){
        const t2=Math.max(0,Math.min(maxAngle,theta+dth));
        const p2=(phi+dph+360)%360;
        const tj=makeTrajectory(ap_mm,ml_mm,t2,p2,depth,skullTiltDeg);
        tj._source='pair_perturb';all.push(tj);
      }
    }
  }
  if(onProgress)onProgress(25);

  // ── Strategy 3: Sparse grid ──
  const regions=targetKeys.map(k=>BRAIN_REGIONS[k]).filter(Boolean);
  let apVals=regions.map(r=>-r.ap),mlVals=regions.map(r=>r.ml);
  if(bilateral)mlVals=[...mlVals,...regions.map(r=>-r.ml)];
  const margin=1.5;
  let apMin=Math.min(...apVals)-margin,apMax=Math.max(...apVals)+margin;
  let mlMin=Math.min(...mlVals)-margin,mlMax=Math.max(...mlVals)+margin;
  if(constraintModes?.lambdaBoundary==='enforce')apMin=Math.max(apMin,ANATOMY.lambda_ap_mm);
  const gridAngles=[[0,0]];
  for(let th=5;th<=maxAngle;th+=5)for(let ph=0;ph<360;ph+=60)gridAngles.push([th,ph]);
  for(let ap=apMin;ap<=apMax+1e-9;ap+=gridMm){
    if(constraintModes?.lambdaBoundary==='enforce'&&ap<ANATOMY.lambda_ap_mm)continue;
    for(let ml=mlMin;ml<=mlMax+1e-9;ml+=gridMm){
      for(const[theta,phi]of gridAngles){
        if(theta>maxAngle)continue;
        const traj=makeTrajectory(Math.round(ap*1000)/1000,Math.round(ml*1000)/1000,theta,phi,depthUm,skullTiltDeg);
        if(constraintModes?.brainBounds==='enforce'){
          if(traj.dir[1]*depthUm/1000>ANATOMY.max_dv_mm)continue;
        }
        traj._source='grid';
        all.push(traj);
      }
    }
  }
  if(onProgress)onProgress(35);

  // ── Deduplicate ──
  const seen=new Set();const unique=[];
  for(const c of all){
    const key=Math.round(c.ap_mm*10)+'_'+Math.round(c.ml_mm*10)+'_'+Math.round(c.theta_deg)+'_'+Math.round(c.phi_deg/5);
    if(!seen.has(key)){seen.add(key);c.id=unique.length;unique.push(c);}
  }
  if(onProgress)onProgress(40);

  // ── Score with channel-aware coverage ──
  const scored=[];
  for(const traj of unique){
    const coverage=computeChannelCoverage(traj,targetKeys,BRAIN_REGIONS,probeConfig);
    if(coverage.coveredKeys.length===0)continue;

    // Hemisphere check
    if(constraintModes?.hemisphere==='enforce'){
      let bad=false;
      for(const k of coverage.coveredKeys){
        const reg=BRAIN_REGIONS[k];
        if(reg&&Math.abs(reg.ml)>=0.5&&Math.abs(traj.ml_mm)>=0.5&&Math.sign(traj.ml_mm)!==Math.sign(reg.ml)){bad=true;break;}
      }
      if(bad)continue;
    }

    traj.coveredRegions=coverage.coveredKeys;
    traj.channelCoverage=coverage.perRegion;
    let totalScore=0;
    for(const k of coverage.coveredKeys) totalScore+=(coverage.perRegion[k].score||0.3);
    traj.compositeScore=totalScore;
    traj.overlapVoxels={};
    for(const k of coverage.coveredKeys) traj.overlapVoxels[k]=coverage.perRegion[k].channelCount||0;
    scored.push(traj);
  }
  if(onProgress)onProgress(55);

  // ── Diversity reduction ──
  const JACCARD_THRESH=0.6;
  const groups={};
  for(const c of scored){
    const key=Math.round(c.ap_mm*5)+'_'+Math.round(c.ml_mm*5);
    if(!groups[key])groups[key]=[];
    groups[key].push(c);
  }
  const reduced=[];
  for(const group of Object.values(groups)){
    group.sort((a,b)=>b.compositeScore-a.compositeScore||b.coveredRegions.length-a.coveredRegions.length);
    const sel=[group[0]];
    for(let i=1;i<group.length&&sel.length<8;i++){
      const cov=new Set(group[i].coveredRegions);
      const maxJ=Math.max(...sel.map(s=>{
        const sc=new Set(s.coveredRegions);
        const inter=[...cov].filter(x=>sc.has(x)).length;
        const union=new Set([...cov,...sc]).size;
        return union===0?1:inter/union;
      }));
      if(maxJ<JACCARD_THRESH)sel.push(group[i]);
    }
    reduced.push(...sel);
  }
  if(onProgress)onProgress(60);
  return reduced;
}

/* ═══════ GREEDY OPTIMIZER ═══════ */
function greedyOptimize(targetKeys,candidates,minClearance,minEntry){
  let uncovered=new Set(targetKeys);const selected=[];
  while(uncovered.size>0){
    let best=null,bestGain=0,bestScore=-1;
    for(const c of candidates){
      if(selected.includes(c))continue;
      if(selected.some(s=>trajsCollide(c,s,minClearance,minEntry)))continue;
      const newR=c.coveredRegions.filter(r=>uncovered.has(r));
      const n=newR.length; if(n===0)continue;
      let sc=0; for(const r of newR) sc+=(c.channelCoverage?.[r]?.score||0.3);
      if(n>bestGain||(n===bestGain&&sc>bestScore)){bestGain=n;bestScore=sc;best=c;}
    }
    if(!best||!bestGain)break;
    selected.push(best);best.coveredRegions.forEach(r=>uncovered.delete(r));
  }
  return{selected,uncovered:[...uncovered]};
}

/* ═══════ B&B EXACT OPTIMIZER ═══════ */
function milpSolve(targetKeys,candidates,minClearance,minEntry,timeLimitMs=12000,onProgress){
  const n=candidates.length,m=targetKeys.length,BigM=n+1;
  if(n===0)return{selected:[],uncovered:[...targetKeys],milpStatus:'Optimal',milpNodes:0};
  if(onProgress)onProgress(63,'milpBuilding');
  const covIdx=candidates.map(c=>new Set(targetKeys.map((k,i)=>c.coveredRegions.includes(k)?i:-1).filter(i=>i>=0)));
  const covBy=targetKeys.map((_,i)=>candidates.map((_,j)=>covIdx[j].has(i)?j:-1).filter(j=>j>=0));
  const conflicts=Array.from({length:n},()=>new Set());
  let nConflicts=0;
  for(let a=0;a<n;a++)for(let b=a+1;b<n;b++)
    if(trajsCollide(candidates[a],candidates[b],minClearance,minEntry)){
      conflicts[a].add(b);conflicts[b].add(a);nConflicts++;
    }
  self.postMessage({type:'milpInfo',nVars:n+m,nConstraints:m+nConflicts,nCandidates:n,originalCandidates:n});
  if(onProgress)onProgress(66,'milpBuilding');
  function lpBound(nSel,excluded,covered){
    let lb=nSel;for(let i=0;i<m;i++){if(covered.has(i))continue;
    let k=0;for(const j of covBy[i])if(!excluded.has(j))k++;lb+=k===0?BigM:1/k;}return lb;
  }
  const order=Array.from({length:n},(_,i)=>i).sort((a,b)=>(covIdx[b].size/(1+conflicts[b].size))-(covIdx[a].size/(1+conflicts[a].size)));
  const tStart=Date.now();let bestObj=Infinity,bestSel=null,nodes=0;
  function bb(sel,selSet,excl,cov,oPos){
    if(Date.now()-tStart>timeLimitMs)return;nodes++;
    const uncN=m-cov.size,currObj=sel.length+BigM*uncN;
    if(uncN===0){if(currObj<bestObj){bestObj=currObj;bestSel=[...sel];}return;}
    if(lpBound(sel.length,excl,cov)>=bestObj)return;
    let brJ=-1,pos=oPos;while(pos<n){const j=order[pos];if(!excl.has(j)&&!selSet.has(j)){brJ=j;break;}pos++;}
    if(brJ===-1){if(currObj<bestObj){bestObj=currObj;bestSel=[...sel];}return;}
    const ne1=new Set([...excl,...conflicts[brJ]]),nc1=new Set(cov);covIdx[brJ].forEach(i=>nc1.add(i));
    sel.push(brJ);selSet.add(brJ);bb(sel,selSet,ne1,nc1,pos+1);sel.pop();selSet.delete(brJ);
    if(Date.now()-tStart>timeLimitMs)return;
    excl.add(brJ);bb(sel,selSet,excl,cov,pos+1);excl.delete(brJ);
  }
  if(onProgress)onProgress(70,'milpSolving');
  bb([],new Set(),new Set(),new Set(),0);
  const timedOut=Date.now()-tStart>=timeLimitMs;
  const status=!timedOut?'Optimal':(bestSel?'Feasible':'FallbackGreedy');
  if(!bestSel){const g=greedyOptimize(targetKeys,candidates,minClearance,minEntry);g.milpStatus='FallbackGreedy';g.milpNodes=nodes;return g;}
  const selected=bestSel.map(j=>candidates[j]);
  const covSet=new Set(selected.flatMap(c=>c.coveredRegions));
  return{selected,uncovered:targetKeys.filter(k=>!covSet.has(k)),milpStatus:status,milpNodes:nodes};
}

/* ═══════ COVERAGE BOOST ═══════ */
function coverageBoost(selected,uncoveredKeys,targetKeys,minClearance,minEntry,depthUm,constraintModes,skullTiltDeg,BRAIN_REGIONS,ANATOMY,probeConfig){
  if(!uncoveredKeys.length)return{selected,uncovered:[]};
  const boosted=[...selected];let remaining=[...uncoveredKeys];
  const angles=[];
  for(let th=0;th<=15;th+=3){if(th===0){angles.push([0,0]);continue;}for(let ph=0;ph<360;ph+=30)angles.push([th,ph]);}
  const offsets=[[0,0],[0.2,0],[-0.2,0],[0,0.2],[0,-0.2],[0.4,0],[-0.4,0],[0,0.4],[0,-0.4],[0.3,0.3],[-0.3,-0.3]];
  for(const rk of uncoveredKeys){
    if(!remaining.includes(rk))continue;
    const reg=BRAIN_REGIONS[rk];if(!reg)continue;
    const apT=-reg.ap,mlT=reg.ml;
    let found=false;
    outer:for(const[dap,dml]of offsets){
      for(const[theta,phi]of angles){
        const ap=apT+dap,ml=mlT+dml;
        if(constraintModes?.lambdaBoundary==='enforce'&&ap<ANATOMY.lambda_ap_mm)continue;
        const traj=makeTrajectory(ap,ml,theta,phi,depthUm,skullTiltDeg);
        const coverage=computeChannelCoverage(traj,targetKeys,BRAIN_REGIONS,probeConfig);
        traj.coveredRegions=coverage.coveredKeys;
        traj.channelCoverage=coverage.perRegion;
        traj.overlapVoxels={};
        for(const k of coverage.coveredKeys)traj.overlapVoxels[k]=coverage.perRegion[k].channelCount||0;
        if(!traj.coveredRegions.includes(rk))continue;
        if(boosted.some(s=>trajsCollide(traj,s,minClearance,minEntry)))continue;
        if(constraintModes?.hemisphere==='enforce'){
          let bad=false;
          for(const k of traj.coveredRegions){const r2=BRAIN_REGIONS[k];
            if(r2&&Math.abs(r2.ml)>=0.5&&Math.abs(traj.ml_mm)>=0.5&&Math.sign(traj.ml_mm)!==Math.sign(r2.ml)){bad=true;break;}}
          if(bad)continue;
        }
        traj.id=Date.now()+Math.random();
        boosted.push(traj);remaining=remaining.filter(k=>!traj.coveredRegions.includes(k));
        found=true;break outer;
      }
    }
  }
  return{selected:boosted,uncovered:remaining};
}

/* ═══════ MAIN DISPATCH ═══════ */
self.onmessage=async function(e){
  const{type,payload}=e.data;
  if(type!=='optimize')return;
  const{targetKeys,params,probeParams,constraintModes,BRAIN_REGIONS,ANATOMY}=payload;
  const onProgress=(pct,msg)=>self.postMessage({type:'progress',pct,msg:msg||''});

  // Build probe config from probeParams
  const probeConfig={
    shanks:probeParams.shanks||4,
    channelsPerShank:probeParams.channelsPerShank||96,
    electrodePitchUm:probeParams.electrodePitchUm||15,
    electrodeColumns:probeParams.electrodeColumns||2,
    shankPitchUm:probeParams.shankPitchUm||250,
    recordingLengthUm:probeParams.recordingLengthUm||720,
    depthUm:probeParams.depthUm||10000,
    entrySpacingUm:probeParams.entrySpacingUm||3500,
  };

  try{
    onProgress(5);
    const candidates=generateCandidates(targetKeys,{
      gridSpacing:params.gridSpacing,maxAngle:params.maxAngle,
      depthUm:probeConfig.depthUm,bilateral:params.bilateral,
      skullTiltDeg:params.skullTiltDeg||0,
    },constraintModes,BRAIN_REGIONS,ANATOMY,probeConfig,onProgress);
    onProgress(60);

    let res;
    if(params.algorithm==='milp'){
      onProgress(61,'milpBuilding');
      res=milpSolve(targetKeys,candidates,params.minClearance,probeConfig.entrySpacingUm,12000,onProgress);
    }else if(params.algorithm==='greedy'){
      res=greedyOptimize(targetKeys,candidates,params.minClearance,probeConfig.entrySpacingUm);
    }else{
      // Exact/backtrack
      res=milpSolve(targetKeys,candidates,params.minClearance,probeConfig.entrySpacingUm,8000,onProgress);
    }
    onProgress(85);

    if(params.coverageBoost&&res.uncovered&&res.uncovered.length>0){
      const boosted=coverageBoost(res.selected,res.uncovered,targetKeys,
        params.minClearance,probeConfig.entrySpacingUm,
        probeConfig.depthUm,constraintModes,params.skullTiltDeg||0,
        BRAIN_REGIONS,ANATOMY,probeConfig);
      boosted.milpStatus=res.milpStatus;boosted.milpNodes=res.milpNodes;
      res=boosted;
    }
    onProgress(98);
    self.postMessage({type:'result',result:res});
  }catch(err){
    self.postMessage({type:'error',message:err.message||String(err)});
  }
};
`;
}

let _workerUrl = null;
export function getWorkerUrl() {
  if (!_workerUrl) {
    const blob = new Blob([getWorkerSource()], { type: 'application/javascript' });
    _workerUrl = URL.createObjectURL(blob);
  }
  return _workerUrl;
}
