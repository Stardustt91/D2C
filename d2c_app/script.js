(function () {
  const statusEl = document.getElementById('status');
  const btnRun = document.getElementById('btn-run');
  const inputSection = document.getElementById('input-section');
  const treeSection = document.getElementById('tree-section');
  const treeRoot = document.getElementById('tree-root');
  const activityDescription = document.getElementById('activity-description');
  const stepVerifyBar = document.getElementById('step-verify-bar');
  const stepVerifyLabel = document.getElementById('step-verify-label');
  const btnContinueNext = document.getElementById('btn-continue-next');
  const detailModal = new bootstrap.Modal(document.getElementById('detailModal'));
  const detailModalTitle = document.getElementById('detailModalTitle');
  const detailModalBody = document.getElementById('detailModalBody');
  const editModal = new bootstrap.Modal(document.getElementById('editModal'));
  const editModalTitle = document.getElementById('editModalTitle');
  const btnSaveEdit = document.getElementById('btnSaveEdit');
  const depthToggle = document.getElementById('depth-toggle');
  const treeContainer = document.getElementById('tree-container');
  const resourcePlanSection = document.getElementById('resource-plan-section');
  const resourcePlanRoot = document.getElementById('resource-plan-root');

  // Keep the segmented depth toggle's active state in sync with currentTreeDepth.
  function syncDepthButtons() {
    if (!depthToggle) return;
    depthToggle.querySelectorAll('button[data-depth]').forEach(function (b) {
      b.classList.toggle('active', parseInt(b.getAttribute('data-depth'), 10) === currentTreeDepth);
    });
  }

  // Global state for current structure, activity, and which layer we've completed (for verify-before-next)
  let currentStructure = null;
  let currentActivity = '';
  let currentEditContext = null;
  /** Structured answers from the chat assistant (volume, crews, shared pools). Sent to the plan step. */
  let currentActivityFacts = null;
  /** After a step completes: 'plan' | 'drivers' | 'components' | 'inputs' | 'parameters' | 'costs'. Used to show "Continue to next layer" */
  let currentStepCompleted = null;
  /** Tree view depth: 0=drivers only, 1=+components, 2=+inputs, 3=full (parameters) */
  let currentTreeDepth = 3;

  // Store full justification texts to avoid data-attribute length limits
  const justificationData = {};

  // Expose to window for CRUD module
  window.currentStructure = currentStructure;
  window.btnRun = btnRun;
  window.currentActivity = currentActivity;
  window.currentEditContext = currentEditContext;
  window.editModal = editModal;

  function setStatus(text, isError) {
    if (statusEl) {
      statusEl.textContent = text;
      statusEl.className = 'text-muted small' + (isError ? ' text-danger' : '');
    }
  }

  window.setStatus = setStatus;

  function updateStepButtons() {
    if (btnRun) btnRun.disabled = false;
    // Any settled state (new content rendered, done, error, timeout) clears the
    // non-destructive progress overlay on the tree.
    if (treeContainer) treeContainer.classList.remove('loading');
  }

  function updateStepVerifyBar() {
    if (!stepVerifyBar || !stepVerifyLabel || !btnContinueNext) return;
    var nextStep = null;
    var label = '';
    var btnText = '';
    if (currentStepCompleted === 'plan') {
      nextStep = 'drivers';
      label = 'Review the resourcing & scaling plan above. Correct any quantity or scaling class — these numbers feed every cost formula. Then continue.';
      btnText = 'Continue to cost drivers';
    } else if (currentStepCompleted === 'drivers') {
      nextStep = 'components';
      label = 'Review cost drivers above. Edit, add or remove as needed, then continue to estimate cost components.';
      btnText = 'Continue to cost components';
    } else if (currentStepCompleted === 'components') {
      nextStep = 'inputs';
      label = 'Review cost components. Edit, add or remove as needed, then continue to identify cost inputs.';
      btnText = 'Continue to cost inputs';
    } else if (currentStepCompleted === 'inputs') {
      nextStep = 'parameters';
      label = 'Review cost inputs. Edit, add or remove as needed, then continue to identify cost parameters and formulas.';
      btnText = 'Continue to cost parameters';
    } else if (currentStepCompleted === 'parameters') {
      nextStep = 'costs';
      label = 'Review cost parameters and formulas. Edit as needed, then continue to estimate costs.';
      btnText = 'Continue to cost estimation';
    } else if (currentStepCompleted === 'costs') {
      label = 'Estimation complete. You can still edit values or export.';
      btnText = '';
    }
    if (nextStep) {
      stepVerifyBar.classList.remove('d-none');
      stepVerifyLabel.textContent = label;
      btnContinueNext.textContent = btnText;
      btnContinueNext.style.display = '';
      btnContinueNext.onclick = function () { runStep(nextStep); };
    } else if (currentStepCompleted === 'costs') {
      stepVerifyBar.classList.remove('d-none');
      stepVerifyLabel.textContent = label;
      btnContinueNext.style.display = 'none';
    } else {
      stepVerifyBar.classList.add('d-none');
    }
  }

  function applyTreeDepth(maxDepth) {
    if (!treeRoot) return;
    var nodes = treeRoot.querySelectorAll('.tree-node[data-depth]');
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var depth = parseInt(node.getAttribute('data-depth'), 10);
      if (isNaN(depth)) continue;
      if (depth > maxDepth) {
        node.style.display = 'none';
      } else {
        node.style.display = 'flex';
        var directChildren = node.children;
        for (var j = 0; j < directChildren.length; j++) {
          var el = directChildren[j];
          if (el.classList && el.classList.contains('tree-node-children')) {
            el.style.display = depth === maxDepth ? 'none' : 'flex';
            break;
          }
        }
      }
    }
    syncToggleIcons();
  }

  // Finds the .tree-node-children container that is a direct child of a tree node.
  function directChildrenContainer(node) {
    if (!node) return null;
    for (var j = 0; j < node.children.length; j++) {
      var el = node.children[j];
      if (el.classList && el.classList.contains('tree-node-children')) return el;
    }
    return null;
  }

  // Keep each collapse toggle's glyph in sync with whether its children are shown.
  // Runs after applyTreeDepth so the global depth control and per-box toggles agree.
  function syncToggleIcons() {
    if (!treeRoot) return;
    var toggles = treeRoot.querySelectorAll('.entity-toggle');
    for (var i = 0; i < toggles.length; i++) {
      var toggle = toggles[i];
      var box = toggle.parentElement;              // .entity-box
      var node = box ? box.parentElement : null;   // .tree-node
      var children = directChildrenContainer(node);
      if (!children) { toggle.style.display = 'none'; continue; }
      toggle.style.display = '';
      toggle.textContent = (children.style.display === 'none') ? '+' : '−';
    }
  }

  var SCALING_CLASSES = ['per_unit_of_volume', 'capacity_pool', 'time_based', 'fixed_one_time'];
  var SCALING_CLASS_LABELS = {
    per_unit_of_volume: 'Per unit of volume',
    capacity_pool: 'Capacity pool (shared)',
    time_based: 'Time-based',
    fixed_one_time: 'Fixed one-time'
  };

  function renderResourcePlan(plan) {
    if (!resourcePlanSection || !resourcePlanRoot) return;
    if (!plan) {
      resourcePlanSection.classList.add('d-none');
      return;
    }
    resourcePlanSection.classList.remove('d-none');

    var vd = plan.volume_driver || {};
    var crews = plan.crews || {};
    var thr = plan.throughput || {};

    var html = '<div class="row g-2 mb-3">';
    function summaryField(label, field, value, type, title) {
      return '<div class="col-auto"><label class="form-label small text-muted mb-0">' + escapeHtml(label) + '</label>' +
        '<input type="' + (type || 'number') + '" class="form-control form-control-sm plan-summary-input" style="max-width:170px;" ' +
        'data-plan-field="' + field + '" value="' + escapeAttr(value != null ? String(value) : '') + '"' +
        (title ? ' title="' + escapeAttr(title) + '"' : '') + '></div>';
    }
    html += summaryField('Volume (' + escapeHtml(vd.unit || vd.name || 'units') + ')', 'volume_value', vd.value, 'number', vd.derivation || '');
    html += summaryField('Duration (months)', 'duration_months', plan.duration_months, 'number');
    html += summaryField('Working days/month', 'working_days_per_month', plan.working_days_per_month, 'number');
    html += summaryField('Parallel crews', 'crews_count', crews.count, 'number', crews.derivation || '');
    html += '</div>';

    if (crews.composition) {
      html += '<p class="small mb-1"><strong>Crew composition:</strong> ' + escapeHtml(crews.composition) + '</p>';
    }
    if (thr.value != null) {
      html += '<p class="small mb-2"><strong>Throughput:</strong> ' + escapeHtml(String(thr.value)) + ' ' + escapeHtml(thr.unit || '') +
        (thr.derivation ? ' <span class="text-muted">— ' + escapeHtml(thr.derivation) + '</span>' : '') + '</p>';
    }

    html += '<div class="table-responsive"><table class="table table-sm align-middle mb-2">';
    html += '<thead><tr><th>Resource</th><th>Scaling class</th><th style="width:110px;">Quantity</th><th style="width:110px;">Unit</th><th>Confidence</th><th>Derivation</th><th>Justification</th><th></th></tr></thead><tbody>';

    var resources = plan.resources || [];
    for (var i = 0; i < resources.length; i++) {
      var r = resources[i];
      html += '<tr>';
      html += '<td><input type="text" class="form-control form-control-sm plan-res-input" data-idx="' + i + '" data-field="resource_name" value="' + escapeAttr(r.resource_name || '') + '"></td>';
      html += '<td><select class="form-select form-select-sm plan-res-input" data-idx="' + i + '" data-field="scaling_class">';
      for (var s = 0; s < SCALING_CLASSES.length; s++) {
        var sc = SCALING_CLASSES[s];
        html += '<option value="' + sc + '"' + (r.scaling_class === sc ? ' selected' : '') + '>' + SCALING_CLASS_LABELS[sc] + '</option>';
      }
      html += '</select></td>';
      html += '<td><input type="number" step="any" class="form-control form-control-sm plan-res-input" data-idx="' + i + '" data-field="quantity" value="' + escapeAttr(r.quantity != null ? String(r.quantity) : '') + '"></td>';
      html += '<td><input type="text" class="form-control form-control-sm plan-res-input" data-idx="' + i + '" data-field="unit" value="' + escapeAttr(r.unit || '') + '"></td>';
      html += '<td><span class="badge ' + (r.confidence === 'high' ? 'bg-success' : r.confidence === 'medium' ? 'bg-warning text-dark' : 'bg-secondary') + '">' + escapeHtml(r.confidence || 'n/a') + '</span></td>';
      html += '<td class="small text-muted">' + escapeHtml(r.derivation || '') + '</td>';
      var srcBadge = '';
      if (r.source) {
        var isHist = String(r.source).toLowerCase() === 'historical';
        srcBadge = '<span class="badge ' + (isHist ? 'bg-info text-dark' : 'bg-secondary') + ' mb-1">' +
          escapeHtml(isHist ? 'Historical' : 'Estimated') + '</span><br>';
      }
      html += '<td class="small text-muted" style="min-width:220px;">' + srcBadge + escapeHtml(r.justification || '') + '</td>';
      html += '<td><button type="button" class="btn btn-outline-danger btn-sm plan-res-delete" data-idx="' + i + '" title="Remove resource">&times;</button></td>';
      html += '</tr>';
    }
    html += '</tbody></table></div>';
    html += '<button type="button" class="btn btn-outline-vodafone btn-sm" id="btn-plan-add-resource">+ Add resource</button>';

    if (plan.assumptions && plan.assumptions.length) {
      html += '<div class="mt-3 small"><strong>Assumptions:</strong><ul class="mb-0">';
      for (var a = 0; a < plan.assumptions.length; a++) {
        html += '<li>' + escapeHtml(plan.assumptions[a]) + '</li>';
      }
      html += '</ul></div>';
    }

    resourcePlanRoot.innerHTML = html;

    // Bind summary field edits
    resourcePlanRoot.querySelectorAll('.plan-summary-input').forEach(function (input) {
      input.addEventListener('change', function () {
        var field = this.dataset.planField;
        var num = parseFloat(this.value);
        if (isNaN(num)) return;
        if (field === 'volume_value') {
          plan.volume_driver = plan.volume_driver || {};
          plan.volume_driver.value = num;
          plan.volume_driver.derivation = (plan.volume_driver.derivation || '') + ' [edited by user]';
        } else if (field === 'duration_months') {
          plan.duration_months = num;
        } else if (field === 'working_days_per_month') {
          plan.working_days_per_month = num;
        } else if (field === 'crews_count') {
          plan.crews = plan.crews || {};
          plan.crews.count = num;
          plan.crews.derivation = (plan.crews.derivation || '') + ' [edited by user]';
        }
      });
    });

    // Bind resource row edits
    resourcePlanRoot.querySelectorAll('.plan-res-input').forEach(function (input) {
      input.addEventListener('change', function () {
        var idx = parseInt(this.dataset.idx, 10);
        var field = this.dataset.field;
        if (isNaN(idx) || !plan.resources || !plan.resources[idx]) return;
        var val = this.value;
        if (field === 'quantity') {
          var q = parseFloat(val);
          if (isNaN(q)) return;
          plan.resources[idx].quantity = q;
          plan.resources[idx].derivation = (plan.resources[idx].derivation || '') + ' [quantity edited by user]';
          plan.resources[idx].confidence = 'high';
        } else {
          plan.resources[idx][field] = val;
        }
      });
    });

    // Delete resource row
    resourcePlanRoot.querySelectorAll('.plan-res-delete').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(this.dataset.idx, 10);
        if (isNaN(idx) || !plan.resources) return;
        plan.resources.splice(idx, 1);
        renderResourcePlan(plan);
      });
    });

    // Add resource row
    var addBtn = resourcePlanRoot.querySelector('#btn-plan-add-resource');
    if (addBtn) {
      addBtn.addEventListener('click', function () {
        plan.resources = plan.resources || [];
        plan.resources.push({
          resource_name: '',
          scaling_class: 'capacity_pool',
          derivation: 'Added by user',
          quantity: 1,
          unit: '',
          confidence: 'high',
          source: 'estimated',
          justification: 'Added by user'
        });
        renderResourcePlan(plan);
      });
    }
  }

  function buildTree(structure) {
    currentStructure = structure;
    window.currentStructure = structure;

    // Every path that changes the hierarchy — a streamed step, a CRUD edit, a cost
    // override — ends up here, so this is the one place the session autosave has to
    // be told about. It debounces, so streaming does not write on every frame.
    if (window.D2CSessions) window.D2CSessions.noteChange();

    renderResourcePlan(structure && structure.resource_plan ? structure.resource_plan : null);

    if (!structure || !structure.cost_drivers || !structure.cost_drivers.length) {
      if (structure && structure.resource_plan) {
        treeRoot.innerHTML = '<p class="text-muted">Resource plan ready — review and edit it above, then click "Continue to cost drivers".</p>';
      } else {
        treeRoot.innerHTML = '<p class="text-muted">No cost drivers yet. Click "Start estimation" to begin.</p>';
      }
      var gv = document.getElementById('grand-total-value');
      if (gv) gv.textContent = '0 EGP/month';
      updateStepButtons();
      updateStepVerifyBar();
      return;
    }

    let html = '';
    let grandTotal = 0;
    // Parameters the engine costed but would not stand behind. Counted so the total
    // can say how much of itself is provisional — the figures now flow into it, and
    // a total built partly on placeholders must not read as a settled one.
    let provisionalCount = 0;

    // Clear previous justification data
    for (let key in justificationData) {
      delete justificationData[key];
    }

    for (const driver of structure.cost_drivers) {
      const driverId = 'driver-' + slug(driver.cost_driver_name);

      // Store full justification (avoid data-attribute length limits)
      justificationData[driverId] = driver.justification || '';

      html += `
        <div class="tree-node" data-entity="driver" data-depth="0" data-id="${driverId}">
          <div class="entity-box driver has-toggle"
            data-name="${escapeAttr(driver.cost_driver_name)}"
            data-driver-name="${escapeAttr(driver.cost_driver_name)}">
            <button type="button" class="entity-toggle" title="Collapse / expand">&minus;</button>
            <span class="entity-label">Cost driver</span>
            <div class="entity-name">${escapeHtml(driver.cost_driver_name)}</div>
          </div>
          <div class="tree-node-children">
      `;

        for (const comp of driver.cost_components || []) {
        const compId = 'comp-' + slug(driver.cost_driver_name) + '-' + slug(comp.cost_component_name);

        // Store full justification and quantity justification
        justificationData[compId] = {
          justification: comp.justification || '',
          quantity_justification: comp.quantity_justification || ''
        };

        html += `
          <div class="tree-node" data-entity="component" data-depth="1" data-id="${compId}">
            <div class="entity-box component has-toggle"
              data-quantity="${escapeAttr(String(comp.quantity || ''))}"
              data-name="${escapeAttr(comp.cost_component_name)}"
              data-driver-name="${escapeAttr(driver.cost_driver_name)}"
              data-component-name="${escapeAttr(comp.cost_component_name)}">
              <button type="button" class="entity-toggle" title="Collapse / expand">&minus;</button>
              <span class="entity-label">Cost component</span>
              <div class="entity-name">${escapeHtml(comp.cost_component_name)}</div>
            </div>
            <div class="tree-node-children">
        `;

        for (const inp of comp.cost_inputs || []) {
          const monthlyCost = inp.monthly_cost_egp || 0;
          const inputQty = inp.quantity != null ? Number(inp.quantity) : 1;
          const compQty = parseFloat(comp.quantity) || 1;
          const sharing = inp.sharing != null ? Number(inp.sharing) : 1;
          const totalCost = (monthlyCost * inputQty * compQty) / sharing;

          grandTotal += totalCost;

          const inputId = 'inp-' + slug(driver.cost_driver_name) + '-' + slug(comp.cost_component_name) + '-' + slug(inp.cost_input_name);
          const qtyInp = inp.quantity != null ? inp.quantity : '';
          const formulaStr = typeof inp.formula === 'string' && inp.formula.trim() ? inp.formula.trim() : '';
          const costParams = inp.cost_parameters || [];

          // Store full details for detail modal (avoid data-attribute length limits)
          const inpSourceUrls = Array.isArray(inp.source_urls) ? inp.source_urls : (inp.source_url ? [inp.source_url] : []);
          justificationData[inputId] = {
            justification: inp.justification || '',
            quantity_justification: inp.quantity_justification || '',
            cost_justification: inp.cost_justification || '',
            source: inpSourceUrls.length ? inpSourceUrls.join(' | ') : '',
            source_urls: inpSourceUrls,
            confidence: inp.confidence || '',
            formula: formulaStr,
            formula_justification: inp.formula_justification || '',
            unit_warnings: Array.isArray(inp.unit_warnings) ? inp.unit_warnings : [],
            formula_error: inp.formula_error || ''
          };
          const costDisplay = totalCost > 0 ? (totalCost.toLocaleString('en-EG') + ' EGP') : '';

          html += `
            <div class="tree-node" data-entity="input" data-depth="2" data-id="${inputId}">
              <div class="entity-box input has-toggle"
                data-quantity="${inp.quantity != null ? inp.quantity : ''}"
                data-component-quantity="${compQty}"
                data-sharing="${sharing}"
                data-name="${escapeAttr(inp.cost_input_name)}"
                data-driver-name="${escapeAttr(driver.cost_driver_name)}"
                data-component-name="${escapeAttr(comp.cost_component_name)}"
                data-input-name="${escapeAttr(inp.cost_input_name)}"
                data-cost="${monthlyCost}"
                data-total-cost="${totalCost}"
                data-currency="EGP"
                data-formula="${escapeAttr(formulaStr)}">
                <button type="button" class="entity-toggle" title="Collapse / expand">&minus;</button>
                <span class="entity-label">Cost input</span>
                <div class="entity-name">${escapeHtml(inp.cost_input_name)}</div>
                ${costDisplay ? `<div class="entity-meta entity-cost">${escapeHtml(costDisplay)}</div>` : ''}
              </div>
              <div class="tree-node-children tree-node-parameters">
          `;

          for (const p of costParams) {
            const pName = p.parameter_name || '';
            const pJust = p.justification || '';
            const pCost = p.monthly_cost_egp != null ? Number(p.monthly_cost_egp) : null;
            const pCostStr = pCost != null && pCost > 0 ? Number(pCost).toLocaleString('en-EG') + ' EGP' : 'Not estimated yet';
            const hasCost = pCost != null && pCost > 0;
            // A flagged status no longer means an empty cost: the engine publishes a
            // provisional figure (median of disagreeing sources, or the closest
            // comparable item) and the badge says which. The badge must therefore
            // show alongside a number, not only in place of one.
            const pStatus = p.estimate_status || (pCost != null ? 'estimated' : '');
            const pBasis = p.estimate_basis || '';
            const pWarnings = Array.isArray(p.estimate_warnings) ? p.estimate_warnings : [];
            const pEvidence = p.evidence_count != null ? Number(p.evidence_count) : null;
            const pRange = (Array.isArray(p.value_range_egp) && p.value_range_egp.length === 2
              && p.value_range_egp[0] != null && p.value_range_egp[1] != null) ? p.value_range_egp : null;
            const pConfidence = p.confidence && p.confidence !== 'none' ? p.confidence : '';
            // Below 'estimated' the figure came off one of the fallback rungs, and
            // which rung it was matters more to a reviewer than the status word:
            // "closest comparable item" and "the model's own guess" call for very
            // different amounts of checking.
            const statusLabel = pStatus === 'insufficient_evidence'
                ? (!hasCost ? 'No sufficient evidence'
                  : pBasis === 'model_judgement' ? 'Unsourced estimate — no evidence found'
                  : pBasis === 'internal_benchmark' ? 'From internal records only'
                  : 'Weak evidence — closest match')
              : pStatus === 'needs_analyst_input'
                ? (hasCost ? 'Sources disagree — median shown' : 'Sources disagree — needs review')
              : pStatus === 'user_set' ? 'Set by analyst'
              : '';
            const statusClass = pStatus === 'needs_analyst_input' ? 'bg-warning text-dark'
              : pStatus === 'user_set' ? 'bg-info text-dark'
              : pBasis === 'model_judgement' ? 'bg-danger'
              : 'bg-secondary';
            // A value an analyst typed in is a decision, not a placeholder, so it is
            // flagged in the tree but does not count against the total.
            if (hasCost && (pStatus === 'insufficient_evidence' || pStatus === 'needs_analyst_input')) {
              provisionalCount++;
            }
            const confidenceClass = pConfidence === 'high' ? 'bg-success'
              : pConfidence === 'medium' ? 'bg-warning text-dark' : 'bg-secondary';
            const rangeStr = pRange
              ? Number(pRange[0]).toLocaleString('en-EG') + ' – ' + Number(pRange[1]).toLocaleString('en-EG') + ' EGP'
              : '';
            const paramId = inputId + '-param-' + slug(pName);
            const pCostJust = p.cost_justification || '';
            const pSourceUrls = Array.isArray(p.source_urls) ? p.source_urls : (p.source_url ? [p.source_url] : []);
            const internalDbCost = p.cost_from_internal_database || '';
            const sourceDisplay = pSourceUrls.length ? pSourceUrls.join(' | ') : '—';
            justificationData[paramId] = {
              justification: pJust,
              cost: pCost,
              cost_justification: pCostJust,
              source_urls: pSourceUrls,
              source: pSourceUrls.length ? pSourceUrls.join(' | ') : '',
              cost_from_internal_database: internalDbCost,
              unit: p.unit || '',
              confidence: pConfidence,
              estimate_status: pStatus,
              estimate_basis: p.estimate_basis || '',
              estimate_warnings: pWarnings,
              evidence_count: pEvidence,
              value_range: rangeStr
            };
            html += `
              <div class="tree-node" data-entity="parameter" data-depth="3" data-id="${paramId}">
                <div class="entity-box parameter"
                  data-name="${escapeAttr(pName)}"
                  data-driver-name="${escapeAttr(driver.cost_driver_name)}"
                  data-component-name="${escapeAttr(comp.cost_component_name)}"
                  data-input-name="${escapeAttr(inp.cost_input_name)}"
                  data-parameter-name="${escapeAttr(pName)}"
                  data-cost="${pCost != null ? pCost : ''}">
                  <div class="entity-actions">
                    <button type="button" class="btn-entity-action edit btn-param-edit" title="Edit">✎</button>
                    <button type="button" class="btn-entity-action delete btn-param-delete" title="Delete">✕</button>
                  </div>
                  <span class="entity-label">Cost parameter</span>
                  <div class="entity-name">${escapeHtml(pName)}</div>
                  ${hasCost ? `<div class="entity-meta${statusLabel ? ' provisional-cost' : ''}">${escapeHtml(pCostStr)}</div>` : ''}
                  ${(hasCost && rangeStr) ? `<div class="entity-meta text-muted small">range ${escapeHtml(rangeStr)}</div>` : ''}
                  ${statusLabel
                    ? `<div class="entity-meta"><span class="badge ${statusClass}">${escapeHtml(statusLabel)}</span></div>`
                    : (hasCost && pConfidence) ? `<div class="entity-meta"><span class="badge ${confidenceClass}">${escapeHtml(pConfidence)} confidence</span>${pEvidence != null ? ` <span class="text-muted small">${pEvidence} source${pEvidence === 1 ? '' : 's'}</span>` : ''}</div>` : ''}
                </div>
              </div>
            `;
          }

          html += `
          <div class="add-button-container">
            <button type="button" class="btn-add-entity" data-add-type="parameter" data-driver-name="${escapeAttr(driver.cost_driver_name)}" data-component-name="${escapeAttr(comp.cost_component_name)}" data-input-name="${escapeAttr(inp.cost_input_name)}">+ Add cost parameter</button>
          </div>
              </div>
            </div>
          `;
        }

        // Add "Add input" button after component's inputs
        html += `
          <div class="add-button-container">
            <button class="btn-add-entity" data-add-type="input" data-driver-name="${escapeAttr(driver.cost_driver_name)}" data-component-name="${escapeAttr(comp.cost_component_name)}">
              + Add cost input
            </button>
          </div>
        `;

        html += `
            </div>
          </div>
        `;
      }

      // Add "Add component" button after driver's components
      html += `
        <div class="add-button-container">
          <button class="btn-add-entity" data-add-type="component" data-driver-name="${escapeAttr(driver.cost_driver_name)}">
            + Add cost component
          </button>
        </div>
      `;

      html += `
          </div>
        </div>
      `;
    }

    // Add "Add driver" button after all drivers
    html += `
      <div class="add-button-container">
        <button class="btn-add-entity" data-add-type="driver">
          + Add cost driver
        </button>
      </div>
    `;

    treeRoot.innerHTML = html;

    syncDepthButtons();
    applyTreeDepth(currentTreeDepth);

    // Update grand total display
    document.getElementById('grand-total-value').textContent =
      grandTotal > 0 ? `${grandTotal.toLocaleString('en-EG')} EGP/month` : 'Not yet estimated';

    const totalNote = document.getElementById('grand-total-note');
    if (totalNote) {
      if (grandTotal > 0 && provisionalCount > 0) {
        totalNote.textContent = `includes ${provisionalCount} provisional figure${provisionalCount === 1 ? '' : 's'} — review the flagged parameters`;
        totalNote.classList.remove('d-none');
      } else {
        totalNote.textContent = '';
        totalNote.classList.add('d-none');
      }
    }

    updateStepButtons();
    updateStepVerifyBar();

    // Add action buttons to entity boxes (if CRUD module loaded). Skip parameter boxes (they use param-edit/param-delete).
    if (window.addEntityActions) {
      treeRoot.querySelectorAll('.entity-box').forEach(function (box) {
        const entityNode = box.closest('.tree-node');
        const entityType = entityNode.dataset.entity;
        if (entityType === 'parameter') return;
        const driverName = box.dataset.driverName || '';
        const componentName = box.dataset.componentName || '';
        const inputName = box.dataset.inputName || box.dataset.name || '';

        window.addEntityActions(box, entityType, driverName, componentName, inputName);
      });
    }

    // Add event listeners for "Add" buttons
    treeRoot.querySelectorAll('.btn-add-entity').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        const addType = this.dataset.addType;
        const driverName = this.dataset.driverName || null;
        const componentName = this.dataset.componentName || null;
        const inputName = this.dataset.inputName || null;

        if (addType === 'parameter') {
          const name = window.prompt('Parameter name:');
          if (name == null || !name.trim()) return;
          const costStr = window.prompt('Monthly cost (EGP), or leave empty:');
          const cost = costStr != null && costStr.trim() !== '' ? parseFloat(costStr) : null;
          if (window.performEntityOperation) {
            window.performEntityOperation({
              activity_description: currentActivity,
              structure: currentStructure,
              entity_type: 'parameter',
              operation: 'add',
              driver_name: driverName,
              component_name: componentName,
              input_name: inputName,
              new_name: name.trim(),
              new_parameter_cost: cost
            });
          }
          return;
        }

        if (window.openAddModal) {
          window.openAddModal(addType, driverName, componentName);
        }
      });
    });

    // Parameter edit/delete (buttons use btn-entity-action + btn-param-edit/btn-param-delete)
    treeRoot.querySelectorAll('.entity-box.parameter .btn-param-edit').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        const box = this.closest('.entity-box.parameter');
        const driverName = box.dataset.driverName;
        const componentName = box.dataset.componentName;
        const inputName = box.dataset.inputName;
        const parameterName = box.dataset.parameterName || '';
        if (!driverName || !componentName || !inputName || !parameterName) return;
        const newName = window.prompt('Parameter name:', parameterName);
        if (newName == null) return;
        const costStr = window.prompt('Monthly cost (EGP), or leave empty:');
        const cost = costStr != null && costStr.trim() !== '' ? parseFloat(costStr) : null;
        if (window.performEntityOperation) {
          window.performEntityOperation({
            activity_description: currentActivity,
            structure: currentStructure,
            entity_type: 'parameter',
            operation: 'edit',
            driver_name: driverName,
            component_name: componentName,
            input_name: inputName,
            parameter_name: parameterName,
            new_name: newName.trim(),
            new_parameter_cost: cost
          });
        }
      });
    });
    treeRoot.querySelectorAll('.entity-box.parameter .btn-param-delete').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        const box = this.closest('.entity-box.parameter');
        const driverName = box.dataset.driverName;
        const componentName = box.dataset.componentName;
        const inputName = box.dataset.inputName;
        const parameterName = box.dataset.parameterName || '';
        if (!parameterName || !window.confirm('Remove parameter "' + parameterName + '"?')) return;
        if (window.performEntityOperation) {
          window.performEntityOperation({
            activity_description: currentActivity,
            structure: currentStructure,
            entity_type: 'parameter',
            operation: 'delete',
            driver_name: driverName,
            component_name: componentName,
            input_name: inputName,
            parameter_name: parameterName
          });
        }
      });
    });
    // Collapse / expand toggles. Clicking the toggle only folds the subtree; it must
    // not bubble up to the box's detail-modal handler, hence stopPropagation.
    treeRoot.querySelectorAll('.entity-toggle').forEach(function (toggle) {
      toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        var box = this.parentElement;
        var node = box ? box.parentElement : null;
        var children = directChildrenContainer(node);
        if (!children) return;
        if (children.style.display === 'none') {
          children.style.display = 'flex';
          node.classList.remove('collapsed');
          this.textContent = '−';
        } else {
          children.style.display = 'none';
          node.classList.add('collapsed');
          this.textContent = '+';
        }
      });
    });

    // Click handlers for entity detail modal
    treeRoot.querySelectorAll('.entity-box').forEach(function (box) {
      box.addEventListener('click', function () {
        const entityNode = this.closest('.tree-node');
        const entity = entityNode.dataset.entity;
        const entityId = entityNode.dataset.id;
        const name = this.dataset.name || '';
        const quantity = this.dataset.quantity;

        // Retrieve full justification from storage (not data attribute)
        const storedData = justificationData[entityId];

        if (entity === 'driver') {
          const justification = storedData || '';
          detailModalTitle.textContent = 'Cost driver: ' + name;
          detailModalBody.innerHTML = `
            <div class="detail-row">
              <dt>Justification</dt>
              <dd>${escapeHtml(justification)}</dd>
            </div>
          `;

        } else if (entity === 'component') {
          const justification = (typeof storedData === 'object' && storedData !== null)
            ? (storedData.justification || '')
            : (storedData || '');
          const qtyJust = (typeof storedData === 'object' && storedData !== null)
            ? (storedData.quantity_justification || '')
            : '';

          detailModalTitle.textContent = 'Cost component: ' + name;
          detailModalBody.innerHTML = `
            <div class="detail-row">
              <dt>Justification</dt>
              <dd>${escapeHtml(justification) || '—'}</dd>
            </div>
          `;

        } else if (entity === 'input') {
          const cost = this.dataset.cost || '0';
          const totalCost = this.dataset.totalCost || '0';
          const compQty = this.dataset.componentQuantity || '1';
          const sharing = this.dataset.sharing || '1';
          const currency = this.dataset.currency || 'EGP';
          const driverName = this.dataset.driverName || '';
          const componentName = this.dataset.componentName || '';
          const inputName = this.dataset.inputName || '';

          const justification = storedData?.justification || '';
          const qtyJust = storedData?.quantity_justification || '';
          const costJust = storedData?.cost_justification || '';
          const source = storedData?.source || '';
          const confidence = storedData?.confidence || '';
          const formulaValue = (storedData && storedData.formula) ? storedData.formula : '';
          const formulaJustification = (storedData && storedData.formula_justification) ? storedData.formula_justification : '';

          const costDisplay = Number(cost) > 0
            ? `${Number(cost).toLocaleString('en-EG')} ${currency}/month`
            : 'Not yet estimated';
          const totalDisplay = Number(totalCost) > 0
            ? `${Number(totalCost).toLocaleString('en-EG')} ${currency}/month`
            : 'Not yet calculated';

          detailModalTitle.textContent = 'Cost input: ' + name;
          detailModalBody.innerHTML = `
            <div class="detail-row">
              <dt>Justification</dt>
              <dd>${escapeHtml(justification) || '—'}</dd>
            </div>
            <div class="detail-row">
              <dt>Sharing</dt>
              <dd>${escapeHtml(String(sharing))}</dd>
            </div>
            <div class="detail-row">
              <dt>Unit Cost (monthly)</dt>
              <dd>${escapeHtml(costDisplay)}</dd>
            </div>
            <div class="detail-row">
              <dt><strong>Total Cost (monthly)</strong></dt>
              <dd><strong>${escapeHtml(totalDisplay)}</strong></dd>
            </div>
            <div class="detail-row">
              <dt>Currency</dt>
              <dd>${escapeHtml(currency)}</dd>
            </div>
            <div class="detail-row">
              <dt>Formula</dt>
              <dd>
                <textarea id="detailFormulaInput" class="form-control font-monospace" rows="2" placeholder="e.g. 3*2*([Net Salary]+[Medical Insurance])+[Mobile Allowance]">${escapeHtml(formulaValue)}</textarea>
                <textarea id="detailFormulaJustInput" class="form-control mt-2" rows="3" placeholder="Explain the numeric literals in the formula (e.g. 3 engineers for 2 months; mobile allowance paid once).">${escapeHtml(formulaJustification)}</textarea>
                <button type="button" class="btn btn-vodafone btn-sm mt-2" id="btnUpdateFormula" data-driver-name="${escapeAttr(driverName)}" data-component-name="${escapeAttr(componentName)}" data-input-name="${escapeAttr(inputName)}">Update formula</button>
              </dd>
            </div>
            <div class="detail-row">
              <dt>Formula justification</dt>
              <dd>${formulaJustification ? escapeHtml(formulaJustification) : '—'}</dd>
            </div>
            ${(storedData && storedData.unit_warnings && storedData.unit_warnings.length) ? `
            <div class="detail-row">
              <dt class="text-warning">Unit consistency warnings</dt>
              <dd><div class="alert alert-warning small mb-0"><ul class="mb-0">${storedData.unit_warnings.map(function(w){ return '<li>' + escapeHtml(w) + '</li>'; }).join('')}</ul></div></dd>
            </div>` : ''}
            ${(storedData && storedData.formula_error) ? `
            <div class="detail-row">
              <dt class="text-warning">Not costed</dt>
              <dd><div class="alert alert-secondary small mb-0">${escapeHtml(storedData.formula_error)}</div></dd>
            </div>` : ''}
            <div class="detail-row">
              <dt>Cost justification</dt>
              <dd>${escapeHtml(costJust)}</dd>
            </div>
            <div class="detail-row">
              <dt>Source</dt>
              <dd>${source ? '<a href="' + escapeAttr(source) + '" target="_blank" rel="noopener">' + escapeHtml(source) + '</a>' : '—'}</dd>
            </div>
            ${confidence ? `<div class="detail-row"><dt>Confidence</dt><dd>${escapeHtml(confidence)}</dd></div>` : ''}
          `;
          var btnUpdateFormula = detailModalBody.querySelector('#btnUpdateFormula');
          var formulaInput = detailModalBody.querySelector('#detailFormulaInput');
          var formulaJustInput = detailModalBody.querySelector('#detailFormulaJustInput');
          if (btnUpdateFormula && formulaInput) {
            btnUpdateFormula.addEventListener('click', function() {
              var d = this.dataset.driverName;
              var c = this.dataset.componentName;
              var i = this.dataset.inputName;
              if (window.performEntityOperation) {
                window.performEntityOperation({
                  activity_description: currentActivity,
                  structure: currentStructure,
                  entity_type: 'input',
                  operation: 'update_formula',
                  driver_name: d,
                  component_name: c,
                  input_name: i,
                  new_formula: formulaInput.value.trim(),
                  new_formula_justification: formulaJustInput ? formulaJustInput.value.trim() : undefined
                });
              }
              detailModal.hide();
            });
          }
        } else if (entity === 'parameter') {
          const paramName = this.dataset.parameterName || this.dataset.name || '';
          const stored = justificationData[entityId] || {};
          const costVal = this.dataset.cost !== undefined && this.dataset.cost !== '' ? Number(this.dataset.cost) : (stored.cost != null ? stored.cost : null);
          const costStr = costVal != null && costVal > 0 ? Number(costVal).toLocaleString('en-EG') + ' EGP' : 'Not estimated';
          const costJust = stored.cost_justification || '—';
          const urls = stored.source_urls && stored.source_urls.length ? stored.source_urls : (stored.source ? [stored.source] : []);
          const sourceUrlsHtml = urls.length ? urls.map(function(u){ return '<a href="' + escapeAttr(u) + '" target="_blank" rel="noopener">' + escapeHtml(u) + '</a>'; }).join('<br>') : '—';
          const internalDbCost = stored.cost_from_internal_database || 'Nothing found';
          const paramDriver = this.dataset.driverName || '';
          const paramComponent = this.dataset.componentName || '';
          const paramInput = this.dataset.inputName || '';
          const statusNote = stored.estimate_status === 'insufficient_evidence'
            ? (costVal == null
              ? 'No number could be produced: the search turned up nothing usable at all. Enter a value below if you have one.'
              : stored.estimate_basis === 'model_judgement'
              ? 'No price for this item was found on the web or in internal records. The figure shown has NO source behind it — it is the model\'s own benchmark, offered only as a starting point. Replace it with a real quote before this estimate is used.'
              : stored.estimate_basis === 'internal_benchmark'
              ? 'No usable web evidence was found. The figure shown comes from internal historical records for comparable work, and its unit basis could not be checked against the pricing unit above — verify before use.'
              : 'The search did not turn up enough qualifying evidence to estimate this. The figure shown is a placeholder taken from the closest comparable item found — confirm or replace it below.')
            : stored.estimate_status === 'needs_analyst_input'
            ? 'Sources disagreed too widely to settle on a figure. The median of those sources is shown as a provisional value — review the range and evidence below, then correct it if needed.'
            : stored.estimate_status === 'user_set'
            ? 'This value was set manually and is not the engine estimate. Re-running the costs step will overwrite it.'
            : '';
          const statusAlertClass = stored.estimate_basis === 'model_judgement' ? 'alert-danger'
            : stored.estimate_status === 'needs_analyst_input' ? 'alert-warning'
            : stored.estimate_status === 'user_set' ? 'alert-info'
            : 'alert-secondary';

          detailModalTitle.textContent = 'Cost parameter: ' + paramName;
          detailModalBody.innerHTML = `
            <div class="detail-row">
              <dt>Name</dt>
              <dd>${escapeHtml(paramName)}</dd>
            </div>
            <div class="detail-row">
              <dt>Pricing unit</dt>
              <dd>${stored.unit ? escapeHtml(stored.unit) : 'EGP per month'}</dd>
            </div>
            <div class="detail-row">
              <dt>Justification</dt>
              <dd>${escapeHtml(stored.justification || '—')}</dd>
            </div>
            <div class="detail-row">
              <dt>Cost${stored.unit ? ' (in pricing unit)' : ''}</dt>
              <dd>
                <div class="input-group input-group-sm" style="max-width: 340px;">
                  <input type="number" step="any" min="0" class="form-control" id="detailParamCostInput"
                    value="${costVal != null ? escapeAttr(String(costVal)) : ''}" placeholder="Not estimated">
                  <span class="input-group-text">${escapeHtml(stored.unit || 'EGP per month')}</span>
                  <button type="button" class="btn btn-vodafone" id="btnUpdateParamCost">Save</button>
                </div>
                <div class="form-text">Enter the price in the pricing unit above — the cost input's formula converts it to a monthly figure. Leave empty and save to clear the value.</div>
              </dd>
            </div>
            ${statusNote ? `
            <div class="detail-row">
              <dt>Status</dt>
              <dd><div class="alert ${statusAlertClass} small mb-0">${escapeHtml(statusNote)}</div></dd>
            </div>` : ''}
            ${stored.confidence ? `
            <div class="detail-row">
              <dt>Confidence</dt>
              <dd>${escapeHtml(stored.confidence)}${stored.evidence_count != null ? ' <span class="text-muted small">from ' + escapeHtml(String(stored.evidence_count)) + ' qualifying source(s)</span>' : ''}</dd>
            </div>` : ''}
            ${stored.value_range ? `
            <div class="detail-row">
              <dt>Range across sources</dt>
              <dd>${escapeHtml(stored.value_range)}</dd>
            </div>` : ''}
            <div class="detail-row">
              <dt>Cost justification</dt>
              <dd class="scrollable-content">${markdownToHtml(costJust)}</dd>
            </div>
            ${(stored.estimate_warnings && stored.estimate_warnings.length) ? `
            <div class="detail-row">
              <dt class="text-warning">Evidence caveats</dt>
              <dd><div class="alert alert-warning small mb-0"><ul class="mb-0">${stored.estimate_warnings.map(function(w){ return '<li>' + escapeHtml(w) + '</li>'; }).join('')}</ul></div></dd>
            </div>` : ''}
            <div class="detail-row">
              <dt>Cost from internal database</dt>
              <dd>${escapeHtml(internalDbCost)}</dd>
            </div>
            <div class="detail-row">
              <dt>Source URLs</dt>
              <dd>${sourceUrlsHtml}</dd>
            </div>
          `;

          // Saving a cost here overrides the engine. The backend records who set it
          // and re-evaluates the parent input's formula, so the tree and the grand
          // total move with the edit instead of drifting away from it.
          var btnUpdateParamCost = detailModalBody.querySelector('#btnUpdateParamCost');
          var paramCostInput = detailModalBody.querySelector('#detailParamCostInput');
          if (btnUpdateParamCost && paramCostInput) {
            btnUpdateParamCost.addEventListener('click', function () {
              var raw = paramCostInput.value.trim();
              var value = null;
              if (raw !== '') {
                value = Number(raw);
                if (!isFinite(value) || value < 0) {
                  window.alert('Enter a non-negative number, or leave the field empty to clear the value.');
                  return;
                }
              }
              if (!paramDriver || !paramComponent || !paramInput || !paramName) return;
              if (window.performEntityOperation) {
                window.performEntityOperation({
                  activity_description: currentActivity,
                  structure: currentStructure,
                  entity_type: 'parameter',
                  operation: 'update_cost',
                  driver_name: paramDriver,
                  component_name: paramComponent,
                  input_name: paramInput,
                  parameter_name: paramName,
                  new_parameter_cost: value
                });
              }
              detailModal.hide();
            });
          }
        }

        detailModal.show();
      });
    });
  }

  function slug(s) {
    return String(s).replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-_]/g, '').toLowerCase() || 'n';
  }

  function escapeHtml(s) {
    if (s == null) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function escapeAttr(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // Configure marked.js for proper markdown rendering
  (function initMarked() {
    if (typeof marked !== 'undefined') {
      marked.use({
        gfm: true,
        breaks: true,
        renderer: {
          link: function (args) {
            var href, title, text;
            if (typeof args === 'object' && args !== null) {
              href = args.href || '';
              title = args.title || '';
              text = args.text || '';
            } else {
              href = arguments[0] || '';
              title = arguments[1] || '';
              text = arguments[2] || '';
            }
            var titleAttr = title ? ' title="' + title + '"' : '';
            return '<a href="' + href + '" target="_blank" rel="noopener"' + titleAttr + '>' + text + '</a>';
          },
          table: function (args) {
            var headerContent = '', bodyContent = '';
            if (typeof args === 'object' && args !== null && args.header) {
              headerContent = args.header;
              bodyContent = args.body || '';
            } else {
              headerContent = arguments[0] || '';
              bodyContent = arguments[1] || '';
            }
            return '<div class="table-responsive"><table class="table table-sm table-bordered">' +
              '<thead>' + headerContent + '</thead>' +
              '<tbody>' + bodyContent + '</tbody>' +
              '</table></div>';
          }
        }
      });
    }
  })();

  function markdownToHtml(text) {
    if (!text) return '—';

    let processedText = String(text);

    // Pre-process: Convert LaTeX math blocks \[...\] into display divs
    processedText = processedText.replace(/\\\[([\s\S]*?)\\\]/g, function (match, mathContent) {
      return '\n<div class="math-block">' + escapeHtml(mathContent.trim()) + '</div>\n';
    });

    // Pre-process: Convert inline LaTeX \(...\) into styled spans
    processedText = processedText.replace(/\\\(([\s\S]*?)\\\)/g, function (match, mathContent) {
      return '<span class="math-inline">' + escapeHtml(mathContent.trim()) + '</span>';
    });

    let html;
    if (typeof marked !== 'undefined') {
      html = marked.parse(processedText);
    } else {
      html = escapeHtml(processedText)
        .replace(/\*\*([^\*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\*([^\*]+)\*/g, '<em>$1</em>')
        .replace(/\[([^\]]+)\]\(([^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
        .replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
      html = '<p>' + html + '</p>';
    }

    // Sanitize with DOMPurify if available
    if (typeof DOMPurify !== 'undefined') {
      html = DOMPurify.sanitize(html, {
        ADD_TAGS: ['div', 'span'],
        ADD_ATTR: ['target', 'rel', 'class'],
        ALLOW_DATA_ATTR: false
      });
    }

    // Post-process: Legacy format "Source Name (URL)" to clickable links
    html = html.replace(/([A-Za-z0-9][A-Za-z0-9\s\-\.]{2,}?)\s*\((https?:\/\/[^\s\)]+)\)/g, function (match, sourceName, url) {
      if (match.indexOf('href=') !== -1 || match.indexOf('<a') !== -1) return match;
      return '<a href="' + url.trim() + '" target="_blank" rel="noopener">' + sourceName.trim() + '</a>';
    });

    return html;
  }

  // Export functions for CRUD module
  window.buildTree = buildTree;
  window.updateStepButtons = updateStepButtons;
  window.escapeHtml = escapeHtml;
  window.escapeAttr = escapeAttr;

  /* ------------------------------------------------------------------
     Session bridge. sessions.js persists the estimation and owns the
     sidebar list; these three functions are the whole of what it may
     touch in here.
     ------------------------------------------------------------------ */

  /** What the autosave writes: everything needed to rebuild this screen later. */
  window.getEstimationState = function () {
    return {
      structure: currentStructure,
      activity_description: currentActivity,
      activity_facts: currentActivityFacts,
      step_completed: currentStepCompleted
    };
  };

  /** Restore a saved session into the estimation view. */
  window.loadSessionState = function (session) {
    if (!session) return;
    currentActivity = session.activity_description || '';
    window.currentActivity = currentActivity;
    currentActivityFacts = session.activity_facts || null;
    currentStepCompleted = session.step_completed || null;
    if (activityDescription) activityDescription.value = currentActivity;

    if (session.structure) {
      if (treeSection) treeSection.classList.remove('d-none');
      buildTree(session.structure);
      setStatus('Opened “' + (session.title || 'session') + '”.');
    } else {
      // Created but never got past the description — show the input, not an empty tree.
      currentStructure = null;
      window.currentStructure = null;
      if (treeSection) treeSection.classList.add('d-none');
      if (resourcePlanSection) resourcePlanSection.classList.add('d-none');
      if (treeRoot) treeRoot.innerHTML = '';
      setStatus('Opened “' + (session.title || 'session') + '”. Click “Start estimation” to run it.');
    }
    updateStepVerifyBar();
    updateStepButtons();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  /** Clear the view back to a blank estimation (New session, or the open one deleted). */
  window.resetEstimationState = function () {
    currentStructure = null;
    window.currentStructure = null;
    currentActivity = '';
    window.currentActivity = '';
    currentActivityFacts = null;
    currentStepCompleted = null;
    if (activityDescription) { activityDescription.value = ''; activityDescription.focus(); }
    if (treeSection) treeSection.classList.add('d-none');
    if (resourcePlanSection) resourcePlanSection.classList.add('d-none');
    if (treeRoot) treeRoot.innerHTML = '';
    if (stepVerifyBar) stepVerifyBar.classList.add('d-none');
    setStatus('');
    updateStepButtons();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // Called by the AI Assistant tab once it finishes drafting a description.
  // Keeps everything on one page — no redirect — and carries the structured
  // facts (volume, crews, shared pools) straight into the plan step.
  window.loadDescriptionIntoEstimation = function (desc, facts) {
    if (activityDescription) activityDescription.value = desc || '';
    currentActivity = desc || '';
    window.currentActivity = currentActivity;
    if (facts) currentActivityFacts = facts;
    setStatus('✓ Description loaded from the AI Assistant. Review it, then click “Start estimation”.');
    if (activityDescription) activityDescription.focus();
  };

  function runStep(step) {
    var text = (activityDescription.value || '').trim();
    if (!text) {
      setStatus('Please enter an activity description.', true);
      return;
    }
    currentActivity = text;
    window.currentActivity = text;

    if (step === 'plan') {
      currentStepCompleted = null;
      // Submitting a description starts a session for it. Re-running the plan on the
      // same text stays in the session that is already open rather than duplicating it.
      if (window.D2CSessions) window.D2CSessions.startEstimation(text, currentActivityFacts);
    }
    setStatus(step === 'plan' ? 'Deriving the resourcing & scaling plan…'
      : step === 'drivers' ? 'Identifying cost drivers…' : 'Running ' + step + ' step…');
    if (btnRun) btnRun.disabled = true;
    if (btnContinueNext) btnContinueNext.disabled = true;
    if (treeSection) treeSection.classList.remove('d-none');
    // Keep the already-estimated layer on screen while the next one is computed.
    // Only show a "Loading…" placeholder when there is no tree yet; otherwise
    // leave the current tree visible and show a non-destructive progress state.
    var hasExistingTree = currentStructure && currentStructure.cost_drivers && currentStructure.cost_drivers.length;
    if (hasExistingTree) {
      if (treeContainer) treeContainer.classList.add('loading');
    } else if (treeRoot) {
      treeRoot.innerHTML = '<p class="text-muted">Loading…</p>';
    }
    updateStepVerifyBar();

    var body = { activity_description: text, step: step };
    if (step === 'plan') {
      if (currentActivityFacts) body.activity_facts = currentActivityFacts;
    } else if (step === 'drivers') {
      // Pass the structure so the resource plan (and user edits to it) reach the agents.
      if (currentStructure) body.structure = currentStructure;
    } else if (step === 'components' || step === 'inputs' || step === 'parameters' || step === 'costs') {
      if (!currentStructure || !currentStructure.cost_drivers || !currentStructure.cost_drivers.length) {
        setStatus('No structure to continue from. Run "Start estimation" first.', true);
        if (btnRun) btnRun.disabled = false;
        if (btnContinueNext) btnContinueNext.disabled = false;
        return;
      }
      body.structure = currentStructure;
    }

    fetch('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify(body)
    })
      .then(function (response) {
        if (!response.ok) {
          return response.text().then(function (errBody) {
            var msg = 'Request failed (' + response.status + ')';
            try {
              var j = JSON.parse(errBody);
              if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
            } catch (e) {}
            throw new Error(msg);
          });
        }
        return response.body.getReader();
      })
      .then(function (reader) {
        var decoder = new TextDecoder();
        var buffer = '';
        var receivedDoneOrError = false;

        function processBuffer() {
          var lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.indexOf('data: ') !== 0) continue;
            try {
              var data = JSON.parse(line.slice(6));
              if (data.event === 'started') {
                setStatus('Started. This may take a minute…');
              } else if (data.event === 'progress' && data.message) {
                setStatus(data.message);
              } else if (data.event === 'structure' && data.structure) {
                try {
                  buildTree(data.structure);
                } catch (e) {
                  console.error('buildTree error:', e);
                  setStatus('Error displaying structure: ' + (e.message || 'Unknown'), true);
                }
              } else if (data.event === 'done') {
                receivedDoneOrError = true;
                currentStepCompleted = step;
                if (data.structure) {
                  try {
                    buildTree(data.structure);
                  } catch (e) {
                    console.error('buildTree error:', e);
                    setStatus('Error displaying structure: ' + (e.message || 'Unknown'), true);
                  }
                }
                setStatus('Done.');
                updateStepButtons();
                updateStepVerifyBar();
                if (btnContinueNext) btnContinueNext.disabled = false;
              } else if (data.event === 'error') {
                receivedDoneOrError = true;
                setStatus('Error: ' + (data.message || 'Unknown'), true);
                updateStepButtons();
                if (btnContinueNext) btnContinueNext.disabled = false;
              } else if (data.event === 'timeout') {
                receivedDoneOrError = true;
                setStatus('Request timed out.', true);
                updateStepButtons();
                if (btnContinueNext) btnContinueNext.disabled = false;
              }
            } catch (e) {
              console.warn('SSE parse error:', e);
            }
          }
        }

        function read() {
          return reader.read().then(function (result) {
            if (result.done) {
              processBuffer();
              if (!receivedDoneOrError) {
                if (!window.currentStructure || !window.currentStructure.cost_drivers || !window.currentStructure.cost_drivers.length) {
                  setStatus('Connection ended with no results. The server may have encountered an error — check the terminal where the app is running.', true);
                  if (treeRoot) treeRoot.innerHTML = '<p class="text-danger">No data received. Check the server terminal for errors and try again.</p>';
                } else {
                  setStatus('Done.');
                }
              }
              updateStepButtons();
              if (btnContinueNext) btnContinueNext.disabled = false;
              return;
            }
            buffer += decoder.decode(result.value, { stream: true });
            setStatus('Updating structure…');
            processBuffer();
            return read();
          });
        }

        return read();
      })
      .catch(function (err) {
        setStatus('Error: ' + (err.message || 'Request failed'), true);
        updateStepButtons();
        if (btnContinueNext) btnContinueNext.disabled = false;
      });
  }

  if (btnRun) {
    btnRun.addEventListener('click', function () { runStep('plan'); });
  }

  // Segmented depth toggle — applies immediately on click (no separate Apply).
  if (depthToggle) {
    depthToggle.querySelectorAll('button[data-depth]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var val = parseInt(this.getAttribute('data-depth'), 10);
        if (isNaN(val)) return;
        currentTreeDepth = val;
        syncDepthButtons();
        applyTreeDepth(currentTreeDepth);
      });
    });
  }

  // Initial button state
  updateStepButtons();

  // Check if there's a stored description from chat
  var storedDescription = localStorage.getItem('activity_description');
  if (storedDescription) {
    activityDescription.value = storedDescription;
    localStorage.removeItem('activity_description');
    setStatus('✓ Activity description loaded from AI Assistant. Review and click "Start estimation" when ready.');
    activityDescription.focus();
  }
  // Structured facts from chat (volume, crews, shared pools) — consumed by the plan step
  var storedFacts = localStorage.getItem('activity_facts');
  if (storedFacts) {
    try {
      currentActivityFacts = JSON.parse(storedFacts);
    } catch (e) {
      currentActivityFacts = null;
    }
    localStorage.removeItem('activity_facts');
  }
})();