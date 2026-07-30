# D2C Web App - User Guide

## Getting Started

### 1. Run the Application
```bash
cd "c:\Users\AtefY\Desktop\projects\D2C Final"
uvicorn d2c_app.app:app --reload --host 0.0.0.0 --port 8000
```

Open your browser to: **http://localhost:8000**

### 2. Generate Initial Cost Structure
1. Enter an activity description in the text area (example provided below)
2. Click **"Estimate costs"**
3. Wait for the structure to generate (may take 2-3 minutes)
4. The tree will update in real-time as each entity is estimated

**Example Activity Description:**
```
The service involves the deployment and commissioning of outdoor telecom equipment 
shelters, including prefabricated cabins, power backup systems, and environmental 
control units. The scope covers site preparation, transportation of shelters and 
equipment, mechanical and electrical installation, integration with existing network 
infrastructure, and on-site testing. The service also includes manpower, lifting 
equipment, safety measures, and coordination with local authorities to ensure 
compliance and timely activation.
```

## Understanding the Interface

### Cost Structure Tree (Vertical Layout)

The tree shows a 4-level hierarchy:

```
Cost Driver
  └─ Cost Component (with quantity)
      └─ Cost Input (with quantity, unit cost, total cost)
```

### Entity Boxes

Each entity is displayed in a box with:
- **Label**: Entity type (Cost driver / Cost component / Cost input)
- **Name**: Entity name
- **Metadata**: Quantity (if applicable)
- **Cost**: Unit cost and total cost (for inputs only)

**Visual Indicators:**
- **Red left border** = Cost Driver
- **Dark grey left border** = Cost Component
- **Light grey left border** = Cost Input

### Grand Total
Displayed at the top right of the cost structure section.

**Formula:** Sum of all `(unit cost × input qty × component qty)` for all inputs.

## CRUD Operations

### Viewing Details

**Click any entity box** to open a detail modal showing:

**Cost Driver:**
- Justification

**Cost Component:**
- Justification
- Quantity

**Cost Input:**
- Justification
- Input Quantity
- Component Quantity
- Unit Cost (monthly)
- **Total Cost (monthly)** ← highlighted
- Currency
- Cost Justification (how it was estimated)
- Source (clickable URL to the web source)
- Confidence (high/medium/low)

### Edit & Delete (Hover Actions)

**Hover over any entity box** to reveal action buttons:
- **✎ (Edit)** - Edit entity
- **✕ (Delete)** - Delete entity

### Delete Operations

Click the **✕** button and confirm.

**Cost Driver:**
- Deletes the driver
- Deletes ALL components under it
- Deletes ALL inputs under those components
- Recalculates grand total

**Cost Component:**
- Deletes the component
- Deletes ALL inputs under it
- Recalculates grand total

**Cost Input:**
- Deletes the input only
- Recalculates grand total

### Edit Operations

Click the **✎** button to open the edit modal.

**Cost Driver:**
- Can change name
- **Triggers re-estimation** of all components, inputs, and costs (takes 30-60s)
- Or keep same name to just view

**Cost Component:**
- Can change name → triggers re-estimation of inputs and costs
- Can change quantity → only recalculates totals (instant)

**Cost Input:**
- Can change name → not recommended (complex re-estimation)
- Can change quantity → only recalculates totals (instant)
- Can change cost → manually override the cost (instant)

### Add Operations

Click any **"+ Add..."** button (transparent box that appears on hover).

**"+ Add cost driver"** (at bottom of all drivers):
1. Opens modal asking for driver name
2. Click "Save Changes"
3. **Automatically estimates:**
   - Components for this driver (limited to 2)
   - Inputs for each component (limited to 2)
   - Costs for each input (from web search)
4. Takes 30-60 seconds
5. Updates tree and grand total

**"+ Add cost component"** (after each driver's components):
1. Opens modal asking for component name
2. Click "Save Changes"
3. **Automatically estimates:**
   - Inputs for this component (limited to 2)
   - Costs for each input (from web search)
4. Takes 20-40 seconds
5. Updates tree and grand total

**"+ Add cost input"** (after each component's inputs):
1. Opens modal asking for input name
2. Click "Save Changes"
3. **Automatically estimates:**
   - Cost for this input (from web search)
4. Takes 10-20 seconds
5. Updates tree and grand total

## Tips & Best Practices

### Quick Updates (Instant)
For quick adjustments without waiting for re-estimation:
- **Update quantities** → instant recalculation
- **Update costs manually** → instant recalculation
- **Delete entities** → instant recalculation

### Full Re-estimation (Slow)
These operations trigger full re-estimation with web searches:
- **Add driver** → estimates 2 components × 2 inputs each = ~4 web searches
- **Add component** → estimates 2 inputs = ~2 web searches
- **Add input** → estimates 1 cost = ~1 web search
- **Edit driver/component name** → same as adding

### Cost Estimation Sources
The system searches the web for current Egypt 2026 pricing using:
1. **Tavily search** (primary)
2. **DuckDuckGo search** (fallback)

Cost justifications now include proper source citations:
```
According to Module X Solutions (https://modulexsolutions.com/...), 
the shelter cost ranges from $2,000-$2,500. Using the mid-range...
```

### Understanding Total Costs

**Individual Total:** `unit cost × input qty × component qty`
- Shown on each cost input box

**Grand Total:** Sum of all individual totals
- Shown at top right of cost structure

**Example:**
```
Cost Input: Prefabricated Cabin
Unit Cost: 75,000 EGP/month
Input Qty: 2
Component Qty: 3
Individual Total: 75,000 × 2 × 3 = 450,000 EGP/month

Grand Total: (sum of all individual totals across all inputs)
```

## Troubleshooting

### Buttons Don't Appear
- Make sure you've completed the initial estimation first
- Hover over entity boxes (buttons only appear on hover)
- Check browser console (F12) for JavaScript errors

### Operations Don't Work
- Check browser console for error messages
- Check backend terminal for Python errors
- Make sure `currentActivity` and `currentStructure` are set (check console: `window.currentActivity`)

### Re-estimation Takes Too Long
- For testing, outputs are limited to 2 items each
- Add operations can still take 30-60s due to web searches
- Consider updating quantities/costs manually for instant results

### Costs Show as "0 EGP"
- The system is now configured to ALWAYS provide estimates
- If this happens, check the cost justification for details
- May indicate web search failures - check backend terminal

## Advanced Features

### Manual Cost Override
1. Click edit (✎) on any cost input
2. Change the cost value
3. Save
4. The new cost overrides the web-estimated cost
5. Grand total recalculates instantly

### Cascading Updates
All operations automatically handle dependencies:
- Delete driver → removes all children
- Edit driver → re-estimates all children
- Add component → estimates inputs and costs
- All totals recalculate automatically

## Testing Limits (for Speed)

To keep testing fast, the system limits outputs to first 2 items:
- **2 cost drivers** maximum
- **2 components per driver** maximum
- **2 inputs per component** maximum
- **Total: 2×2×2 = 8 cost estimations** maximum

To remove these limits for production, edit `d2c_app/app.py` and remove all `[:2]` slices.
