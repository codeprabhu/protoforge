const content = document.querySelector('#content');
const notice = document.querySelector('#notice');
const pages = {overview: 'Overview', models: 'Model Registry', providers: 'Providers', credentials: 'Credentials', experiments: 'Experiments', history: 'History', settings: 'Settings'};
let page = 'overview';
let state = {providers: [], models: [], credentials: [], experiments: [], defaultModel: null};

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
function highlightRTL(code) {
  return escapeHtml(code).replace(/(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|\b(module|endmodule|input|output|inout|wire|reg|always|begin|end|if|else|case|endcase|posedge|negedge|assign|parameter|localparam|integer|for|function|task|default)\b|\b(?:\d+'[bBoOdDhH][0-9a-fA-F_xXzZ?]+|\d+)\b/g,
    (match, comment, keyword) => comment ? `<span class="vcomment">${comment}</span>` : keyword ? `<span class="vkw">${keyword}</span>` : `<span class="vnum">${match}</span>`);
}
async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {...options, headers: {'Content-Type': 'application/json', ...(options.headers || {})}});
  let result = {};
  try { result = await response.json(); } catch {}
  if (!response.ok) throw new Error(result.detail || result.message || `Request failed (${response.status})`);
  return result;
}
function flash(message, error = false) {
  notice.textContent = message;
  notice.className = message ? `notice${error ? ' error' : ''}` : '';
  if (message) setTimeout(() => { notice.textContent = ''; notice.className = ''; }, 5000);
}
function empty(title, detail) { return `<div class="panel empty"><strong>${escapeHtml(title)}</strong>${escapeHtml(detail)}</div>`; }
async function load() {
  document.querySelector('#title').textContent = pages[page];
  document.querySelector('#crumb').textContent = pages[page].toUpperCase();
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.page === page));
  try {
    const [providers, models, credentials, experiments] = await Promise.all([api('/providers'), api('/models'), api('/credentials'), api('/experiments')]);
    state = {providers, models: models.models, credentials, experiments, defaultModel: models.default_model};
    await render();
  } catch (error) { flash(error.message, true); }
}
async function render() {
  if (page === 'overview') return overview();
  if (page === 'providers') return providersView();
  if (page === 'models') return modelsView();
  if (page === 'credentials') return credentialsView();
  if (page === 'history') return historyView();
  if (page === 'experiments') return experimentsView();
  content.innerHTML = empty('Settings', 'Settings will be available in a later stage.');
}
async function overview() {
  const data = await api('/dashboard');
  const defaultName = state.models.find(item => item.model_id === state.defaultModel)?.display_name || 'Not selected';
  content.innerHTML = `<div class="metrics">
    <div class="metric"><span>Configured providers</span><strong>${data.provider_count}</strong></div>
    <div class="metric"><span>Configured models</span><strong>${data.model_count}</strong></div>
    <div class="metric"><span>Available models</span><strong>${data.available_model_count}</strong></div>
    <div class="metric"><span>Default model</span><strong style="font-size:15px">${escapeHtml(defaultName)}</strong></div>
  </div><div class="section-head"><h2>Provider connections</h2><button class="button small" data-nav="providers">Manage providers</button></div>
  ${data.providers.length ? `<div class="panel"><table class="table"><thead><tr><th>Provider</th><th>Endpoint</th><th>Status</th><th>Models</th></tr></thead><tbody>${data.providers.map(p => `<tr><td>${escapeHtml(p.display_name)} <span class="muted code">${escapeHtml(p.provider_id)}</span></td><td>${escapeHtml(p.endpoint_url || 'Default')}</td><td><span class="status ${escapeHtml(p.status)}">${escapeHtml(p.status)}</span></td><td>${p.available_models.length}</td></tr>`).join('')}</tbody></table></div>` : empty('No providers configured', 'Add Ollama or configure a provider endpoint to get started.')}
  <div class="section-head"><h2>Recent validation activity</h2></div>
  ${data.recent_validation.length ? `<div class="panel">${data.recent_validation.map(p => `<p>${escapeHtml(p.provider)} / ${escapeHtml(p.account)} <span class="status ${escapeHtml(p.validation_status)}">${escapeHtml(p.validation_status)}</span></p>`).join('')}</div>` : empty('No validation activity', 'Credential validation results will appear here.')}`;
}
function providersView() {
  content.innerHTML = `<div class="section-head"><h2>Providers</h2><button class="button primary" data-action="add-provider">+ Add provider</button></div>
  ${state.providers.length ? `<div class="panel"><table class="table"><thead><tr><th>Provider</th><th>Endpoint</th><th>Credential profile</th><th>Status</th><th>Actions</th></tr></thead><tbody>${state.providers.map(p => `<tr><td><strong>${escapeHtml(p.display_name)}</strong><div class="muted code">${escapeHtml(p.provider_id)}</div></td><td>${escapeHtml(p.endpoint_url || 'Default')}</td><td>${escapeHtml(p.credential_account || 'None')}</td><td><span class="status ${escapeHtml(p.status)}">${escapeHtml(p.status)}</span></td><td><div class="actions"><button class="button small" data-action="test-provider" data-id="${escapeHtml(p.provider_id)}">Test</button><button class="button small" data-action="edit-provider" data-id="${escapeHtml(p.provider_id)}">Edit</button><button class="button small danger" data-action="delete-provider" data-id="${escapeHtml(p.provider_id)}">Remove</button></div></td></tr>`).join('')}</tbody></table></div>` : empty('No providers yet', 'Register a provider to begin setting up models.')}`;
}
function modelsView() {
  content.innerHTML = `<div class="section-head"><h2>Configured models</h2><button class="button primary" data-action="add-model">+ Add model</button></div><div class="toolbar"><input class="input" id="model-search" placeholder="Search models or providers…"><span class="muted">${state.models.length} models</span></div><div id="model-list"></div>`;
  document.querySelector('#model-search').addEventListener('input', event => drawModels(event.target.value));
  drawModels('');
}
function drawModels(query) {
  const models = state.models.filter(m => `${m.display_name} ${m.model_identifier} ${m.provider_id}`.toLowerCase().includes(query.toLowerCase()));
  document.querySelector('#model-list').innerHTML = models.length ? `<div class="panel"><table class="table"><thead><tr><th>Model</th><th>Provider</th><th>Availability</th><th>Context</th><th>Temperature</th><th>Enabled</th><th>Actions</th></tr></thead><tbody>${models.map(m => `<tr><td><strong>${escapeHtml(m.display_name)}</strong><div class="muted code">${escapeHtml(m.model_identifier)}</div></td><td>${escapeHtml(m.provider_id)} <span class="status">${m.local ? 'local' : 'remote'}</span></td><td><span class="status ${escapeHtml(m.availability)}">${escapeHtml(m.availability)}</span></td><td>${m.context_window ?? 'Unknown'}</td><td>${m.default_temperature}</td><td>${m.enabled ? 'Yes' : 'No'}</td><td><div class="actions"><button class="button small" data-action="default-model" data-id="${m.model_id}">${state.defaultModel === m.model_id ? 'Default' : 'Set default'}</button><button class="button small" data-action="edit-model" data-id="${m.model_id}">Edit</button><button class="button small danger" data-action="delete-model" data-id="${m.model_id}">Remove</button></div></td></tr>`).join('')}</tbody></table></div>` : empty(query ? 'No matching models' : 'No models configured', 'Add a provider, then register one or more models.');
}
function credentialsView() {
  content.innerHTML = `<div class="section-head"><h2>Credential profiles</h2><button class="button primary" data-action="add-credential">+ Add credential</button></div><div class="notice">Secrets are saved by the server to your operating system keyring. This dashboard never displays stored keys.</div>
  ${state.credentials.length ? `<div class="panel"><table class="table"><thead><tr><th>Provider</th><th>Account</th><th>Label</th><th>Validation</th><th>Actions</th></tr></thead><tbody>${state.credentials.map(p => `<tr><td>${escapeHtml(p.provider)}</td><td>${escapeHtml(p.account)}</td><td>${escapeHtml(p.display_label || '—')}</td><td><span class="status ${escapeHtml(p.validation_status || 'unknown')}">${escapeHtml(p.validation_status || 'Not tested')}</span></td><td><div class="actions"><button class="button small" data-action="validate-credential" data-provider="${escapeHtml(p.provider)}" data-account="${escapeHtml(p.account)}">Validate</button><button class="button small" data-action="update-credential" data-provider="${escapeHtml(p.provider)}" data-account="${escapeHtml(p.account)}">Update</button><button class="button small danger" data-action="delete-credential" data-provider="${escapeHtml(p.provider)}" data-account="${escapeHtml(p.account)}">Delete</button></div></td></tr>`).join('')}</tbody></table></div>` : empty('No credential profiles', 'Credentials are stored only in the operating system keyring.')}`;
}
async function historyView() {
  const runs = await api('/runs');
  content.innerHTML = `<div class="section-head"><h2>Existing ProtoForge runs</h2><span class="muted">${runs.length} traces</span></div>
  ${runs.length ? `<div class="panel"><table class="table"><thead><tr><th>Run</th><th>Domain</th><th>Status</th><th>Events</th><th>Started</th></tr></thead><tbody>${runs.map(r => `<tr class="run-row" data-action="show-run" data-id="${escapeHtml(r.run_id)}"><td class="code">${escapeHtml(r.run_id)}</td><td>${escapeHtml(r.domain)}</td><td>${escapeHtml(r.status)}</td><td>${r.events_count}</td><td>${r.start_time ? new Date(r.start_time * 1000).toLocaleString() : '—'}</td></tr>`).join('')}</tbody></table></div><div id="run-detail"></div>` : empty('No run traces found', 'Completed ProtoForge traces will appear here.')}`;
}
function experimentsView() {
  content.innerHTML = `<div class="section-head"><h2>New I²C experiment</h2><span class="muted">Models receive the same specification and frozen retrieval corpus.</span></div>
  ${state.models.length ? `<form id="experiment-form" class="panel"><div class="field"><label>Experiment name</label><input class="input" name="name" required maxlength="160" value="I2C model comparison"></div>
  <div class="field"><label>Specification</label><select class="select" name="specification_id"><option value="i2c-default">I²C v1 default specification</option></select></div>
  <div class="field"><label>Models</label><div class="model-select">${state.models.map(m => `<label class="model-option"><input type="checkbox" name="model_ids" value="${m.model_id}" ${m.enabled && m.availability === 'available' ? 'checked' : ''} ${m.enabled ? '' : 'disabled'}><span><b>${escapeHtml(m.display_name)}</b><small>${escapeHtml(m.provider_id)} · ${escapeHtml(m.model_identifier)} · ${m.local ? 'local' : 'remote'} · ${escapeHtml(m.availability)}</small></span></label>`).join('')}</div></div>
  <div class="field"><label>Repair model (optional)</label><select class="select" name="repair_model_id"><option value="">Same model that generates each candidate</option>${state.models.filter(m=>m.enabled).map(m=>`<option value="${m.model_id}">${escapeHtml(m.provider_id)} · ${escapeHtml(m.model_identifier)}</option>`).join('')}</select></div>
  <div class="field"><label>Failure analyst (optional)</label><select class="select" name="analyst_model_id"><option value="">Use the repair model, or generator when repair is unchanged</option>${state.models.filter(m=>m.enabled).map(m=>`<option value="${m.model_id}">${escapeHtml(m.provider_id)} · ${escapeHtml(m.model_identifier)}</option>`).join('')}</select></div>
  <div class="field-row">${field('Samples per model', 'samples_per_model', '1', 'number')}${field('Temperature', 'temperature', '0', 'number')}</div>
  <div class="field-row">${field('Random seed', 'seed', '42', 'number')}${field('Maximum repairs', 'max_repairs', '2', 'number')}</div>
  <div class="field-row">${field('Generation timeout (seconds)', 'generation_timeout', '180', 'number')}<div class="field"><label>Execution mode</label><select class="select" name="execution_mode"><option value="sequential">Sequential (recommended for local models)</option><option value="concurrent">Concurrent</option></select></div></div>
  <div class="field-row">${field('Global concurrency', 'global_concurrency', '1', 'number')}${field('Local model concurrency', 'local_concurrency', '1', 'number')}</div>
  <div class="field-row">${field('Per-provider limit', 'provider_limit', '1', 'number')}${field('Per-model limit', 'model_limit', '1', 'number')}</div>
  <label class="checkline"><input type="checkbox" name="retrieval" checked> Use verified RTL retrieval</label>
  <div class="actions" style="margin-top:18px"><button class="button primary" type="submit">Create and start</button></div></form>` : empty('No enabled models', 'Add providers and models in Model Registry before creating an experiment.')}
  <div class="section-head"><h2>Experiments</h2><span class="muted">Progress is saved and reloadable</span></div><div id="experiment-list"></div>`;
  document.querySelector('#experiment-form')?.addEventListener('submit', createExperiment);
  drawExperiments();
}
function drawExperiments() {
  const target = document.querySelector('#experiment-list');
  if (!target) return;
  target.innerHTML = state.experiments.length ? state.experiments.map(experiment => `<div class="panel experiment-panel"><div class="section-head"><div><strong>${escapeHtml(experiment.name)}</strong><div class="muted">${escapeHtml(experiment.domain)} · ${experiment.candidates.length} candidates · ${new Date(experiment.created_at*1000).toLocaleString()}</div></div><div class="actions"><span class="status ${escapeHtml(experiment.status)}">${escapeHtml(experiment.status)}</span>${experiment.status === 'created' ? `<button class="button small" data-action="start-experiment" data-id="${experiment.experiment_id}">Start</button>` : ''}${['queued','running'].includes(experiment.status) ? `<button class="button small danger" data-action="cancel-experiment" data-id="${experiment.experiment_id}">Cancel</button>` : ''}<a class="button small" href="/api/experiments/${experiment.experiment_id}/report?format=json">JSON report</a><a class="button small" href="/api/experiments/${experiment.experiment_id}/report?format=md">Markdown</a></div></div>
  <div class="progress-track"><span style="width:${progressPercent(experiment)}%"></span></div><div class="muted progress-label">${experiment.candidates.filter(c=>['completed','failed','cancelled'].includes(c.status)).length} / ${experiment.candidates.length} finished</div>
  <div class="ranking-note">Ranking: protocol success, first-pass success, structural/compile gates, fewer repairs, lower latency, then token count. Ranking is informational.</div>
  ${experiment.candidates.length > 1 ? `<div class="compare-controls"><label>Compare <select class="select" id="left-${experiment.experiment_id}">${experiment.candidates.map(c=>`<option value="${c.candidate_id}">${escapeHtml(c.model_identifier)} (${c.candidate_id.slice(0,8)})</option>`).join('')}</select></label><span>with</span><label><select class="select" id="right-${experiment.experiment_id}">${experiment.candidates.map((c,i)=>`<option value="${c.candidate_id}" ${i===1?'selected':''}>${escapeHtml(c.model_identifier)} (${c.candidate_id.slice(0,8)})</option>`).join('')}</select></label><button class="button small" data-action="compare-candidates" data-experiment="${experiment.experiment_id}">Show RTL diff</button></div><pre class="candidate-diff" id="diff-${experiment.experiment_id}" hidden></pre>` : ''}
  <div class="candidate-grid">${experiment.candidates.map(candidate => { const v=candidate.verification||{}; const show=x=>x===true?'pass':x===false?'fail':'unavailable'; return `<article class="candidate-card"><div class="eyebrow">RANK ${candidate.rank ?? '—'} · ${escapeHtml(candidate.provider_type)}</div><h3>${escapeHtml(candidate.model_identifier)}</h3><div class="candidate-status"><span class="status ${escapeHtml(candidate.status)}">${escapeHtml(candidate.status)}</span><span class="muted">${escapeHtml(candidate.current_stage)}</span></div><div class="candidate-metric">Final protocol judge: ${show(candidate.functional_success)}</div><div class="candidate-metrics"><span>First pass: ${show(candidate.first_pass_success)}</span><span>Structural: ${show(v.structural)}</span><span>Compilation: ${show(v.compile)}</span><span>Hardware: ${show(v.hardware)}</span><span>Simulation: ${show(v.simulation)}</span><span>Protocol judge: ${show(v.judge)}</span></div><div class="muted">${candidate.generation_latency_sec == null ? 'Generation time unavailable' : `${candidate.generation_latency_sec.toFixed(2)}s generation`} · ${candidate.total_latency_sec == null ? 'total time unavailable' : `${candidate.total_latency_sec.toFixed(2)}s total`} · ${candidate.repair_count} repairs · ${candidate.rtl_size_bytes == null ? 'RTL size unavailable' : `${candidate.rtl_size_bytes} RTL bytes`}</div><div class="muted">${candidate.token_usage ? `${candidate.token_usage.prompt ?? '—'} prompt / ${candidate.token_usage.completion ?? '—'} completion tokens` : 'Token usage unavailable'} · Cost unavailable (no pricing configured)</div>${candidate.error ? `<p class="error-text">${escapeHtml(candidate.error)}</p>` : ''}<div class="actions"><button class="button small" data-action="inspect-candidate" data-experiment="${experiment.experiment_id}" data-id="${candidate.candidate_id}">Inspect RTL</button>${candidate.candidate_rtl ? `<a class="button small" href="/api/experiments/${experiment.experiment_id}/candidates/${candidate.candidate_id}/rtl">Download RTL</a>` : ''}${['queued','running'].includes(candidate.status) ? `<button class="button small danger" data-action="cancel-candidate" data-experiment="${experiment.experiment_id}" data-id="${candidate.candidate_id}">Cancel candidate</button>` : ''}</div><div class="candidate-detail" id="detail-${candidate.candidate_id}"></div></article>`; }).join('')}</div></div>`).join('') : empty('No experiments yet', 'Configure models above and launch a controlled experiment.');
}
function progressPercent(experiment) { return experiment.candidates.length ? Math.round(experiment.candidates.filter(c => ['completed','failed','cancelled'].includes(c.status)).length * 100 / experiment.candidates.length) : 0; }
async function createExperiment(event) {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    const data = new FormData(form);
    const modelIds = data.getAll('model_ids');
    const payload = {name:data.get('name'), specification_id:data.get('specification_id'), domain:'i2c', model_ids:modelIds,
      samples_per_model:Number(data.get('samples_per_model')), temperature:Number(data.get('temperature')), seed:Number(data.get('seed')),
      max_repairs:Number(data.get('max_repairs')), generation_timeout:Number(data.get('generation_timeout')),
      repair_model_id:data.get('repair_model_id') || null,
      analyst_model_id:data.get('analyst_model_id') || null,
      execution_mode:data.get('execution_mode'), global_concurrency:Number(data.get('global_concurrency')),
      local_concurrency:Number(data.get('local_concurrency')), retrieval:form.elements.retrieval.checked,
      provider_concurrency:Object.fromEntries(state.providers.map(p=>[p.provider_id,Number(data.get('provider_limit'))])),
      model_concurrency:Object.fromEntries(state.models.map(m=>[m.model_id,Number(data.get('model_limit'))]))};
    const experiment = await api('/experiments', {method:'POST', body:JSON.stringify(payload)});
    await api(`/experiments/${experiment.experiment_id}/start`, {method:'POST'});
    flash('Experiment started. Candidate progress is saved as it runs.');
    await load();
  } catch (error) { flash(error.message, true); }
}
async function inspectCandidate(button) {
  const detail = document.querySelector(`#detail-${CSS.escape(button.dataset.id)}`);
  if (detail.dataset.loaded === 'true') { detail.hidden = !detail.hidden; return; }
  const data = await api(`/experiments/${button.dataset.experiment}/candidates/${button.dataset.id}`);
  detail.dataset.loaded = 'true';
  detail.innerHTML = data.versions.map(version => `<details ${version.attempt === 0 ? 'open' : ''}><summary>${version.repair ? 'Repair' : 'Generation'} revision ${version.attempt} · ${escapeHtml(version.model || 'model unavailable')} · ${escapeHtml(version.code_sha256 || 'hash unavailable')}</summary><p class="eyebrow">Structured explanation (model generated)</p><pre>${escapeHtml(JSON.stringify(version.explanation || {status:'not_reported'},null,2))}</pre>${version.failure_analysis ? `<p class="eyebrow">Failure analyst hypothesis (not verified)</p><pre>${escapeHtml(JSON.stringify(version.failure_analysis,null,2))}</pre>` : ''}<p class="eyebrow">Prompt</p><pre>${escapeHtml(version.prompt || 'Unavailable')}</pre><p class="eyebrow">Retrieved context</p><pre>${escapeHtml(JSON.stringify(version.retrieval || {},null,2))}</pre><p class="eyebrow">Verification evidence and logs</p><pre>${escapeHtml(JSON.stringify(version.stages || [],null,2))}</pre><pre class="rtl-viewer">${highlightRTL(version.rtl)}</pre><a class="button small" href="/api/experiments/${button.dataset.experiment}/candidates/${button.dataset.id}/rtl?attempt=${version.attempt}">Download revision</a></details>`).join('') || '<p class="muted">Candidate output is not available yet.</p>';
}
async function compareCandidates(button) {
  const experiment = button.dataset.experiment;
  const left = document.querySelector(`#left-${CSS.escape(experiment)}`).value;
  const right = document.querySelector(`#right-${CSS.escape(experiment)}`).value;
  if (left === right) return flash('Choose two different candidates to compare.', true);
  const result = await api(`/experiments/${experiment}/diff?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
  const target = document.querySelector(`#diff-${CSS.escape(experiment)}`);
  target.textContent = result.diff || 'The RTL files are identical.';
  target.hidden = false;
}
function field(label, name, value = '', type = 'text', hint = '') {
  return `<div class="field"><label>${escapeHtml(label)}</label><input class="input" name="${name}" type="${type}" value="${escapeHtml(value)}">${hint ? `<small>${escapeHtml(hint)}</small>` : ''}</div>`;
}
function openModal(title, body, save) {
  const dialog = document.querySelector('#modal');
  document.querySelector('#modal-title').textContent = title;
  document.querySelector('#modal-content').innerHTML = body;
  dialog.showModal();
  document.querySelector('#modal-form').onsubmit = async event => {
    event.preventDefault();
    try { await save(new FormData(event.currentTarget)); dialog.close(); await load(); }
    catch (error) { flash(error.message, true); }
  };
}
function providerModal(id) {
  const provider = state.providers.find(item => item.provider_id === id);
  const body = `${provider ? '' : field('Provider configuration ID', 'provider_id')}${field('Provider type', 'provider_type', provider?.provider_type || '')}${field('Display name', 'display_name', provider?.display_name || '')}${field('Endpoint URL (optional)', 'endpoint_url', provider?.endpoint_url || '', 'url')}${field('Credential account/profile (optional)', 'credential_account', provider?.credential_account || '')}`;
  openModal(provider ? 'Edit provider' : 'Add provider', body, async form => {
    const payload = Object.fromEntries(form);
    await api(`/providers${id ? `/${id}` : ''}`, {method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload)});
  });
}
function modelModal(id) {
  if (!state.providers.length) return flash('Add a provider before registering models.', true);
  const model = state.models.find(item => item.model_id === id);
  const selector = model ? '' : `<div class="field"><label>Provider</label><select class="select" name="provider_id">${state.providers.map(p => `<option value="${escapeHtml(p.provider_id)}">${escapeHtml(p.display_name)}</option>`).join('')}</select></div>`;
  const body = `${selector}${field('Display name', 'display_name', model?.display_name || '')}${field('Model identifier', 'model_identifier', model?.model_identifier || '')}
    <div class="field-row">${field('Context window (optional)', 'context_window', model?.context_window || '', 'number')}${field('Default temperature', 'default_temperature', model?.default_temperature ?? 0, 'number')}</div>
    ${field('Generation timeout (seconds)', 'generation_timeout', model?.generation_timeout ?? 120, 'number')}
    ${field('Capabilities (comma separated, optional)', 'capabilities', model?.capabilities?.join(', ') || '')}
    <div class="field"><label><input type="checkbox" name="local" ${model?.local ?? state.providers[0].provider_id === 'ollama' ? 'checked' : ''}> Local model</label></div>
    <div class="field"><label><input type="checkbox" name="enabled" ${model?.enabled ?? true ? 'checked' : ''}> Enabled</label></div>`;
  openModal(model ? 'Edit model' : 'Add model', body, async form => {
    const payload = Object.fromEntries(form);
    for (const key of ['context_window', 'default_temperature', 'generation_timeout']) {
      if (payload[key] === '') delete payload[key]; else payload[key] = Number(payload[key]);
    }
    payload.local = form.has('local');
    payload.enabled = form.has('enabled');
    if (payload.capabilities !== undefined) payload.capabilities = payload.capabilities.trim() ? payload.capabilities.split(',').map(value => value.trim()) : null;
    await api(model ? `/models/${id}` : '/models', {method: model ? 'PATCH' : 'POST', body: JSON.stringify(payload)});
  });
}
function credentialModal(provider = '', account = '') {
  openModal(account ? 'Update credential' : 'Add credential', `${field('Provider identifier', 'provider', provider)}${field('Account/profile name', 'account', account)}${field('Display label (optional)', 'display_label')}${field('Credential', 'secret', '', 'password', 'The secret is sent directly to the backend and is never returned.')}`, async form => {
    await api('/credentials', {method: 'POST', body: JSON.stringify(Object.fromEntries(form))});
  });
}
async function perform(action, element) {
  const id = element.dataset.id;
  if (action === 'add-provider') return providerModal();
  if (action === 'edit-provider') return providerModal(id);
  if (action === 'add-model') return modelModal();
  if (action === 'edit-model') return modelModal(id);
  if (action === 'add-credential') return credentialModal();
  if (action === 'update-credential') return credentialModal(element.dataset.provider, element.dataset.account);
  if (action === 'inspect-candidate') return inspectCandidate(element);
  if (action === 'compare-candidates') return compareCandidates(element);
  if (action === 'start-experiment') { await api(`/experiments/${id}/start`, {method:'POST'}); return load(); }
  if (action === 'cancel-experiment') { await api(`/experiments/${id}/cancel`, {method:'POST'}); return load(); }
  if (action === 'cancel-candidate') { await api(`/experiments/${element.dataset.experiment}/candidates/${id}/cancel`, {method:'POST'}); return load(); }
  if (action === 'test-provider') {
    const result = await api(`/providers/${id}/test`, {method: 'POST'});
    flash(result.message, result.status !== 'available');
    return load();
  }
  if (action === 'validate-credential') {
    const result = await api(`/credentials/${encodeURIComponent(element.dataset.provider)}/${encodeURIComponent(element.dataset.account)}/validate`, {method: 'POST'});
    flash(result.message, result.status !== 'valid');
    return load();
  }
  if (action === 'delete-provider' && confirm(`Remove provider ${id}?`)) await api(`/providers/${id}`, {method: 'DELETE'});
  if (action === 'delete-model' && confirm('Remove this model configuration?')) await api(`/models/${id}`, {method: 'DELETE'});
  if (action === 'delete-credential' && confirm('Delete this keyring credential profile?')) await api(`/credentials/${encodeURIComponent(element.dataset.provider)}/${encodeURIComponent(element.dataset.account)}`, {method: 'DELETE'});
  if (action === 'default-model') await api(`/models/${id}/default`, {method: 'POST'});
  if (action === 'show-run') {
    const run = await api(`/runs/${encodeURIComponent(id)}`);
    document.querySelector('#run-detail').innerHTML = `<div class="panel"><h2>${escapeHtml(run.run_id)} — ${escapeHtml(run.status)}</h2><p class="muted">${escapeHtml(run.domain)} · ${run.verification.total_verifications} verification gates</p><pre>${escapeHtml(JSON.stringify(run.verification.gates, null, 2))}</pre></div>`;
    return;
  }
  await load();
}
document.querySelector('#navigation').addEventListener('click', event => {
  const button = event.target.closest('[data-page]');
  if (button) { page = button.dataset.page; load(); }
});
document.querySelector('#content').addEventListener('click', event => {
  const button = event.target.closest('[data-nav]');
  if (button) { page = button.dataset.nav; load(); }
});
document.querySelector('#content').addEventListener('click', event => {
  const button = event.target.closest('[data-action]');
  if (button) perform(button.dataset.action, button).catch(error => flash(error.message, true));
});
document.querySelector('#quick-add').addEventListener('click', () => { page = 'models'; load(); });
load();
setInterval(async () => {
  if (page !== 'experiments') return;
  try {
    state.experiments = await api('/experiments');
    drawExperiments();
  } catch (error) { flash(error.message, true); }
}, 1500);
