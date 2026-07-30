# Excel Export Feature - User Guide

## Overview

The Excel export feature allows you to export the complete cost estimation structure to an Excel file with all details including entity names, justifications, quantities, sharing factors, costs, and sources.

---

## How to Use

### 1️⃣ Complete Cost Estimation

First, run a cost estimation to generate the cost structure.

### 2️⃣ Click Export Button

Look for the **"📊 Export Excel"** button next to the Grand Total badge at the top of the cost structure.

### 3️⃣ Enter Activity Name

A modal will appear asking for the activity name:
- This name will be used as the filename
- It will also appear as a column in the Excel file
- Default: Pre-filled with your activity description (if short) or "Cost_Estimation"

**Example:**
```
Activity Name: 5G Rollout Cairo
```

### 4️⃣ Confirm Export

Click **"📊 Export"** or press **Enter** to generate the file.

### 5️⃣ File Download

The Excel file will automatically download with the format:
```
{Activity_Name}_{Timestamp}.xlsx

Example: 5G_Rollout_Cairo_2026-02-14_15-30-45.xlsx
```

---

## Excel File Structure

### Column Headers

The exported Excel file contains **18 columns**:

| Column | Description | Example |
|--------|-------------|---------|
| **Activity Name** | Name of the activity | 5G Rollout Cairo |
| **Cost Driver** | Top-level cost category | Shelters |
| **Driver Justification** | Why this driver is needed | Required to protect equipment... |
| **Cost Component** | Sub-category under driver | Prefabricated Cabins |
| **Component Justification** | Why this component is needed | Modular units for quick deployment... |
| **Component Quantity** | Number of components | 50 |
| **Cost Input** | Specific cost item | Prefabricated Cabin |
| **Input Justification** | Why this input is needed | Each site requires one cabin... |
| **Input Quantity** | Number of inputs per component | 1 |
| **Sharing** | How many entities share this cost | 1 (or 2, 3, etc.) |
| **Unit Cost (EGP/month)** | Cost per unit per month | 15,000 |
| **Total Cost (EGP/month)** | Calculated total: (Unit × Input Qty × Component Qty) / Sharing | 750,000 |
| **Is Percentage** | Whether cost is percentage-based | No |
| **Percentage Value** | Percentage if applicable | 14 |
| **Currency** | Currency code | EGP |
| **Cost Justification** | How cost was determined | According to SalaryExpert... |
| **Source URL** | Link to cost source | https://www.salaryexpert.com/... |
| **Confidence** | Estimation confidence level | high, medium, low |

### Summary Section

At the bottom of the spreadsheet:
- **Blank row** for separation
- **"SUMMARY"** header row
- **Grand Total (EGP/month)**: Sum of all cost inputs
- **Generated Date**: Timestamp of export

---

## Example Export Structure

```
Activity Name    | Cost Driver  | Component           | Cost Input      | Quantity | Sharing | Unit Cost | Total Cost
----------------|--------------|---------------------|-----------------|----------|---------|-----------|------------
5G Rollout      | Shelters     | Prefabricated Cabin | Cabin Unit      | 1        | 1       | 15,000    | 750,000
5G Rollout      | Shelters     | Prefabricated Cabin | Transport       | 1        | 1       | 2,000     | 100,000
5G Rollout      | Labor        | Engineers           | Senior Engineer | 2        | 3       | 45,000    | 30,000
...
```

---

## Features

### ✅ Complete Data Export
- All hierarchical levels (Drivers → Components → Inputs)
- All justifications and metadata
- Calculated totals with formula breakdown

### ✅ Timestamped Filenames
- Prevents overwriting previous exports
- Easy to track export history
- Format: `YYYY-MM-DD_HH-MM-SS`

### ✅ Activity Name Integration
- Appears in filename for easy identification
- Included as first column in every row
- Useful for merging multiple exports

### ✅ Formatted for Analysis
- Fixed column widths for readability
- Bold header row
- Summary section at the end
- Ready for pivot tables and charts

### ✅ Source Attribution
- Full URL links to sources
- Confidence levels included
- Cost justification with methodology

---

## Use Cases

### 📊 Budget Planning
Export for presentation to finance team with complete cost breakdown.

### 📈 Comparative Analysis
Export multiple scenarios and compare side-by-side in Excel.

### 📝 Documentation
Archive estimates for project records and audit trails.

### 🔄 Further Processing
Import into other systems (ERP, project management tools).

### 📧 Sharing
Send to stakeholders who prefer Excel format.

---

