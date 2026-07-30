# Unified Structure Formatter

## Overview
This document explains the unified structure formatter that replaces the three separate formatting functions previously scattered across different files.

## What Changed

### Before (3 separate functions)
- `cost_component.py` → `format_structure_so_far()` - showed drivers and components
- `cost_inputs.py` → `format_structure_so_far()` - showed drivers, components, and inputs
- `cost_estimation.py` → `format_structure_for_estimation()` - showed drivers, components, inputs, and costs

### After (1 unified function)
- `structure_formatter.py` → `format_structure()` - handles ALL stages with a single function

## Function Signature

```python
from structure_formatter import format_structure

formatted_output = format_structure(
    updated_structure=None,      # The current structure dict
    all_cost_drivers=None,       # List of all driver names
    all_cost_components=None,    # List of (driver, component) tuples
    all_cost_inputs=None         # List of (driver, component, input) tuples
)
```

## Progressive Estimation Stages

### Stage 1: Cost Drivers Identified
```
cost driver: cost component: cost input: cost
Shelters: Not yet estimated: Not yet estimated: Not yet estimated
Transportation: Not yet estimated: Not yet estimated: Not yet estimated
Installation Labor: Not yet estimated: Not yet estimated: Not yet estimated
Site Preparation: Not yet estimated: Not yet estimated: Not yet estimated
```

### Stage 2: Components Identified for First Driver
```
cost driver: cost component: cost input: cost
Shelters: Prefabricated Cabins: Not yet estimated: Not yet estimated
Shelters: Installation Hardware: Not yet estimated: Not yet estimated
Shelters: Environmental Control Units: Not yet estimated: Not yet estimated
Transportation: Not yet estimated: Not yet estimated: Not yet estimated
Installation Labor: Not yet estimated: Not yet estimated: Not yet estimated
Site Preparation: Not yet estimated: Not yet estimated: Not yet estimated
```

### Stage 3: Inputs Identified for First Component
```
cost driver: cost component: cost input: cost
Shelters: Prefabricated Cabins: Prefabricated Cabin: Not yet estimated
Shelters: Prefabricated Cabins: Transportation Cost: Not yet estimated
Shelters: Prefabricated Cabins: Installation Hardware: Not yet estimated
Shelters: Installation Hardware: Not yet estimated: Not yet estimated
Shelters: Environmental Control Units: Not yet estimated: Not yet estimated
Transportation: Not yet estimated: Not yet estimated: Not yet estimated
Installation Labor: Not yet estimated: Not yet estimated: Not yet estimated
Site Preparation: Not yet estimated: Not yet estimated: Not yet estimated
```

### Stage 4: Cost Estimated for First Input
```
cost driver: cost component: cost input: cost
Shelters: Prefabricated Cabins: Prefabricated Cabin: 180,000 EGP
Shelters: Prefabricated Cabins: Transportation Cost: Not yet estimated
Shelters: Prefabricated Cabins: Installation Hardware: Not yet estimated
Shelters: Installation Hardware: Not yet estimated: Not yet estimated
Shelters: Environmental Control Units: Not yet estimated: Not yet estimated
Transportation: Not yet estimated: Not yet estimated: Not yet estimated
Installation Labor: Not yet estimated: Not yet estimated: Not yet estimated
Site Preparation: Not yet estimated: Not yet estimated: Not yet estimated
```

## Updated Files

All files now import and use the unified formatter:

### 1. `structure_formatter.py` (NEW)
- Contains the unified `format_structure()` function
- Handles all stages of estimation
- Includes comprehensive test examples

### 2. `cost_component.py`
- Removed old `format_structure_so_far()` function
- Imports `format_structure` from `structure_formatter`
- Updated all calls to use the new function

### 3. `cost_inputs.py`
- Removed old `format_structure_so_far()` function
- Imports `format_structure` from `structure_formatter`
- Updated all calls to use the new function

### 4. `cost_estimation.py`
- Removed old `format_structure_for_estimation()` function
- Imports `format_structure` from `structure_formatter`
- Updated `cost_estimation_from_internet()` to build required parameter lists

### 5. `workflow_test.py`
- Updated to use the unified `format_structure()` function
- Builds all required parameter lists at each stage
- Displays structure progression at every step

### 6. `test_cost_estimation_flow.py`
- Updated examples to use the new function
- Shows clear examples of progressive estimation
- Includes usage documentation

## Usage Examples

### Basic Usage
```python
from structure_formatter import format_structure

# After identifying cost drivers
formatted = format_structure(
    updated_structure=structure,
    all_cost_drivers=['Shelters', 'Transportation'],
    all_cost_components=[],
    all_cost_inputs=[]
)

# After identifying components for first driver
formatted = format_structure(
    updated_structure=structure,
    all_cost_drivers=['Shelters', 'Transportation'],
    all_cost_components=[('Shelters', 'Prefabricated Cabins')],
    all_cost_inputs=[]
)

# After identifying inputs for first component
formatted = format_structure(
    updated_structure=structure,
    all_cost_drivers=['Shelters', 'Transportation'],
    all_cost_components=[('Shelters', 'Prefabricated Cabins')],
    all_cost_inputs=[('Shelters', 'Prefabricated Cabins', 'Cabin Cost')]
)
```

### Building Parameter Lists from Structure

```python
# Extract all_cost_drivers
all_cost_drivers = []
if structure and structure.get('cost_drivers'):
    for driver in structure['cost_drivers']:
        all_cost_drivers.append(driver.get('cost_driver_name'))

# Extract all_cost_components
all_cost_components = []
if structure and structure.get('cost_drivers'):
    for driver in structure['cost_drivers']:
        driver_name = driver.get('cost_driver_name')
        for comp in driver.get('cost_components', []):
            comp_name = comp.get('cost_component_name')
            all_cost_components.append((driver_name, comp_name))

# Extract all_cost_inputs
all_cost_inputs = []
if structure and structure.get('cost_drivers'):
    for driver in structure['cost_drivers']:
        driver_name = driver.get('cost_driver_name')
        for comp in driver.get('cost_components', []):
            comp_name = comp.get('cost_component_name')
            for inp in comp.get('cost_inputs', []):
                inp_name = inp.get('cost_input_name')
                all_cost_inputs.append((driver_name, comp_name, inp_name))
```

## Benefits

1. **Single Source of Truth**: One function handles all formatting stages
2. **Consistent Format**: Always shows "cost driver: cost component: cost input: cost"
3. **Progressive Display**: Shows "Not yet estimated" for items not yet identified/estimated
4. **Easy to Maintain**: Changes to format only need to be made in one place
5. **Clear Progression**: Users can see the structure evolve as estimation proceeds

## Testing

Run the test scripts to see the formatter in action:

```bash
# Test the formatter with all 4 stages
python agents/structure_formatter.py

# Test with cost estimation flow examples
python agents/test_cost_estimation_flow.py

# Test the full workflow (requires API keys)
python agents/workflow_test.py
```
