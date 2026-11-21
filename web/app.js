'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const state = { bootstrap: null, plan: null, cases: [], activeCase: null, selectedOption: null, tab: 'decisions', busy: false, planRequest: 0, caseRequest: 0, logRequest: 0, caseCache: new Map(), updateDrafts: new Map(), rationaleDrafts: new Map() };
  const labels = { ready: 'Ready for review', conditional: 'Needs confirmation', blocked: 'Blocked', pass: 'Recorded', pending: 'Pending', fail: 'Failed', approved: 'Approved', open: 'Open', selected: 'Provisional choice', provisional: 'Provisional choice' };
  const defaultCaseTypes = [
    {id:'car_unavailable',label:'Car unavailable',description:'A defect or repair extension prevents an assigned car from running.',requires_car:true},
    {id:'maintenance_change',label:'Maintenance change',description:'A maintenance deadline, workshop appointment or repair plan has changed.',requires_car:false},
    {id:'capacity_change',label:'Capacity or booking change',description:'Capacity sold, blocked compartments or the required product mix has changed.',requires_car:false},
    {id:'service_delay',label:'Service delay',description:'A delayed arrival or departure affects preparation, connections or the next service.',requires_car:false},
    {id:'resource_unavailable',label:'Resource unavailable',description:'A shunter, track, operator, crew or another required resource is unavailable.',requires_car:false},
    {id:'formation_change',label:'Formation or operating restriction',description:'A new train-length, orientation, power or route restriction changes the formation.',requires_car:false},
    {id:'workshop_release',label:'Workshop car released',description:'A car becomes available for collection and needs a placement or transfer decision.',requires_car:true},
    {id:'other',label:'Other operational change',description:'Record another change, propose actions and compare the evidence and dependencies.',requires_car:false}
  ];
  function caseTypes() { return state.bootstrap?.case_types?.length ? state.bootstrap.case_types : defaultCaseTypes; }
  function caseType(id) { return caseTypes().find(type => type.id === id) || defaultCaseTypes[0]; }

  function node(tag, attrs = {}, ...children) {
    const item = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (name === 'className') item.className = value;
      else if (name === 'text') item.textContent = value;
      else if (name.startsWith('on') && typeof value === 'function') item.addEventListener(name.slice(2).toLowerCase(), value);
      else if (name in item && ['checked', 'disabled', 'value', 'hidden', 'required'].includes(name)) item[name] = value;
      else item.setAttribute(name, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      item.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return item;
  }

  function text(value, fallback = 'Unknown') {
    if (value === null || value === undefined || value === '') return fallback;
    if (Array.isArray(value)) return value.map(v => text(v, '')).filter(Boolean).join(' · ') || fallback;
    if (typeof value === 'object') return Object.entries(value).map(([key, val]) => `${key.replaceAll('_', ' ')}: ${text(val, 'unknown')}`).join(' · ') || fallback;
    return String(value);
  }
  function lineName(id) { return state.bootstrap?.lines?.find(line => line.id === id)?.name || id || 'Train'; }
  function dateLabel(value) {
    if (!value) return 'Date unknown';
    const date = new Date(String(value).slice(0, 10) + 'T12:00:00');
    return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(date);
  }
  function timeLabel(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Brussels' }).format(date);
  }
  function badge(status, label) { return node('span', { className: `badge ${['ready', 'conditional', 'blocked', 'pass', 'pending', 'fail', 'error', 'approved', 'open', 'selected', 'provisional'].includes(status) ? status : 'neutral'}` }, label || labels[status] || text(status, 'Not recorded')); }
  function isAssigned(car) { return [0, -1, 1, 2, 3, 4, 9].includes(Number(car.code)) && car.code !== null && car.code !== undefined && String(car.code).trim() !== ''; }
  function error(message) { $('global-error').textContent = text(message, 'Something went wrong. Please try again.'); $('global-error').hidden = false; }
  function clearError() { $('global-error').hidden = true; $('global-error').textContent = ''; }
  function status(message) { $('global-status').textContent = message || ''; }
  function setBusy(busy) { state.busy = busy; document.querySelectorAll('[data-mutation]').forEach(button => button.disabled = busy || button.dataset.ineligible === 'true'); }
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    let payload;
    try { payload = await response.json(); } catch { throw new Error('The local server returned an unreadable response. Check that Sleeper Operations is running.'); }
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
    return payload;
  }
  async function mutation(action) {
    if (state.busy) return;
    clearError(); status(''); setBusy(true);
    try { await action(); } catch (err) { error(err.message); }
    finally { setBusy(false); }
  }
  function setTab(tab) {
    state.tab = tab;
    for (const name of ['decisions', 'fleet', 'log']) {
      $('view-' + name).hidden = name !== tab;
      $('tab-' + name).setAttribute('aria-selected', String(name === tab));
    }
    if (tab === 'log') loadLog();
  }
  function currentOption() { return state.activeCase?.options?.find(option => option.id === state.selectedOption) || null; }
  function acceptCase(caseData) {
    if (!caseData || !caseData.id) throw new Error('The server did not return a valid decision record.');
    const sameCase = state.activeCase?.id === caseData.id;
    state.activeCase = caseData;
    state.caseCache.set(caseData.id, caseData);
    if (!sameCase || !caseData.options?.some(option => option.id === state.selectedOption)) state.selectedOption = caseData.decision?.option_id || caseData.options?.[0]?.id || null;
    if (!caseData.options?.some(option => option.id === state.selectedOption)) state.selectedOption = caseData.options?.[0]?.id || null;
    $('new-case-panel').hidden = true;
    $('case-workspace').hidden = false;
    renderCaseList(); renderCase();
  }
  async function refreshCases() {
    const result = await api('/api/cases');
    state.cases = Array.isArray(result) ? result : result.cases || [];
    renderCaseList();
  }
  function renderCaseList() {
    $('case-count').textContent = String(state.cases.length);
    if (!state.cases.length) { $('case-list').replaceChildren(node('p', { className: 'empty-state' }, 'No decisions yet. Open an operational case to compare responses.')); return; }
    $('case-list').replaceChildren(...state.cases.map(item => node('button', { type: 'button', className: 'case-item', 'aria-current': String(state.activeCase?.id === item.id), onClick: () => openCase(item.id) },
      node('span', { className: 'case-title' }, `${lineName(item.line)} · ${item.car_id || caseType(item.case_type || 'car_unavailable').label}`),
      node('span', { className: 'case-reason' }, text(item.reason, 'Disruption case')),
      node('span', { className: 'case-meta' }, dateLabel(item.date), badge(item.status || 'open'))
    )));
  }
  async function loadPlan(line, date, { keepCase = false } = {}) {
    const request = ++state.planRequest;
    state.plan = null;
    renderAffectedCars(); renderFleet();
    $('load-plan').disabled = true;
    $('route-label').textContent = 'Loading planning snapshot…';
    try {
      const plan = await api(`/api/plan?line=${encodeURIComponent(line)}&date=${encodeURIComponent(date)}`);
      if (request !== state.planRequest) return;
      state.plan = plan;
      if (!keepCase && state.activeCase && (state.activeCase.line !== line || state.activeCase.date !== date)) showNewCase();
      const lineInfo = state.bootstrap?.lines?.find(item => item.id === line);
      $('route-label').textContent = `${plan.route || lineInfo?.route || lineName(line)} · ${dateLabel(date)}`;
      $('source-context').textContent = 'Imported spreadsheet snapshot · historical records and future planning rows may differ.';
      $('case-scope-label').textContent = `${lineName(line)} · ${dateLabel(date)}. Original spreadsheet data will stay unchanged.`;
      renderAffectedCars(); renderFleet();
      if(state.activeCase && state.activeCase.line===line && state.activeCase.date===date)renderCase();
    } catch (err) {
      if (request === state.planRequest) { state.plan = null; $('route-label').textContent = 'Planning snapshot unavailable'; renderAffectedCars(); renderFleet(); throw err; }
    } finally { if (request === state.planRequest) $('load-plan').disabled = false; }
  }
  function renderAffectedCars() {
    const type = caseType($('case-type').value);
    const catalogue = type.id === 'workshop_release';
    const cars = catalogue ? (state.bootstrap?.cars || []).map(car => ({...car,car_id:car.id})) : (state.plan?.cars || []).filter(isAssigned);
    $('affected-car').replaceChildren(node('option', { value: '' }, type.requires_car ? (cars.length ? (catalogue ? 'Choose a fleet car' : 'Choose an assigned car') : 'No suitable car records available') : 'No specific car / whole service'), ...cars.map(car => node('option', { value: car.car_id }, `${car.car_id} · ${text(car.type)}${car.product ? ' · '+car.product : ''}`)));
    $('affected-car').required = Boolean(type.requires_car);
    $('affected-car').disabled = !cars.length && Boolean(type.requires_car);
    $('affected-car-label').textContent = catalogue ? 'Released car' : type.requires_car ? 'Affected car' : 'Affected car (optional)';
    $('case-type-description').textContent = type.description || '';
    $('delay-field').hidden = !['service_delay','resource_unavailable'].includes(type.id);
    $('resource-field').hidden = !['resource_unavailable','maintenance_change','formation_change','workshop_release','other'].includes(type.id);
    $('product-field').hidden = !['capacity_change','car_unavailable','formation_change','maintenance_change','workshop_release','other'].includes(type.id);
    $('capacity-field').hidden = type.id !== 'capacity_change';
    const invalid = !state.plan || (type.requires_car && !cars.length);
    $('create-case').dataset.ineligible = String(invalid);
    $('create-case').disabled = state.busy || invalid;
  }
  function showNewCase() {
    ++state.caseRequest;
    state.activeCase = null; state.selectedOption = null;
    $('new-case-panel').hidden = false; $('case-workspace').hidden = true;
    renderCaseList();
  }
  async function openCase(id) {
    if (state.busy) return;
    const request = ++state.caseRequest;
    clearError(); status('Loading decision…');
    try {
      const caseData = await api(`/api/cases/${encodeURIComponent(id)}`);
      if (request !== state.caseRequest) return;
      $('line-picker').value = caseData.line; $('date-picker').value = caseData.date;
      acceptCase(caseData); setTab('decisions');
      await loadPlan(caseData.line, caseData.date, { keepCase: true });
      if (request === state.caseRequest) status('');
    } catch (err) { if (request === state.caseRequest) { status(''); error(err.message); } }
  }
  function factsList(pairs, className = 'detail-facts') {
    return node('dl', { className }, pairs.map(([label, value]) => node('div', {}, node('dt', {}, label), node('dd', {}, text(value)))));
  }
  function renderCase() {
    const item = state.activeCase;
    if (!item) return;
    const option = currentOption();
    const impactedCar = state.plan?.cars?.find(car => car.car_id === item.car_id);
    const type = caseType(item.case_type || 'car_unavailable');
    const heading = node('div', { className: 'case-context' },
      node('div', { className: 'section-heading' }, node('div', {}, node('div', { className: 'eyebrow' }, `${lineName(item.line)} · ${dateLabel(item.date)}`), node('h2', {}, `${type.label}${item.car_id ? ' · '+item.car_id : ''}`)), badge(item.status || 'open')),
      node('p', { className: 'reason' }, item.reason),
      node('p', { className: 'small muted' }, `Case ${item.id} · ${timeLabel(item.created_at)}`),
      item.context && Object.keys(item.context).length ? node('p', {className:'small muted'}, Object.entries(item.context).filter(([,value])=>value!==null && value!=='').map(([key,value])=>`${key.replaceAll('_',' ')}: ${text(value)}`).join(' · ')) : null
    );
    const impact = node('div', { className: 'impact-strip' },
      [['Affected car', item.car_id || 'Service-level change'], ['Product', item.context?.product || impactedCar?.product || impactedCar?.type || 'Not specified'], ['Affected passengers', item.affected_passengers === null || item.affected_passengers === undefined ? 'Not yet recorded' : item.affected_passengers]].map(([key, value]) => node('dl', {}, node('dt', {}, key), node('dd', {}, text(value))))
    );
    const rows = (item.options || []).map(candidate => node('tr', { 'data-selected': String(candidate.id === state.selectedOption) },
      node('td', {}, node('label', { className: 'option-label' }, node('input', { type: 'radio', name: 'response-option', value: candidate.id, checked: candidate.id === state.selectedOption, onChange: () => {state.selectedOption = candidate.id; renderCase();} }), node('span', {}, node('strong', {}, candidate.title), node('small', {}, candidate.description || candidate.movement || 'Inspect the checks before choosing.')))),
      node('td', { 'data-label': 'Impact and movement' }, node('p', {}, text(candidate.passenger_impact)), node('p', { className: 'small muted' }, text(candidate.movement))),
      node('td', { 'data-label': 'Readiness and added cost' }, badge(candidate.status), node('p', { className: 'small muted', style: 'margin-top:7px' }, text(candidate.cost_label, 'Cost not confirmed')))
    ));
    const comparison = node('section', {}, node('div', { className: 'section-heading' }, node('h2', {}, 'Compare responses'), node('span', { className: 'small muted' }, `${rows.length} candidate actions`)), item.ranking_note ? node('p', { className: 'small muted', style: 'margin-bottom:14px' }, item.ranking_note) : null, rows.length ? node('table', { className: 'option-table', 'aria-label': 'Candidate responses to the operational change' }, node('thead', {}, node('tr', {}, ['Action', 'Impact and movement', 'Readiness and added cost'].map(label => node('th', { scope: 'col' }, label)))), node('tbody', {}, rows)) : node('p', { className: 'empty-state' }, 'No response candidates are available. Review the source plan and record further operational information.'));
    const children = [heading, impact, comparison, renderCustomOptionForm(item)];
    if (option) children.push(node('div', { className: 'option-details-grid' }, renderOptionDetails(item, option), renderCommunication(item, option)));
    $('case-workspace').replaceChildren(...children);
    setBusy(state.busy);
  }
  function renderOptionDetails(item, option) {
    const failed = (option.checks || []).filter(check => check.status === 'fail');
    const pending = (option.checks || []).filter(check => check.status !== 'pass' && check.status !== 'fail');
    const eligible = option.status === 'ready' && !failed.length && !pending.length && !(option.blockers || []).length;
    const checkList = node('ul', { className: 'check-list' }, (option.checks || []).map(check => node('li', {}, node('details', {className:'evidence-detail'}, node('summary', { className: 'check-title' }, node('span', {}, check.label), badge(check.status)), node('p', { className: 'check-evidence' }, text(check.evidence, 'Evidence has not been recorded.'))))));
    const rationale = node('textarea', { rows: '3', maxlength: '4000', required: true, placeholder: 'Explain why this action is preferred and which dependencies remain.', value: state.rationaleDrafts.get(item.id) ?? item.decision?.rationale ?? '', onInput: event => state.rationaleDrafts.set(item.id, event.target.value) });
    const canSave = option.status !== 'blocked' && !failed.length;
    const provisional = node('button', { type: 'submit', className: 'button secondary', 'data-mutation': '', 'data-ineligible': String(!canSave), disabled: !canSave || state.busy }, 'Save provisional choice');
    const approve = node('button', { type: 'button', className: 'button primary', 'data-mutation': '', 'data-ineligible': String(!eligible), disabled: !eligible || state.busy, onClick: () => saveDecision(true, rationale.value) }, 'Approve this action');
    const form = node('form', { className: 'decision-form', onSubmit: event => {event.preventDefault(); if (event.currentTarget.reportValidity()) saveDecision(false, rationale.value);} },
      node('label', {}, 'Decision rationale', rationale),
      node('div', { className: 'button-row' }, provisional, approve),
      node('p', { className: 'small muted' }, !canSave ? 'Source-backed conflicts block this action. Review another candidate or correct the conflicting source data.' : eligible ? 'All configured checks are recorded. Review the evidence before approval.' : 'A provisional choice can be saved now. Approval stays unavailable while any configured check is pending or failed.')
    );
    return node('section', { className: 'panel option-details', 'aria-label': 'Selected option and operating checks' },
      node('div', { className: 'section-heading' }, node('h2', {}, option.title), badge(option.status)),
      node('p', {}, option.description || 'Review this candidate against its recorded evidence.'),
      factsList([['Passenger impact', option.passenger_impact], ['Required movement', option.movement], ['Added cost', option.cost_label || 'Not confirmed'], ['Remaining checks', `${pending.length} pending · ${failed.length} failed`]]),
      checkList,
      form
    );
  }
  function renderCustomOptionForm(item) {
    const title=node('input',{type:'text',required:true,maxlength:'200',placeholder:'For example: combine collection with an existing train movement'});
    const description=node('textarea',{required:true,rows:'3',maxlength:'4000',placeholder:'Describe the action, its intended outcome and its dependencies.'});
    const impact=node('input',{type:'text',maxlength:'1000',placeholder:'Unknown until bookings are checked'});
    const movement=node('input',{type:'text',maxlength:'1000',placeholder:'Required shunting, transfer or timetable changes'});
    const cost=node('input',{type:'text',maxlength:'1000',placeholder:'Unknown; quote needed'});
    const form=node('form',{className:'form-stack panel',onSubmit:event=>{
      event.preventDefault();if(!event.currentTarget.reportValidity())return;
      mutation(async()=>{
        const updated=await api(`/api/cases/${encodeURIComponent(item.id)}/options`,{method:'POST',body:JSON.stringify({title:title.value.trim(),description:description.value.trim(),passenger_impact:impact.value.trim()||'Unknown; booking impact needs review',movement:movement.value.trim()||'To be established',cost_label:cost.value.trim()||'Unknown; quote needed'})});
        const existing=new Set((item.options||[]).map(option=>option.id));
        const added=updated.options?.find(option=>!existing.has(option.id));
        acceptCase(updated);if(added){state.selectedOption=added.id;renderCase();}await refreshCases();status('Proposed action saved. Its feasibility checks start pending.');
      });
    }},node('label',{},'Action title',title),node('label',{},'Description and dependencies',description),node('label',{},'Expected passenger impact',impact),node('label',{},'Movement or operational change',movement),node('label',{},'Cost information',cost),node('p',{className:'small muted'},'A proposal is not a validated plan. Record evidence and confirmations before approval.'),node('div',{},node('button',{type:'submit',className:'button secondary','data-mutation':''},'Add candidate action')));
    return node('details',{className:'custom-option-form'},node('summary',{},'Propose another action'),form);
  }
  async function saveDecision(approve, rationale) {
    const caseId = state.activeCase?.id, optionId = state.selectedOption;
    if (!caseId || !optionId) return;
    if (!rationale.trim()) { error('Add the reason for this choice before saving it.'); return; }
    await mutation(async () => {
      const updated = await api(`/api/cases/${encodeURIComponent(caseId)}/decision`, { method: 'POST', body: JSON.stringify({ option_id: optionId, rationale: rationale.trim(), approve }) });
      acceptCase(updated); await refreshCases();
      status(approve ? 'Action approved and decision saved. Review the handoff drafts in the decision log.' : 'Provisional choice saved. Review the handoff drafts in the decision log.');
    });
  }
  function updateFeed(updates) {
    if (!updates?.length) return node('p', { className: 'empty-state' }, 'No department updates recorded yet.');
    return node('div', { className: 'update-feed' }, [...updates].reverse().map(update => node('article', { className: 'update-message' },
      node('div', { className: 'update-meta' }, node('strong', {}, text(update.department)), node('time', {}, timeLabel(update.created_at))),
      node('p', {}, text(update.message)),
      update.confirmations?.length ? node('p', { className: 'confirmed-fields' }, `Recorded confirmations: ${update.confirmations.map(id => { const check = state.activeCase?.options?.flatMap(option => option.checks || []).find(check => check.id === id); return check?.label || id; }).join(', ')}`) : null
      ,update.revoked_confirmations?.length ? node('p',{className:'small muted',style:'margin-top:6px'},`Withdrawn confirmations: ${update.revoked_confirmations.join(', ')}`) : null
    )));
  }
  function renderCommunication(item, option) {
    const cached = state.updateDrafts.get(item.id) || { department: 'TCS operations', message: '' };
    const department = node('select', {}, ['TCS operations', 'Workshop / ECM', 'Fleet desk', 'Customer service', 'ES operations', 'Other'].map(name => node('option', { value: name }, name)));
    department.value = cached.department;
    department.addEventListener('change', () => state.updateDrafts.set(item.id, { ...cached, department: department.value, message: message.value }));
    const message = node('textarea', { rows: '4', required: true, maxlength: '8000', placeholder: 'Paste or write the received update. Include its conditions and source.', value: cached.message, onInput: event => state.updateDrafts.set(item.id, { department: department.value, message: event.target.value }) });
    const checkboxes = (option.checks || []).filter(check => check.status === 'pending').map(check => node('label', { className: 'inline-check' }, node('input', { type: 'checkbox', value: check.id, name: 'confirmations' }), node('span', {}, check.label)));
    const confirmationGroup = node('fieldset', { className: 'confirmation-group' }, node('legend', {}, 'Facts confirmed by this message'), node('p', { className: 'small muted' }, 'Tick only confirmations actually received for the selected option.'), node('div', { className: 'confirmation-list' }, checkboxes.length ? checkboxes : node('p', { className: 'small muted' }, 'No pending checks can be confirmed for this option. Failed source checks cannot be overridden here.')));
    const recorded = new Set();
    [...(item.updates||[])].sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at))).filter(update=>update.option_id===option.id).forEach(update=>{(update.confirmations||[]).forEach(id=>recorded.add(id));(update.revoked_confirmations||[]).forEach(id=>recorded.delete(id));});
    const revokeChecks=(option.checks||[]).filter(check=>check.status==='pass' && recorded.has(check.id));
    const revokeGroup=revokeChecks.length ? node('details',{},node('summary',{className:'small muted'},'This update withdraws an earlier confirmation'),node('div',{className:'confirmation-list'},revokeChecks.map(check=>node('label',{className:'inline-check'},node('input',{type:'checkbox',value:check.id,name:'revocations'}),node('span',{},check.label))))) : null;
    const form = node('form', { className: 'form-stack', onSubmit: event => {
      event.preventDefault();
      if (!event.currentTarget.reportValidity()) return;
      const confirmations = Array.from(event.currentTarget.querySelectorAll('input[name="confirmations"]:checked')).map(input => input.value);
      const revoke_confirmations = Array.from(event.currentTarget.querySelectorAll('input[name="revocations"]:checked')).map(input => input.value);
      const body = { department: department.value, message: message.value.trim(), option_id: option.id, confirmations, revoke_confirmations };
      if (!body.message) { error('Enter the received department update.'); return; }
      mutation(async () => {
        const updated = await api(`/api/cases/${encodeURIComponent(item.id)}/updates`, { method: 'POST', body: JSON.stringify(body) });
        state.updateDrafts.delete(item.id); acceptCase(updated); await refreshCases(); status('Department update recorded. The option checks have been refreshed.');
      });
    } }, node('label', {}, 'Department / source', department), node('label', {}, 'Received update', message), confirmationGroup, revokeGroup, node('div', {}, node('button', { type: 'submit', className: 'button secondary', 'data-mutation': '' }, 'Record received update')));
    return node('aside', { className: 'panel communication', 'aria-label': 'Communication linked to this operational case' }, node('div', { className: 'section-heading' }, node('div', {}, node('div', { className: 'eyebrow' }, 'Linked communication'), node('h2', {}, 'Updates and confirmations'))), updateFeed(item.updates), node('h3', {}, 'Add a department update'), form);
  }
  function renderFleet() {
    const plan = state.plan;
    if (!plan) {
      $('plan-summary').replaceChildren(node('p', { className: 'empty-state' }, 'Load a planning snapshot to inspect its car records.'));
      $('fleet-body').replaceChildren(); $('plan-issues').replaceChildren(); $('running-data').replaceChildren(node('p', { className: 'empty-state' }, 'No snapshot selected.')); return;
    }
    const stats = plan.statistics || {};
    $('plan-summary').replaceChildren(node('div', { className: 'snapshot-stats' }, [['Commercial cars', stats.commercial_cars], ['Nominal product capacity', stats.nominal_capacity], ['Train passenger count', stats.passengers]].map(([label, value]) => node('dl', {}, node('dt', {}, label), node('dd', {}, text(value))))), node('p', { className: 'small muted', style: 'margin-bottom:18px' }, 'Capacity is nominal and recomputed from car codes. Staff compartments, blocked capacity, product setup, and actual bookings need separate confirmation.'));
    const issues = plan.issues || [];
    $('plan-issues').replaceChildren(issues.length ? node('div', { className: 'issue-list' }, issues.map(issue => node('p', {}, badge(issue.severity || 'neutral', issue.severity === 'error' ? 'Conflict' : issue.severity === 'warning' ? 'Check' : 'Note'), node('span', {}, text(issue.message))))) : node('p', { className: 'small muted', style: 'margin-bottom:18px' }, 'No issues detected by the available snapshot checks. This does not establish complete operational feasibility.'));
    const cars = (plan.cars || []).filter(car => !$('assigned-only').checked || isAssigned(car));
    $('fleet-body').replaceChildren(...(cars.length ? cars.map(car => node('tr', {},
      node('td', {}, node('strong', {}, car.car_id)),
      node('td', {}, node('p', {}, text(car.owner)), node('p', { className: 'small muted' }, text(car.type))),
      node('td', {}, node('p', {}, text(car.code, 'Blank')), node('p', { className: 'small muted' }, text(car.product, 'No sold product recorded'))),
      node('td', {}, node('p', {}, text(car.colour_meaning || car.status, 'No status recorded')), node('p', { className: 'small muted' }, text(car.location, 'Location not established')), car.notes ? node('p', { className: 'small muted' }, text(car.notes)) : null),
      node('td', { className: 'small' }, car.source ? `${text(car.source.sheet, plan.line)}!${text(car.source.cell, '?')}` : 'Not available')
    )) : [node('tr', {}, node('td', { colspan: '5', className: 'empty-state' }, 'No matching car records for this line and date.'))]));
    const running = plan.running;
    if (running) $('running-data').replaceChildren(factsList([['Departure deviation', delay(running.departure_delay)], ['Arrival deviation', delay(running.arrival_delay)], ['Recorded passengers', running.pax], ['Incident notes', running.notes || 'No note recorded']], 'detail-facts'), node('p', { className: 'small muted panel-footnote' }, 'Running results are historical observations, not a timetable. Negative minute deviations indicate an early train. Missing values remain unknown.'));
    else $('running-data').replaceChildren(node('p', { className: 'empty-state' }, 'No daily operating result is available for this date. Weekly summary rows are not shown as individual runs.'));
  }
  function delay(value) { return typeof value === 'number' ? `${value > 0 ? '+' : ''}${value} min` : text(value, 'Not recorded'); }
  function handoffText(item) {
    const option = item.options?.find(candidate => candidate.id === item.decision?.option_id);
    return [`SLEEPER OPERATIONS — HANDOFF DRAFT`, `Case: ${item.id}`, `Type: ${caseType(item.case_type||'car_unavailable').label}`, `Line: ${lineName(item.line)}`, `Date: ${item.date}`, `Affected car: ${item.car_id || 'Service-level change'}`, `Context: ${text(item.context,'Not specified')}`, `Change: ${item.reason}`, `Decision: ${option?.title || 'Not selected'}`, `Status: ${item.decision?.approved ? 'Approved by planner' : 'Provisional — not approved'}`, `Rationale: ${item.decision?.rationale || 'Not recorded'}`, '', ...(item.drafts || []).flatMap(draft => [`${draft.department || 'Department'}`, draft.text || '', '']), 'Draft instructions only. No messages have been sent by this application.'].join('\n');
  }
  function downloadHandoff(item) {
    const blob = new Blob([handoffText(item)], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob), link = node('a', { href: url, download: `sleeper-handoff-${String(item.id).replace(/[^a-zA-Z0-9_-]/g, '')}.txt` });
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); status('Handoff draft downloaded.');
  }
  async function copyHandoff(item) {
    try { if (!navigator.clipboard?.writeText) throw new Error(); await navigator.clipboard.writeText(handoffText(item)); status('Handoff draft copied.'); }
    catch { error('Clipboard access is unavailable in this browser. Use Download handoff instead.'); }
  }
  async function loadLog() {
    const request = ++state.logRequest;
    $('decision-log').replaceChildren(node('p', { className: 'empty-state' }, 'Loading decision records…'));
    try {
      await refreshCases();
      const records = await Promise.all(state.cases.map(item => api(`/api/cases/${encodeURIComponent(item.id)}`)));
      if (request !== state.logRequest) return;
      records.forEach(item => state.caseCache.set(item.id, item));
      if (!records.length) { $('decision-log').replaceChildren(node('p', { className: 'empty-state' }, 'No operational cases yet. Start in the decision desk.')); return; }
      $('decision-log').replaceChildren(node('div', { className: 'log-grid' }, records.map(renderLogEntry)));
    } catch (err) { if (request === state.logRequest) { $('decision-log').replaceChildren(node('p', { className: 'empty-state' }, 'Decision records could not be loaded.')); error(err.message); } }
  }
  function renderLogEntry(item) {
    const option = item.options?.find(candidate => candidate.id === item.decision?.option_id);
    const section = node('article', { className: 'panel log-entry' },
      node('div', { className: 'section-heading' }, node('div', {}, node('div', { className: 'eyebrow' }, `${lineName(item.line)} · ${dateLabel(item.date)} · ${item.car_id ? 'car '+item.car_id : caseType(item.case_type||'car_unavailable').label}`), node('h2', {}, option?.title || 'No action selected yet')), badge(item.status || 'open')),
      node('p', {}, item.reason),
      item.decision ? node('p', { className: 'muted' }, item.decision.rationale || 'No rationale recorded.') : node('p', { className: 'muted' }, 'Review the options and save a choice to prepare a handoff.'),
      node('div', { className: 'log-actions' }, node('button', { type: 'button', className: 'button secondary', onClick: () => openCase(item.id) }, 'Open decision'), item.decision ? node('button', { type: 'button', className: 'button secondary', onClick: () => downloadHandoff(item) }, 'Download handoff') : null, item.decision ? node('button', { type: 'button', className: 'button secondary', onClick: () => copyHandoff(item) }, 'Copy handoff') : null)
    );
    if (item.drafts?.length) section.append(node('div', { className: 'draft-list' }, item.drafts.map(draft => node('section', { className: 'draft' }, node('div', { className: 'section-heading' }, node('h3', {}, draft.department || 'Department'), badge('neutral', 'Draft · not sent')), node('p', {}, draft.text || '')))));
    section.append(node('details', {}, node('summary', {}, `${item.updates?.length || 0} received department updates`), node('div', { style: 'margin-top:14px' }, updateFeed(item.updates))));
    return section;
  }
  async function boot() {
    clearError();
    try {
      state.bootstrap = await api('/api/bootstrap');
      const { meta = {}, lines = [] } = state.bootstrap;
      $('case-type').replaceChildren(...caseTypes().map(type=>node('option',{value:type.id},type.label)));
      $('line-picker').replaceChildren(...lines.map(line => node('option', { value: line.id }, line.name || line.id)));
      $('line-picker').value = lines.some(line => line.id === 'Milan') ? 'Milan' : lines[0]?.id || '';
      $('date-picker').value = meta.suggested_date || meta.date_max || '';
      if (meta.date_min) $('date-picker').min = meta.date_min;
      if (meta.date_max) $('date-picker').max = meta.date_max;
      $('line-picker').disabled = !lines.length; $('date-picker').disabled = !lines.length; $('load-plan').disabled = !lines.length;
      const warnings = Array.isArray(meta.warnings) ? meta.warnings : [];
      $('warning-list').replaceChildren(node('p', { style: 'margin-top:10px' }, `Sources: ${(meta.source_files || []).map(source => typeof source === 'object' ? source.name || source.filename || 'Spreadsheet' : source).join(', ') || 'Not recorded'}`), node('p', { className: 'small', style: 'margin-top:5px' }, `Imported: ${timeLabel(meta.imported_at) || 'Not recorded'}`), node('ul', {}, warnings.length ? warnings.map(warning => node('li', {}, text(warning))) : node('li', {}, 'No importer warnings recorded. Operational checks can still require additional evidence.')));
      await refreshCases();
      if (!meta.imported) { error('No spreadsheet data is loaded. Start the application with the fleet and running workbook paths. See the README for setup.'); $('route-label').textContent = 'No imported data'; renderFleet(); return; }
      await loadPlan($('line-picker').value, $('date-picker').value);
      if (state.cases.length) await openCase(state.cases[0].id);
    } catch (err) { error(err.message); $('route-label').textContent = 'Could not connect to the local workspace'; }
  }

  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => setTab(button.dataset.tab)));
  const tabs = Array.from(document.querySelectorAll('[role=tab]'));
  tabs.forEach((tab, index) => tab.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus(); tabs[next].click();
  }));
  $('scope-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!event.currentTarget.reportValidity() || state.busy) return;
    clearError(); status('');
    try { await loadPlan($('line-picker').value, $('date-picker').value); } catch (err) { error(err.message); }
  });
  $('new-case-button').addEventListener('click', () => { if (state.busy) return; clearError(); status(''); showNewCase(); $('affected-car').focus(); });
  $('assigned-only').addEventListener('change', renderFleet);
  $('case-type').addEventListener('change',renderAffectedCars);
  $('case-form').addEventListener('submit', event => {
    event.preventDefault(); if (!event.currentTarget.reportValidity() || !state.plan) return;
    const passengers = $('affected-passengers').value.trim();
    const count = passengers === '' ? null : Number(passengers);
    if (count !== null && (!Number.isInteger(count) || count < 0)) { error('Affected passengers must be a whole number of zero or more, or left unknown.'); return; }
    const context={};
    if(!$('delay-field').hidden && $('context-delay').value!=='')context.delay_minutes=Number($('context-delay').value);
    if(!$('resource-field').hidden && $('context-resource').value.trim())context.resource=$('context-resource').value.trim();
    if(!$('product-field').hidden && $('context-product').value)context.product=$('context-product').value;
    if(!$('capacity-field').hidden && $('context-capacity').value!=='')context.capacity_gap=Number($('context-capacity').value);
    const body = { line: state.plan.line, date: state.plan.date, case_type:$('case-type').value, context, car_id: $('affected-car').value || null, reason: $('case-reason').value.trim(), affected_passengers: count };
    if (!body.reason) { error('Describe the change before assessing responses.'); return; }
    mutation(async () => {
      const result = await api('/api/cases', { method: 'POST', body: JSON.stringify(body) });
      $('case-reason').value = ''; $('affected-passengers').value = '';
      acceptCase(result); await refreshCases(); status('Operational case saved. Candidate actions and evidence are ready to review.');
    });
  });
  boot();
})();
