const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {receiptPath,candidatePaths,prepare,persist}=require('./notion_sync.cjs');
assert.equal(receiptPath('123-1'),'notion-receipts/123-1.json');
assert.throws(()=>receiptPath('../token'));
assert.deepEqual(candidatePaths([{type:'blob',path:'no_analysis/2026-11-05/1300-123-1.json'}]),
 ['no_analysis/2026-11-05/1300-123-1.json']);
assert.equal(receiptPath('123-1-0123456789ab'),'notion-receipts/123-1-0123456789ab.json');
assert.deepEqual(candidatePaths([{type:'blob',path:'unclassified/2026-11-03/1100-123-1-0123456789ab.json'}]),
 ['unclassified/2026-11-03/1100-123-1-0123456789ab.json']);
const tree=['live/2026-11-05/0900-123-1.json','fallback/2026-11-05/1300-456-1.json',
            'unclassified/2026-11-04/1900-100-2.json','schema.json','live/.gitkeep',
            'notion-receipts/123-1.json','.github/workflows/leak.yml'].map(path=>({path,type:'blob'}));
assert.deepEqual(candidatePaths(tree),['fallback/2026-11-05/1300-456-1.json',
 'live/2026-11-05/0900-123-1.json','unclassified/2026-11-04/1900-100-2.json']);
async function integrationChecks() {
  const original=process.cwd(), temp=fs.mkdtempSync(path.join(os.tmpdir(),'notion-queue-'));
  process.chdir(temp);
  try {
    const files={
      'live/2026-11-05/0900-123-1.json':{delivery_status:'sent',observation_id:'123-1'},
      'fallback/2026-11-05/1300-456-1.json':{delivery_status:'sent',observation_id:'456-1'},
      'unclassified/2026-11-04/1900-100-2.json':{delivery_status:'failed',observation_id:'100-2'},
    };
    const fetched=[],created=[];
    const github={rest:{git:{getRef:async()=>({data:{object:{sha:'head'}}}),
      getTree:async()=>({data:{tree:[...Object.keys(files),'notion-receipts/123-1.json'].map(path=>({path,type:'blob'}))}})},
      repos:{getContent:async(args)=>{
        fetched.push(args.path);
        if(!(args.path in files)){const e=new Error('missing');e.status=404;throw e;}
        return {data:{content:Buffer.from(JSON.stringify(files[args.path])).toString('base64')}};
      },createOrUpdateFileContents:async(args)=>{created.push(args);}}}};
    const params={github,context:{repo:{owner:'owner',repo:'repo'}},core:{info:()=>{}}};
    await prepare(params);
    const queue=JSON.parse(fs.readFileSync('notion_queue.json'));
    assert.equal(queue.length,1);
    assert.equal(queue[0].observation.observation_id,'456-1');
    assert.ok(!fetched.includes('live/2026-11-05/0900-123-1.json'),'already synced raw data must not be downloaded again');
    assert.match(queue[0].archive_url,/blob\/analysis-data\/fallback/);
    fs.writeFileSync('notion_receipts.json',JSON.stringify([{observation_id:'456-1',delivery_key:'key',
      page_id:'page',payload_sha256:'original'}]));
    await persist(params);
    assert.equal(created.length,1);
    assert.equal(created[0].path,'notion-receipts/456-1.json');
    files[created[0].path]={page_id:'different-page',payload_sha256:'original'};
    await assert.rejects(persist(params),/receipt conflict/);
    assert.equal(created.length,1,'conflicting receipt must not be overwritten');
  } finally {
    process.chdir(original);
    // Only the exact temporary directory created above is removed.
    assert.equal(path.dirname(path.resolve(temp)),path.resolve(os.tmpdir()));
    fs.rmSync(temp,{recursive:true,force:true});
  }
}
integrationChecks().then(()=>console.log('Notion queue skips receipts/failed sends, preserves originals, and rejects conflicting receipts'))
 .catch(e=>{console.error(e);process.exitCode=1;});
