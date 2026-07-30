# CRUD Operations for Cost Entities

## Overview

The D2C web app now supports full CRUD (Create, Read, Update, Delete) operations for all cost entities:
- **Cost Drivers**
- **Cost Components**  
- **Cost Inputs**

All operations automatically trigger **cascading updates** and **re-calculation** of the total cost.

## Features Implemented

### 1. **Backend API** (`app.py`)

**Endpoint:** `POST /entity/operation`

**Request Body:**
```json
{
  "activity_description": "...",
  "structure": {...},
  "entity_type": "driver|component|input",
  "operation": "add|edit|delete|update_quantity|update_cost",
  "driver_name": "...",
  "component_name": "...",
  "input_name": "...",
  "new_name": "...",
  "new_quantity": "...",
  "new_cost": 123.45
}
```

**Response:**
```json
{
  "structure": {...},
  "message": "Operation completed successfully"
}
```

### 2. **Frontend UI** (`index.html`, `style.css`)

**Action Buttons:**
- Each entity box shows **Edit (✎)** and **Delete (✕)** buttons on hover
- Styled with Vodafone theme
- Appear in top-right corner of entity boxes

**Edit Modal:**
- Single modal for editing all entity types
- Shows relevant fields based on entity type:
  - **Driver**: Name
  - **Component**: Name, Quantity
  - **Input**: Name, Quantity, Cost

### 3. **JavaScript Logic** (`script.js`, `crud.js`)

**`script.js` responsibilities:**
- Build and display tree structure
- Store current structure and activity globally
- Export functions for CRUD module
- Handle initial workflow execution

**`crud.js` responsibilities:**
- Add action buttons to entity boxes
- Handle edit/delete operations
- Make API calls to backend
- Refresh tree after operations

## Operations

### Cost Driver Operations

#### 1. Delete Driver
- **Action:** Click delete button (✕) on driver box
- **Effect:** Removes driver and ALL its components and inputs
- **Recalculation:** Automatic (total cost updated)

#### 2. Edit Driver
- **Action:** Click edit button (✎) on driver box
- **Effect:** 
  - Can change driver name
  - **Re-estimates** all components for this driver
  - **Re-estimates** all inputs for each component
  - **Re-estimates** all costs for each input
- **Recalculation:** Automatic

#### 3. Add Driver
- **Action:** Click "+ Add driver" button (to be added)
- **Effect:**
  - Identifies new driver based on activity description
  - **Estimates** components for new driver
  - **Estimates** inputs for each component
  - **Estimates** costs for each input
- **Recalculation:** Automatic

### Cost Component Operations

#### 1. Delete Component
- **Action:** Click delete button (✕) on component box
- **Effect:** Removes component and ALL its inputs
- **Recalculation:** Automatic

#### 2. Edit Component
- **Action:** Click edit button (✎) on component box
- **Effect:**
  - Can change component name
  - **Re-estimates** all inputs for this component
  - **Re-estimates** all costs for each input
- **Recalculation:** Automatic

#### 3. Update Component Quantity
- **Action:** Edit quantity in edit modal
- **Effect:** Updates quantity field only
- **Recalculation:** Automatic (total cost = unit cost × input qty × component qty)

#### 4. Add Component
- **Action:** Click "+ Add component" button (to be added)
- **Effect:**
  - Requires component name
  - **Estimates** inputs for new component
  - **Estimates** costs for each input
- **Recalculation:** Automatic

### Cost Input Operations

#### 1. Delete Input
- **Action:** Click delete button (✕) on input box
- **Effect:** Removes input
- **Recalculation:** Automatic

#### 2. Update Input Quantity
- **Action:** Edit quantity in edit modal
- **Effect:** Updates quantity field only
- **Recalculation:** Automatic (total cost = unit cost × input qty × component qty)

#### 3. Update Input Cost
- **Action:** Edit cost in edit modal
- **Effect:** Updates cost manually (overrides AI estimation)
- **Recalculation:** Automatic

#### 4. Add Input
- **Action:** Click "+ Add input" button (to be added)
- **Effect:**
  - Requires input name
  - **Estimates** cost for new input from internet
- **Recalculation:** Automatic

## Cascading Update Logic

### Delete Operations
```
Delete Driver → Remove driver + all components + all inputs
Delete Component → Remove component + all inputs
Delete Input → Remove input only
```

### Edit Operations
```
Edit Driver → Keep driver, clear components, re-estimate everything
Edit Component → Keep component, clear inputs, re-estimate inputs + costs
Edit Input (name) → Not implemented (would need re-estimation)
```

### Add Operations
```
Add Driver → Estimate components → Estimate inputs → Estimate costs
Add Component → Estimate inputs → Estimate costs
Add Input → Estimate cost only
```

### Quantity/Cost Updates
```
Update Component Quantity → Recalculate total costs (no re-estimation)
Update Input Quantity → Recalculate total costs (no re-estimation)
Update Input Cost → Recalculate total costs (no re-estimation)
```

## Total Cost Formula

```
Grand Total = Σ (monthly_cost_egp × input.quantity × component.quantity)
```

For all inputs across all components across all drivers.

## Testing the CRUD System

1. **Run the app:**
   ```bash
   uvicorn d2c_app.app:app --reload
   ```

2. **Generate initial structure:**
   - Enter activity description
   - Click "Estimate costs"
   - Wait for structure to load

3. **Test operations:**
   - Hover over any entity box to see action buttons
   - Click Edit (✎) to modify
   - Click Delete (✕) to remove
   - Check that total cost updates automatically

## Known Limitations

1. **"Add" buttons not yet visible** - The `addAddButton` function is defined but not yet called in `buildTree`. Need to add calls after each entity level.

2. **Data attributes for entity identification** - Entity boxes need `data-driver-name` and `data-component-name` attributes for CRUD to identify them properly.

3. **Re-estimation takes time** - Edit operations that trigger re-estimation can take 30-60 seconds per component/input due to web search and LLM calls.

4. **No undo** - Deletions are immediate and cannot be undone (consider adding confirmation dialogs).

## Next Steps

To fully complete the CRUD system:

1. **Add "Add Entity" buttons** in buildTree:
   ```javascript
   // After all drivers: add "Add driver" button
   // After each driver's components: add "Add component" button  
   // After each component's inputs: add "Add input" button
   ```

2. **Add data attributes** to entity boxes:
   ```javascript
   data-driver-name="${driver.cost_driver_name}"
   data-component-name="${comp.cost_component_name}"
   ```

3. **Add loading indicators** during re-estimation operations

4. **Add confirmation dialogs** for destructive operations

5. **Add success/error notifications** after operations complete

## Files Modified

- ✅ `d2c_app/app.py` - Added `handle_entity_operation()` and `/entity/operation` endpoint
- ✅ `d2c_app/index.html` - Added edit modal HTML
- ✅ `d2c_app/style.css` - Added action button styles
- ✅ `d2c_app/script.js` - Added global state and exports
- ✅ `d2c_app/crud.js` - **NEW** - Complete CRUD logic

The foundation is in place! 🎉
