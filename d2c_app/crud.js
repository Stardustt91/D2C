// CRUD operations for cost entities
// This file extends script.js with add/edit/delete functionality.
//
// Note: quantities were removed from the UI (every cost entity is fixed to 1),
// so the edit modal only carries name, and — for inputs — sharing and cost.
//
// Requires globals from script.js: currentStructure, currentActivity,
// currentEditContext, editModal, btnSaveEdit, buildTree, setStatus.

// Add action buttons to entity boxes
window.addEntityActions = function (entityBox, entityType, driverName, componentName, inputName) {
  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'entity-actions';
  actionsDiv.innerHTML = `
    <button class="btn-entity-action edit" title="Edit">✎</button>
    <button class="btn-entity-action delete" title="Delete">✕</button>
  `;

  actionsDiv.querySelector('.edit').addEventListener('click', function (e) {
    e.stopPropagation();
    openEditModal(entityType, driverName, componentName, inputName);
  });

  actionsDiv.querySelector('.delete').addEventListener('click', function (e) {
    e.stopPropagation();
    if (confirm(`Delete this ${entityType}?`)) {
      deleteEntity(entityType, driverName, componentName, inputName);
    }
  });

  entityBox.appendChild(actionsDiv);
};

// Open edit modal
function openEditModal(entityType, driverName, componentName, inputName) {
  window.currentEditContext = {
    type: entityType,
    driver: driverName,
    component: componentName,
    input: inputName,
    operation: 'edit',
    originalCost: null,
    originalSharing: null
  };

  const editModalTitle = document.getElementById('editModalTitle');
  const editEntityName = document.getElementById('editEntityName');
  const editSharingField = document.getElementById('editSharingField');
  const editCostField = document.getElementById('editCostField');
  const editEntitySharing = document.getElementById('editEntitySharing');
  const editEntityCost = document.getElementById('editEntityCost');

  editModalTitle.textContent = `Edit ${entityType}`;

  // Reset fields
  editSharingField.style.display = 'none';
  editCostField.style.display = 'none';
  editEntityName.value = '';
  editEntitySharing.value = '1';
  editEntityCost.value = '';

  if (entityType === 'driver') {
    editEntityName.value = driverName;
  } else if (entityType === 'component') {
    editEntityName.value = componentName;
  } else if (entityType === 'input') {
    editEntityName.value = inputName;
    editSharingField.style.display = 'block';
    editCostField.style.display = 'block';

    const struct = window.currentStructure;
    if (struct && struct.cost_drivers) {
      for (const dr of struct.cost_drivers) {
        if (dr.cost_driver_name === driverName) {
          for (const comp of dr.cost_components || []) {
            if (comp.cost_component_name === componentName) {
              for (const inp of comp.cost_inputs || []) {
                if (inp.cost_input_name === inputName) {
                  const sharing = inp.sharing || 1;
                  const cost = inp.monthly_cost_egp || '0';
                  editEntitySharing.value = sharing;
                  editEntityCost.value = cost;
                  window.currentEditContext.originalSharing = parseInt(sharing);
                  window.currentEditContext.originalCost = parseFloat(cost);
                  break;
                }
              }
              break;
            }
          }
          break;
        }
      }
    }
  }

  window.editModal.show();
}

// Open add modal
function openAddModal(entityType, driverName, componentName) {
  window.currentEditContext = {
    type: entityType,
    driver: driverName,
    component: componentName,
    operation: 'add'
  };

  const editModalTitle = document.getElementById('editModalTitle');
  const editEntityName = document.getElementById('editEntityName');

  editModalTitle.textContent = `Add ${entityType}`;
  editEntityName.value = '';
  document.getElementById('editEntityCost').value = '0';
  document.getElementById('editSharingField').style.display = 'none';
  document.getElementById('editCostField').style.display = 'none';

  window.editModal.show();
}

// Export for use in script.js
window.openAddModal = openAddModal;

// Save edit/add
window.btnSaveEdit.addEventListener('click', function () {
  if (!window.currentEditContext) {
    console.error('No edit context');
    return;
  }

  const ctx = window.currentEditContext;
  const newName = document.getElementById('editEntityName').value.trim();
  const newSharing = document.getElementById('editEntitySharing').value;
  const newCost = document.getElementById('editEntityCost').value;

  if (!newName && ctx.operation === 'add') {
    alert('Name is required');
    return;
  }

  // Determine the actual operation based on what changed
  let operation = ctx.operation;
  if (ctx.operation === 'edit' && ctx.type === 'input') {
    const sharingChanged = newSharing && parseInt(newSharing) !== ctx.originalSharing;
    const costChanged = newCost && parseFloat(newCost) !== ctx.originalCost;
    if (sharingChanged) {
      operation = 'update_sharing';
    } else if (costChanged) {
      operation = 'update_cost';
    }
  }

  const payload = {
    activity_description: window.currentActivity,
    structure: window.currentStructure,
    entity_type: ctx.type,
    operation: operation,
    driver_name: ctx.driver,
    component_name: ctx.component,
    input_name: ctx.input,
    new_name: newName || null,
    new_sharing: newSharing ? parseInt(newSharing) : null,
    new_cost: newCost ? parseFloat(newCost) : null
  };

  performEntityOperation(payload);
  window.editModal.hide();
});

// Delete entity
function deleteEntity(entityType, driverName, componentName, inputName) {
  performEntityOperation({
    activity_description: window.currentActivity,
    structure: window.currentStructure,
    entity_type: entityType,
    operation: 'delete',
    driver_name: driverName,
    component_name: componentName,
    input_name: inputName
  });
}

// Perform entity operation via API (exposed for parameter/formula actions from script.js)
window.performEntityOperation = function performEntityOperation(payload) {
  let statusMsg = 'Processing…';
  if (payload.operation === 'add') {
    statusMsg = `Adding ${payload.entity_type} and estimating dependencies… (this may take 30–60s)`;
  } else if (payload.operation === 'edit') {
    statusMsg = `Re-estimating ${payload.entity_type} and dependencies… (this may take 30–60s)`;
  } else if (payload.operation === 'delete') {
    statusMsg = 'Deleting and recalculating…';
  } else {
    statusMsg = 'Updating and recalculating…';
  }

  window.setStatus(statusMsg);
  var runBtn = document.getElementById('btn-run');
  if (runBtn) runBtn.disabled = true;

  fetch('/entity/operation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
    .then(res => {
      if (!res.ok) {
        return res.json().then(err => { throw new Error(err.detail || 'Request failed'); });
      }
      return res.json();
    })
    .then(data => {
      if (data.structure) {
        window.buildTree(data.structure);
        window.setStatus('✓ Operation completed successfully.');
      } else {
        window.setStatus('Operation failed: ' + (data.detail || 'Unknown error'), true);
      }
      if (window.updateStepButtons) window.updateStepButtons();
    })
    .catch(err => {
      window.setStatus('Error: ' + (err.message || 'Request failed'), true);
      if (window.updateStepButtons) window.updateStepButtons();
    });
};

console.log('CRUD module loaded');
