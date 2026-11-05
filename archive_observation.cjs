// Append actual observations to a private, data-only branch without expiry.
const fs = require('node:fs');
const BRANCH = 'analysis-data';

function archivePath(item) {
  if (!['live', 'fallback', 'unclassified'].includes(item.data_class)) throw new Error('Unknown data class');
  if (!/^\d+-\d+(?:-[a-f0-9]{12})?$/.test(item.observation_id)) throw new Error('Invalid origin run');
  const match = /^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})$/.exec(item.slot);
  if (!match) throw new Error('Invalid original slot');
  return `${item.data_class}/${match[1]}/${match[2]}${match[3]}-${item.observation_id}.json`;
}

async function appendObservation({github, repo, observation, core}) {
  const path = archivePath(observation);
  const body = JSON.stringify(observation, null, 2)+'\n';
  if (Buffer.byteLength(body) > 1000000) throw new Error('Observation exceeds archive file limit');
  for (let attempt = 0; attempt < 3; attempt++) {
    let ref;
    try { ref = (await github.rest.git.getRef({...repo, ref: 'heads/'+BRANCH})).data; }
    catch (error) { if (error.status !== 404) throw error; }
    try {
      if (!ref) {
        const schema = {schema_version: 'composition-observation-v1',
          folders: {live: 'Current-session collection passed the normal freshness requirements',
                    fallback: 'Older quotes or completed closes, explicitly labelled',
                    unclassified: 'Legacy records without reliable price-mode metadata'},
          decision_basis: 'Independent of price data class; fallback_unvalidated is an auxiliary decision, not older prices',
          run_kind: 'scheduled, manual or diagnostic; use delivery_key and target to deduplicate evaluations',
          retention: 'Git history on this data-only branch; no Actions artifact expiry',
          privacy: 'Same private repository; no credentials in observations'};
        const tree = await github.rest.git.createTree({...repo, tree: [
          {path, mode: '100644', type: 'blob', content: body},
          {path: 'schema.json', mode: '100644', type: 'blob', content: JSON.stringify(schema,null,2)+'\n'},
          ...['live','fallback','unclassified'].map(x => ({path:x+'/.gitkeep',mode:'100644',type:'blob',content:''}))]});
        const commit = await github.rest.git.createCommit({...repo,
          message:'Archive '+observation.observation_id+' ('+observation.data_class+')',
          tree:tree.data.sha, parents:[]});
        await github.rest.git.createRef({...repo, ref:'refs/heads/'+BRANCH, sha:commit.data.sha});
      } else {
        let existing;
        try { existing = (await github.rest.repos.getContent({...repo,path,ref:BRANCH})).data; }
        catch (error) { if (error.status !== 404) throw error; }
        if (existing) {
          const old = JSON.parse(Buffer.from(existing.content,'base64').toString('utf8'));
          if (old.payload_sha256 !== observation.payload_sha256 || old.delivery_key !== observation.delivery_key)
            throw new Error('Immutable observation conflict; refusing to overwrite '+path);
          core.info('Observation already archived: '+path);
          return path;
        }
        await github.rest.repos.createOrUpdateFileContents({...repo,path,branch:BRANCH,
          message:'Archive '+observation.observation_id+' ('+observation.data_class+')',
          content:Buffer.from(body).toString('base64')});
      }
      core.info('Observation archived: '+BRANCH+'/'+path);
      return path;
    } catch (error) {
      if (![409,422].includes(error.status) || attempt === 2) throw error;
      // Retry against the new head; never force-push or overwrite a prior file.
    }
  }
}

async function run({github, context, core}) {
  const file = 'direction_results/observation.json';
  if (!fs.existsSync(file)) { core.info('No completed analysis observation to archive.'); return; }
  const observation = JSON.parse(fs.readFileSync(file,'utf8'));
  return appendObservation({github,repo:context.repo,observation,core});
}

module.exports = {archivePath, appendObservation, run};
