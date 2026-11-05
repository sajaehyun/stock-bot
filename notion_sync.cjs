// Queue immutable delivered originals and preserve durable Notion receipts.
const fs = require('node:fs');
const BRANCH = 'analysis-data';
function receiptPath(observationId) {
  if(!/^\d+-\d+(?:-[a-f0-9]{12})?$/.test(observationId))throw new Error('Invalid original observation ID');
  return 'notion-receipts/'+observationId+'.json';
}
function candidatePaths(tree) {
  return tree.filter(x=>x.type==='blob' && /^(live|fallback|unclassified|no_analysis)\/\d{4}-\d{2}-\d{2}\/\d{4}-\d+-\d+(?:-[a-f0-9]{12})?\.json$/.test(x.path))
    .map(x=>x.path).sort();
}
async function prepare({github,context,core,includeArchivedMetadata=false}) {
  let ref;
  try { ref=(await github.rest.git.getRef({...context.repo,ref:'heads/'+BRANCH})).data; }
  catch(error) { if(error.status!==404)throw error; fs.writeFileSync('notion_queue.json','[]');return; }
  const tree=(await github.rest.git.getTree({...context.repo,tree_sha:ref.object.sha,recursive:'1'})).data;
  if(tree.truncated)throw new Error('Observation tree truncated; do not silently omit old pending deliveries');
  const paths=new Set(tree.tree.map(x=>x.path));
  const queue=[];
  for(const path of candidatePaths(tree.tree)) {
    const observationId=path.split('/').pop().replace(/^\d{4}-/,'').replace(/\.json$/,'');
    const alreadySaved=paths.has(receiptPath(observationId));
    if(alreadySaved && !includeArchivedMetadata)continue;
    const file=(await github.rest.repos.getContent({...context.repo,path,ref:ref.object.sha})).data;
    const observation=JSON.parse(Buffer.from(file.content,'base64').toString('utf8'));
    if(alreadySaved && observation.historical_import)continue;
    if(observation.delivery_status!=='sent')continue;
    queue.push({observation,archive_url:`https://github.com/${context.repo.owner}/${context.repo.repo}/blob/${BRANCH}/${path}`});
    if(queue.length>=25)break; // Remaining originals stay queued for the next cloud run.
  }
  fs.writeFileSync('notion_queue.json',JSON.stringify(queue));
  core.info('Pending confirmed Telegram deliveries: '+queue.length);
}
async function persist({github,context,core}) {
  if(!fs.existsSync('notion_receipts.json'))return;
  const receipts=JSON.parse(fs.readFileSync('notion_receipts.json','utf8'));
  for(const receipt of receipts) {
    const path=receiptPath(receipt.observation_id);
    let old;
    try { old=(await github.rest.repos.getContent({...context.repo,path,ref:BRANCH})).data; }
    catch(error) { if(error.status!==404)throw error; }
    if(old) {
      const saved=JSON.parse(Buffer.from(old.content,'base64').toString('utf8'));
      if(saved.page_id!==receipt.page_id || saved.payload_sha256!==receipt.payload_sha256)
        throw new Error('Notion receipt conflict; refusing to replace a prior record');
      continue;
    }
    await github.rest.repos.createOrUpdateFileContents({...context.repo,path,branch:BRANCH,
      message:'Record Notion mirror '+receipt.observation_id,
      content:Buffer.from(JSON.stringify(receipt,null,2)+'\n').toString('base64')});
    core.info('Notion receipt preserved: '+receipt.observation_id);
  }
}
module.exports={receiptPath,candidatePaths,prepare,persist};