## Tips & Best Practices

### ✅ DO:
- **Use descriptive activity names**: "5G_Rollout_Cairo_Phase1" instead of "Project1"
- **Export after review**: Make sure all edits are complete before exporting
- **Keep exports organized**: Create a folder for all cost estimation exports
- **Include metadata**: Add notes in Excel about assumptions or special conditions

### ❌ DON'T:
- **Use special characters** in activity names (they'll be replaced with underscores)
- **Export without reviewing**: Check the cost structure first
- **Overwrite important files**: Timestamps prevent this, but be careful
- **Share sensitive data**: Remove confidential information before sending

---

## Filename Sanitization

Activity names are automatically sanitized for safe filenames:

| Input | Output |
|-------|--------|
| `5G Rollout (Cairo)` | `5G_Rollout__Cairo_` |
| `Network/Optimization` | `Network_Optimization` |
| `Phase #1 - Testing` | `Phase__1___Testing` |

**Characters replaced:** All non-alphanumeric characters except `-` and `_` are replaced with `_`

---

## Technical Details

### Library Used
- **SheetJS (xlsx)**: v0.20.1
- CDN: `https://cdn.sheetjs.com/xlsx-0.20.1/package/dist/xlsx.full.min.js`
- License: Apache 2.0

### File Format
- **Format**: `.xlsx` (Excel 2007+)
- **Compatibility**: Excel, Google Sheets, LibreOffice Calc
- **Encoding**: UTF-8

### Column Widths
- Auto-sized for readability
- Justification columns: 40-50 characters wide
- Numeric columns: 10-18 characters wide
- Name columns: 25-30 characters wide

### Performance
- Export time: < 1 second for typical structures (100-200 rows)
- File size: ~10-50 KB for typical estimates
- No server upload required (client-side generation)

---

## Troubleshooting

### Issue: Export button not visible
**Solution**: Run a cost estimation first. The button only appears when there's data to export.

### Issue: Download doesn't start
**Solution**: Check browser popup/download blockers. Allow downloads from localhost.

### Issue: File opens with garbled text
**Solution**: Make sure you're opening with Excel, Google Sheets, or compatible software (not a text editor).

### Issue: Missing data in export
**Solution**: Ensure all cost estimations completed successfully. Check for errors in the console.

### Issue: Can't open file - "corrupted" error
**Solution**: This usually indicates a browser extension interfering. Try in incognito mode or a different browser.

### Issue: Filename has too many underscores
**Solution**: Use simpler activity names with fewer special characters.

---

## Formula Reference

### Total Cost Calculation
```
Total Cost = (Unit Cost × Input Qty × Component Qty) / Sharing
```

**Example:**
- Unit Cost: 15,000 EGP/month
- Input Qty: 2
- Component Qty: 50
- Sharing: 1

```
Total Cost = (15,000 × 2 × 50) / 1 = 1,500,000 EGP/month
```

### Grand Total
```
Grand Total = Σ (All Total Costs)
```

---

## Future Enhancements (Potential)

1. **Multiple Formats**: Export to CSV, PDF, JSON
2. **Custom Column Selection**: Choose which columns to include
3. **Template Support**: Apply custom Excel templates
4. **Charts & Graphs**: Auto-generate visualizations
5. **Email Export**: Send directly via email
6. **Batch Export**: Export multiple estimates at once
7. **Compare Mode**: Side-by-side comparison sheet

---

## Keyboard Shortcuts

- **Open Export Modal**: Click "📊 Export Excel" button
- **Confirm Export**: Press `Enter` in the activity name field
- **Cancel**: Press `Escape` or click "Cancel"

---

## Example Use Case: Project Presentation

### Scenario
You need to present cost estimates for a 5G rollout to the finance team.

### Steps:
1. Complete cost estimation in the web interface
2. Review and edit any cost entities as needed
3. Click "📊 Export Excel"
4. Enter: "5G_Rollout_Cairo_Q1_2026"
5. Press Enter
6. Open the downloaded file
7. Add company logo and formatting
8. Create pivot table for summary view
9. Generate charts for visualization
10. Present to finance team

### Result:
Professional Excel file with complete cost breakdown, ready for presentation and archival.

---

## Support & Feedback

If you encounter issues or have suggestions for the export feature:
1. Check this guide for troubleshooting steps
2. Review browser console for error messages
3. Ensure SheetJS library loaded successfully
4. Test with a simple cost structure first

---

**Ready to export!** Complete your cost estimation and click the **"📊 Export Excel"** button.
