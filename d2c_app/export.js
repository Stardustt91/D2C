// Excel Export – same columns and row logic as workflow_test.py CSV

(function() {
  const btnExport = document.getElementById('btn-export');
  const exportWrap = document.getElementById('export-wrap');
  const exportMenu = document.getElementById('export-menu');
  const exportModal = new bootstrap.Modal(document.getElementById('exportModal'));
  const activityNameInput = document.getElementById('activityNameInput');
  const btnConfirmExport = document.getElementById('btnConfirmExport');
  const exportFormatLabel = document.getElementById('exportFormatLabel');

  // Which format the user picked from the dropdown; consumed on confirm.
  let pendingFormat = 'excel';
  const FORMAT_LABELS = { excel: 'as Excel', pptx: 'as PowerPoint', db: 'to Database' };

  // Same column order as workflow_test.py CSV_COLUMNS
  const CSV_COLUMNS = [
    'cost_driver',
    'cost_driver_justification',
    'cost_component',
    'cost_component_justification',
    'cost_component_quantity',
    'cost_input',
    'cost_input_justification',
    'cost_input_quantity',
    'cost_parameter',
    'cost_parameter_justification',
    'formula',
    'cost',
    'cost_status',
    'cost_justification',
    'cost_source'
  ];

  /**
   * How much weight the exported figure can bear.
   *
   * The engine now publishes a number even when it is not confident — a median of
   * disagreeing sources, or the price of the closest comparable item. Once the
   * workbook leaves the app that caveat exists nowhere else, so it travels in its
   * own column rather than only inside the justification prose.
   */
  function fmtStatus(status, basis, hasCost) {
    switch (status) {
      case 'needs_analyst_input':
        return hasCost ? 'PROVISIONAL - sources disagree, median shown' : 'Not estimated - sources disagree';
      case 'insufficient_evidence':
        if (!hasCost) return 'Not estimated - no usable evidence';
        if (basis === 'model_judgement') return 'UNSOURCED - model estimate, no evidence found';
        if (basis === 'internal_benchmark') return 'PROVISIONAL - internal historical records only';
        return 'PROVISIONAL - closest comparable item';
      case 'user_set':
        return 'Set by analyst';
      default:
        return '';
    }
  }

  function fmtCost(val) {
    if (val == null) return '';
    try {
      const n = parseFloat(val);
      if (isNaN(n)) return '';
      return n > 0 ? n.toLocaleString('en-EG', { maximumFractionDigits: 0 }) + ' EGP' : '';
    } catch (e) {
      return '';
    }
  }

  function fmtSource(obj) {
    if (obj == null) return '';
    if (Array.isArray(obj)) {
      return obj.filter(Boolean).map(function(x) { return String(x).trim(); }).join(' | ');
    }
    const s = String(obj).trim();
    return s || '';
  }

  /**
   * Is there anything to write?
   *
   * The old check was `!structure.cost_drivers`, and an empty array is truthy — so a
   * structure holding only a resource plan passed it and produced a workbook whose
   * cost sheet was nothing but headers, with no warning.
   */
  function hasCostStructure() {
    const s = window.currentStructure;
    return !!(s && Array.isArray(s.cost_drivers) && s.cost_drivers.length);
  }

  /** A row with blanks for whatever the given values leave out. */
  function makeRow(values) {
    const row = [];
    for (let i = 0; i < CSV_COLUMNS.length; i++) {
      const v = values[i];
      row.push(v == null ? '' : String(v));
    }
    return row;
  }

  /**
   * Build rows in the same way as workflow_test.py: one row per parameter, or one per
   * input where an input has no parameters.
   *
   * A level that has not been generated yet still gets a row of its own. Rows used to
   * come only from the innermost loop, so a workbook exported before the inputs step —
   * or containing a driver whose components failed to expand — came out as nothing but
   * headers, with no indication that the drivers on screen had been dropped.
   */
  function buildRows(structure) {
    const rows = [];
    const drivers = structure.cost_drivers || [];

    for (let di = 0; di < drivers.length; di++) {
      const dr = drivers[di];
      const dname = dr.cost_driver_name || '';
      const dJust = dr.justification || '';
      const components = dr.cost_components || [];

      if (!components.length) {
        rows.push(makeRow([dname, dJust]));
        continue;
      }

      for (let ci = 0; ci < components.length; ci++) {
        const comp = components[ci];
        const cname = comp.cost_component_name || '';
        const cJust = comp.justification || '';
        const cQty = comp.quantity;
        const cQtyStr = cQty != null ? String(cQty) : '';
        const inputs = comp.cost_inputs || [];

        if (!inputs.length) {
          rows.push(makeRow([dname, dJust, cname, cJust, cQtyStr]));
          continue;
        }

        for (let ii = 0; ii < inputs.length; ii++) {
          const inp = inputs[ii];
          const iname = inp.cost_input_name || '';
          const iJust = inp.justification || '';
          const iQty = inp.quantity;
          const iQtyStr = iQty != null ? String(iQty) : '';
          const formula = inp.formula || '';
          const params = Array.isArray(inp.cost_parameters) ? inp.cost_parameters : [];

          const inpMonthly = inp.monthly_cost_egp;
          const inpCostStr = fmtCost(inpMonthly);
          const inpCostJust = String(inp.cost_justification || inp.justification || '').trim();
          const inpSource = fmtSource(inp.source_urls || inp.source_url);

          if (params.length > 0) {
            for (let pi = 0; pi < params.length; pi++) {
              const p = params[pi];
              const pname = String(p.parameter_name || '');
              const pJust = String(p.justification || '');
              const pMonthly = p.monthly_cost_egp;
              const pCostStr = fmtCost(pMonthly);
              const pCostJust = String(p.cost_justification || p.justification || '').trim();
              const pSource = fmtSource(p.source_urls || p.source_url);
              rows.push([
                String(dname),
                String(dJust),
                String(cname),
                String(cJust),
                String(cQtyStr),
                String(iname),
                String(iJust),
                String(iQtyStr),
                pname,
                pJust,
                String(formula),
                String(pCostStr),
                fmtStatus(p.estimate_status, p.estimate_basis, pMonthly != null && Number(pMonthly) > 0),
                String(pCostJust),
                String(pSource)
              ]);
            }
          } else {
            rows.push([
              String(dname),
              String(dJust),
              String(cname),
              String(cJust),
              String(cQtyStr),
              String(iname),
              String(iJust),
              String(iQtyStr),
              '',
              '',
              String(formula),
              String(inpCostStr),
              fmtStatus(inp.estimate_status, inp.estimate_basis, inpMonthly != null && Number(inpMonthly) > 0),
              String(inpCostJust),
              String(inpSource)
            ]);
          }
        }
      }
    }

    return rows;
  }

  // Resource-plan columns (all fields of a planned resource).
  const RESOURCE_COLUMNS = [
    'Resource',
    'Scaling class',
    'Quantity',
    'Unit',
    'Confidence',
    'Source',
    'Justification',
    'Derivation'
  ];

  /**
   * Build the "Resources" sheet as an array-of-arrays: a plan-level summary block
   * (volume driver, duration, crews, throughput) on top, then the full resource
   * table, then assumptions and review notes. Returns { data, headerRowIndex } so
   * the caller can style the table header row.
   */
  function buildResourceSheetData(plan) {
    const rows = [];
    const vd = plan.volume_driver || {};
    const crews = plan.crews || {};
    const thr = plan.throughput || {};

    rows.push(['Resource Plan']);
    rows.push([]);
    rows.push(['Volume driver', String(vd.name || ''), vd.value != null ? vd.value : '', String(vd.unit || '')]);
    if (vd.derivation) rows.push(['Volume driver basis', String(vd.derivation)]);
    rows.push(['Duration (months)', plan.duration_months != null ? plan.duration_months : '']);
    rows.push(['Working days / month', plan.working_days_per_month != null ? plan.working_days_per_month : '']);
    rows.push(['Parallel crews', crews.count != null ? crews.count : '']);
    if (crews.composition) rows.push(['Crew composition', String(crews.composition)]);
    if (crews.derivation) rows.push(['Crews basis', String(crews.derivation)]);
    if (thr.value != null || thr.unit) rows.push(['Throughput', thr.value != null ? thr.value : '', String(thr.unit || '')]);
    if (thr.derivation) rows.push(['Throughput basis', String(thr.derivation)]);
    rows.push([]);

    const headerRowIndex = rows.length;
    rows.push(RESOURCE_COLUMNS.slice());

    const resources = Array.isArray(plan.resources) ? plan.resources : [];
    for (let i = 0; i < resources.length; i++) {
      const r = resources[i] || {};
      rows.push([
        String(r.resource_name || ''),
        String(r.scaling_class || ''),
        r.quantity != null ? r.quantity : '',
        String(r.unit || ''),
        String(r.confidence || ''),
        String(r.source || ''),
        String(r.justification || ''),
        String(r.derivation || '')
      ]);
    }

    const assumptions = Array.isArray(plan.assumptions) ? plan.assumptions : [];
    if (assumptions.length) {
      rows.push([]);
      rows.push(['Assumptions']);
      for (let a = 0; a < assumptions.length; a++) rows.push([String(assumptions[a] || '')]);
    }

    const reviewNotes = Array.isArray(plan.review_notes) ? plan.review_notes : [];
    if (reviewNotes.length) {
      rows.push([]);
      rows.push(['Review notes']);
      for (let n = 0; n < reviewNotes.length; n++) rows.push([String(reviewNotes[n] || '')]);
    }

    return { data: rows, headerRowIndex: headerRowIndex };
  }

  /* -------- Dropdown menu (single Export button) -------- */
  function closeMenu() {
    if (!exportWrap) return;
    exportWrap.classList.remove('open');
    if (exportMenu) exportMenu.hidden = true;
    if (btnExport) btnExport.setAttribute('aria-expanded', 'false');
  }
  function openMenu() {
    if (!exportWrap) return;
    exportWrap.classList.add('open');
    if (exportMenu) exportMenu.hidden = false;
    if (btnExport) btnExport.setAttribute('aria-expanded', 'true');
  }

  if (btnExport) {
    btnExport.addEventListener('click', function (e) {
      e.stopPropagation();
      exportWrap.classList.contains('open') ? closeMenu() : openMenu();
    });
  }
  document.addEventListener('click', function (e) {
    if (exportWrap && !exportWrap.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMenu(); });

  if (exportMenu) {
    exportMenu.querySelectorAll('.export-item').forEach(function (item) {
      item.addEventListener('click', function () {
        closeMenu();
        openExportModal(item.dataset.format || 'excel');
      });
    });
  }

  /**
   * What the name field is pre-filled with, still fully editable.
   *
   * The open session's title first: it is what the analyst named this work and what
   * they identify it by in the sidebar, so the file lands with a name they recognise.
   * Falling back to a 50-character slice of the description only produces a name when
   * there is no session — it truncates mid-sentence, which is why it is second.
   */
  function defaultExportName() {
    const title = window.D2CSessions && typeof window.D2CSessions.activeTitle === 'function'
      ? window.D2CSessions.activeTitle()
      : '';
    if (title) return title;
    const descEl = document.getElementById('activity-description');
    const desc = descEl ? descEl.value.trim() : '';
    if (desc) return desc.length <= 50 ? desc : desc.substring(0, 50);
    return 'Cost_Estimation';
  }

  /** Filename-safe form. Runs of punctuation collapse to one underscore, so
   *  "Civil Works, Site Construction" becomes Civil_Works_Site_Construction. */
  function sanitizeFilename(name) {
    const cleaned = String(name || '').replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '');
    return cleaned || 'Cost_Estimation';
  }

  function openExportModal(format) {
    pendingFormat = format || 'excel';
    if (exportFormatLabel) {
      exportFormatLabel.textContent = FORMAT_LABELS[pendingFormat] || '';
      exportFormatLabel.style.display = 'inline-block';
    }
    activityNameInput.value = defaultExportName();
    exportModal.show();
    setTimeout(function () { activityNameInput.focus(); activityNameInput.select(); }, 300);
  }

  function runPendingExport() {
    const activityName = activityNameInput.value.trim() || 'Cost_Estimation';
    exportModal.hide();
    if (pendingFormat === 'pptx') {
      exportToPowerPoint(activityName);
    } else if (pendingFormat === 'db') {
      exportToDb(activityName);
    } else {
      exportToExcel(activityName);
    }
  }

  if (btnConfirmExport) btnConfirmExport.addEventListener('click', runPendingExport);

  activityNameInput.addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      runPendingExport();
    }
  });

  function exportToExcel(activityName) {
    if (!hasCostStructure()) {
      alert('Nothing to export yet — no cost drivers have been generated.\n\n' +
            'If you have only run the resource plan, continue to the cost drivers step first.');
      return;
    }

    try {
      const rows = buildRows(window.currentStructure);
      const excelData = [CSV_COLUMNS].concat(rows);

      const wb = XLSX.utils.book_new();
      const ws = XLSX.utils.aoa_to_sheet(excelData);

      ws['!cols'] = [
        { wch: 28 },
        { wch: 45 },
        { wch: 28 },
        { wch: 45 },
        { wch: 10 },
        { wch: 30 },
        { wch: 45 },
        { wch: 10 },
        { wch: 28 },
        { wch: 45 },
        { wch: 35 },
        { wch: 18 },
        { wch: 34 },
        { wch: 50 },
        { wch: 50 }
      ];

      const range = XLSX.utils.decode_range(ws['!ref']);
      for (let col = range.s.c; col <= range.e.c; col++) {
        const cellAddress = XLSX.utils.encode_cell({ r: 0, c: col });
        if (ws[cellAddress]) {
          ws[cellAddress].s = {
            font: { bold: true },
            fill: { fgColor: { rgb: 'E60000' } },
            alignment: { horizontal: 'center' }
          };
        }
      }

      XLSX.utils.book_append_sheet(wb, ws, 'Cost Estimation');

      // Second sheet: the resource plan with all its fields (when one exists).
      const plan = window.currentStructure.resource_plan;
      const hasPlan = !!plan && (
        (Array.isArray(plan.resources) && plan.resources.length > 0) || !!plan.volume_driver
      );
      if (hasPlan) {
        const resInfo = buildResourceSheetData(plan);
        const resWs = XLSX.utils.aoa_to_sheet(resInfo.data);
        resWs['!cols'] = [
          { wch: 30 },
          { wch: 22 },
          { wch: 12 },
          { wch: 16 },
          { wch: 12 },
          { wch: 12 },
          { wch: 55 },
          { wch: 55 }
        ];
        for (let col = 0; col < RESOURCE_COLUMNS.length; col++) {
          const addr = XLSX.utils.encode_cell({ r: resInfo.headerRowIndex, c: col });
          if (resWs[addr]) {
            resWs[addr].s = {
              font: { bold: true },
              fill: { fgColor: { rgb: 'E60000' } },
              alignment: { horizontal: 'center' }
            };
          }
        }
        const titleAddr = XLSX.utils.encode_cell({ r: 0, c: 0 });
        if (resWs[titleAddr]) resWs[titleAddr].s = { font: { bold: true, sz: 14 } };

        XLSX.utils.book_append_sheet(wb, resWs, 'Resources');
      }

      const timestamp = new Date().toISOString()
        .replace(/T/, '_')
        .replace(/:/g, '-')
        .replace(/\..+/, '');
      const filename = sanitizeFilename(activityName) + '_' + timestamp + '.xlsx';

      XLSX.writeFile(wb, filename);

      if (window.setStatus) {
        window.setStatus('✓ Exported to ' + filename);
      }
    } catch (error) {
      console.error('Export error:', error);
      alert('Failed to export to Excel. Please try again.');
    }
  }

  function exportToPowerPoint(activityName) {
    if (!hasCostStructure()) {
      alert('Nothing to export yet — no cost drivers have been generated.\n\n' +
            'If you have only run the resource plan, continue to the cost drivers step first.');
      return;
    }
    const name = (activityName || 'Cost_Estimation').trim();
    const timestamp = new Date().toISOString()
      .replace(/T/, '_')
      .replace(/:/g, '-')
      .replace(/\..+/, '');
    const filename = sanitizeFilename(name) + '_' + timestamp + '.pptx';

    fetch('/export-pptx', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ structure: window.currentStructure, name: name })
    })
      .then(function(res) {
        if (!res.ok) {
          return res.json().then(function(j) { throw new Error(j.detail || res.statusText); }).catch(function() { throw new Error(res.statusText); });
        }
        return res.blob();
      })
      .then(function(blob) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
        if (window.setStatus) {
          window.setStatus('✓ Exported to ' + filename);
        }
      })
      .catch(function(err) {
        console.error('PPTX export error:', err);
        alert('Failed to export to PowerPoint: ' + (err.message || 'Please try again.'));
      });
  }

  function exportToDb(activityName) {
    if (!hasCostStructure()) {
      alert('Nothing to export yet — no cost drivers have been generated.\n\n' +
            'If you have only run the resource plan, continue to the cost drivers step first.');
      return;
    }
    fetch('/export-db', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ structure: window.currentStructure, name: activityName })
    })
      .then(function(res) {
        if (!res.ok) {
          return res.json().then(function(j) { throw new Error(j.detail || res.statusText); });
        }
        return res.json();
      })
      .then(function(data) {
        if (window.setStatus) {
          window.setStatus('✓ ' + data.message);
        }
        alert('Exported to database: ' + data.rows + ' rows saved.');
      })
      .catch(function(err) {
        console.error('DB export error:', err);
        alert('Failed to export to database: ' + (err.message || 'Please try again.'));
      });
  }

  console.log('Export module loaded (14-column CSV + Resources sheet + PowerPoint + DB export)');
})();
