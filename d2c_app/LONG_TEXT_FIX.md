# Long Text Display Fix

## Problem
Previously, justification texts (especially long activity descriptions) were being stored in HTML `data-` attributes, which have character limits. This caused:
1. **Text truncation**: Long texts were cut off
2. **No scrolling**: Modal content couldn't handle overflow
3. **Poor formatting**: Long markdown text was hard to read

## Solution Implemented

### 1. JavaScript Storage for Justifications
Instead of storing justifications in `data-justification` attributes, we now use a JavaScript object:

```javascript
// Global storage for full justification texts
const justificationData = {};
```

**Benefits:**
- No character limits
- Full text preservation
- Better memory management

### 2. Updated `buildTree()` Function

#### For Cost Drivers:
```javascript
const driverId = 'driver-' + slug(driver.cost_driver_name);

// Store full justification (avoid data-attribute length limits)
justificationData[driverId] = driver.justification || '';

html += `
  <div class="tree-node" data-entity="driver" data-id="${driverId}">
    <div class="entity-box driver" 
      data-name="${escapeAttr(driver.cost_driver_name)}"
      data-driver-name="${escapeAttr(driver.cost_driver_name)}">
      <!-- No data-justification attribute -->
    </div>
  </div>
`;
```

#### For Cost Components:
```javascript
const compId = 'comp-' + slug(driver.cost_driver_name) + '-' + slug(comp.cost_component_name);

// Store full justification
justificationData[compId] = comp.justification || '';
```

#### For Cost Inputs:
```javascript
const inputId = 'inp-' + slug(driver.cost_driver_name) + '-' + slug(comp.cost_component_name) + '-' + slug(inp.cost_input_name);

// Store full justifications and metadata
justificationData[inputId] = {
  justification: inp.justification || '',
  cost_justification: inp.cost_justification || '',
  source: inp.source_url || '',
  confidence: inp.confidence || ''
};
```

### 3. Updated Modal Display Logic

```javascript
treeRoot.querySelectorAll('.entity-box').forEach(function (box) {
  box.addEventListener('click', function () {
    const entity = this.closest('.tree-node').dataset.entity;
    const entityId = this.closest('.tree-node').dataset.id;
    
    // Retrieve full justification from storage (not data attribute)
    const storedData = justificationData[entityId];
    
    if (entity === 'driver') {
      const justification = storedData || '';
      detailModalBody.innerHTML = `
        <div class="detail-row">
          <dt>Justification</dt>
          <dd class="scrollable-content">${markdownToHtml(justification)}</dd>
        </div>
      `;
    }
    // ... similar for component and input
  });
});
```

### 4. Scrollable Content Styling

Added CSS class `.scrollable-content` for long text areas:

```css
/* Scrollable content for long justifications */
.detail-row dd.scrollable-content {
  max-height: 400px;
  overflow-y: auto;
  padding-right: 0.5rem;
}

.detail-row dd.scrollable-content::-webkit-scrollbar {
  width: 6px;
}

.detail-row dd.scrollable-content::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 3px;
}

.detail-row dd.scrollable-content::-webkit-scrollbar-thumb {
  background: var(--vodafone-red);
  border-radius: 3px;
}

.detail-row dd.scrollable-content::-webkit-scrollbar-thumb:hover {
  background: #b30000;
}
```

### 5. Larger Modal Size

Changed modal size from `modal-lg` to `modal-xl` in `index.html`:

```html
<div class="modal-dialog modal-dialog-centered modal-xl">
```

## Result

✅ **No text truncation**: Full activity descriptions and justifications are preserved  
✅ **Scrollable content**: Long texts display with a styled scrollbar (max-height: 400px)  
✅ **Markdown formatting**: All text is properly converted from markdown to HTML  
✅ **Better UX**: Larger modal (modal-xl) with Vodafone-branded scrollbar  
✅ **Performance**: No character limit constraints from HTML attributes  

## Example: Long Activity Description

When you click on a cost driver with a long activity description like:

> As part of a strategic capacity expansion project, Vodafone Egypt will undertake the installation and commissioning of new 5G telecom equipment across 40 existing mobile network sites in Greater Cairo. This phased approach will involve a systematic rollout where each site will undergo a repeatable scope of installation, integration, and testing to ensure that the new equipment meets the required performance standards. The final deliverable for this project will be the successful installation, integration, and commissioning of 5G equipment at all 40 sites, culminating in successful site acceptance tests (SAT) and performance KPIs that meet defined thresholds...

The full text will now:
1. Display completely (no truncation)
2. Be formatted with proper paragraphs
3. Show a scrollbar if it exceeds 400px height
4. Render any markdown formatting (bold, italic, links, lists)

## Files Modified

1. **`d2c_app/script.js`**:
   - Added `justificationData` object
   - Updated `buildTree()` to store justifications in object
   - Updated modal event listener to retrieve from object
   - Added `.scrollable-content` class to long text areas

2. **`d2c_app/style.css`**:
   - Added `.scrollable-content` styling
   - Custom scrollbar styles with Vodafone branding

3. **`d2c_app/index.html`**:
   - Changed modal size from `modal-lg` to `modal-xl`

## Technical Notes

- **Unique IDs**: Each entity has a unique ID constructed from its hierarchy (e.g., `inp-{driver}-{component}-{input}`)
- **Data Cleanup**: `justificationData` is cleared at the start of each `buildTree()` call
- **Memory Management**: Old data is automatically garbage collected
- **Backward Compatible**: Still works with short justifications

## Testing

To verify the fix works:
1. Run a cost estimation with a long activity description
2. Click on any cost driver, component, or input
3. Verify:
   - Full text is visible
   - Scrollbar appears if content > 400px
   - Markdown formatting is correct
   - No text is cut off
