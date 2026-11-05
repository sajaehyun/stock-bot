const assert=require('node:assert/strict');
const {archivePath,appendObservation}=require('./archive_observation.cjs');
const repo={owner:'sajaehyun',repo:'semiconductor-alert'},core={info(){}};
const item={data_class:'fallback',observation_id:'123-1',slot:'2026-11-05 09:00',payload_sha256:'abc',delivery_key:'slot'};
const notFound=()=>Object.assign(new Error('missing'),{status:404});
async function main(){
  assert.equal(archivePath(item),'fallback/2026-11-05/0900-123-1.json');
  assert.equal(archivePath({...item,data_class:'live'}),'live/2026-11-05/0900-123-1.json');
  assert.throws(()=>archivePath({...item,data_class:'../live'}));
  assert.throws(()=>archivePath({...item,observation_id:'../123'}));
  let tree,commit,ref;
  const first={rest:{git:{getRef:async()=>{throw notFound()},
    createTree:async args=>{tree=args;return {data:{sha:'tree'}}},
    createCommit:async args=>{commit=args;return {data:{sha:'commit'}}},
    createRef:async args=>{ref=args;return {data:{}}}}}};
  await appendObservation({github:first,repo,observation:item,core});
  assert.deepEqual(commit.parents,[]);
  assert.equal(ref.ref,'refs/heads/analysis-data');
  assert(tree.tree.some(x=>x.path.startsWith('fallback/2026-11-05/')));
  assert(!tree.tree.some(x=>x.path.startsWith('.github/')));
  let writes=0;
  const existing={rest:{git:{getRef:async()=>({data:{object:{sha:'old'}}})},repos:{
    getContent:async()=>({data:{content:Buffer.from(JSON.stringify(item)).toString('base64')}}),
    createOrUpdateFileContents:async()=>{writes++;return {data:{}}}}}};
  await appendObservation({github:existing,repo,observation:item,core});
  assert.equal(writes,0);
  await assert.rejects(()=>appendObservation({github:existing,repo,observation:{...item,payload_sha256:'changed'},core}),/refusing to overwrite/);
  assert.equal(writes,0);
  let attempts=0;
  existing.rest.repos.getContent=async()=>{throw notFound()};
  existing.rest.repos.createOrUpdateFileContents=async args=>{
    assert.equal(args.branch,'analysis-data');
    assert(!('sha' in args));
    if(attempts++===0)throw Object.assign(new Error('race'),{status:409});
    return {data:{}};
  };
  await appendObservation({github:existing,repo,observation:item,core});
  assert.equal(attempts,2);
  console.log('Observation archive tests passed: source separation, data-only branch, immutable duplicate/conflict, concurrent retry.');
}
main().catch(error=>{console.error(error);process.exitCode=1});
