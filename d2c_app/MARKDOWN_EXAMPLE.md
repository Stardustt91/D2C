# Example: Markdown-Formatted Cost Justification

## What the LLM Will Now Generate

### Example 1: Truck Rental Cost

**Previous Format (Plain Text):**
```
The estimated monthly cost for truck rental is based on the average daily rental costs found in multiple sources. The daily rental costs were £15 (approximately EGP 957.37) according to Skyscanner, and $62 and $64 (approximately EGP 2901.59 and EGP 2995.19 respectively) according to Expedia. The average of these costs was calculated to be approximately EGP 2284.71 per day. To convert this to a monthly cost, I multiplied by 30, resulting in approximately EGP 68541.41.
```

**New Format (Markdown):**
```markdown
**Sources Used:**
- [Skyscanner](https://www.skyscanner.net): £15/day
- [Expedia](https://www.expedia.com): $62/day and $64/day

**Currency Conversion:**
- £15 × 62 (GBP/EGP) = **957 EGP/day**
- $62 × 31 (USD/EGP) = **1,922 EGP/day**
- $64 × 31 (USD/EGP) = **1,984 EGP/day**

**Average Daily Cost:**
(957 + 1,922 + 1,984) / 3 = **1,621 EGP/day**

**Monthly Cost:**
1,621 × 30 days = **48,630 EGP/month**

**Confidence:** Medium - Based on international rental platforms, local Egyptian rates may vary.
```

**How It Displays:**

---

**Sources Used:**
- [Skyscanner](https://www.skyscanner.net): £15/day
- [Expedia](https://www.expedia.com): $62/day and $64/day

**Currency Conversion:**
- £15 × 62 (GBP/EGP) = **957 EGP/day**
- $62 × 31 (USD/EGP) = **1,922 EGP/day**
- $64 × 31 (USD/EGP) = **1,984 EGP/day**

**Average Daily Cost:**
(957 + 1,922 + 1,984) / 3 = **1,621 EGP/day**

**Monthly Cost:**
1,621 × 30 days = **48,630 EGP/month**

**Confidence:** Medium - Based on international rental platforms, local Egyptian rates may vary.

---

### Example 2: Engineer Salary

**Markdown:**
```markdown
**Source:** According to [SalaryExpert](https://www.salaryexpert.com/salary/job/project-manager/egypt), 
the average Project Manager gross salary in Cairo is **477,013 EGP annually**.

**Calculation:**
- Annual salary: **477,013 EGP**
- Monthly salary: 477,013 / 12 = **39,751 EGP/month**

**Taxes:** Using Egypt's tax brackets for this income level:
- Tax rate: **20%**
- Monthly tax: 39,751 × 0.20 = **7,950 EGP**

**Net Monthly Salary:** 39,751 - 7,950 = **31,801 EGP/month**

**Confidence:** High - Direct data from salary survey for Egypt 2026.
```

---

### Example 3: Equipment Purchase (One-time)

**Markdown:**
```markdown
**Source:** According to [Alibaba Egypt Suppliers](https://www.alibaba.com/...), 
the cost for a **500 kVA diesel generator** ranges from **$8,000 to $12,000**.

**Price Analysis:**
- Lower bound: $8,000 × 31 = **248,000 EGP**
- Upper bound: $12,000 × 31 = **372,000 EGP**
- Average: (248,000 + 372,000) / 2 = **310,000 EGP**

**Amortization (5-year lifespan):**
- Total cost: **310,000 EGP**
- Lifespan: 60 months
- Monthly equivalent: 310,000 / 60 = **5,167 EGP/month**

*Note: This is a one-time purchase amortized over expected lifespan. 
Actual payment is upfront.*

**Confidence:** Medium - Based on supplier quotes, final price negotiable.
```

---

### Example 4: With Correction Note

**Markdown (After Reflection Agent Fix):**
```markdown
**Source:** According to [Rental Company](https://example.com), 
truck rental costs **$21 per day**.

**Calculation:**
- Daily cost: $21 × 31 (USD/EGP) = **651 EGP/day**
- Monthly cost: 651 × 30 = **19,530 EGP/month**

**Confidence:** Medium - International rates, local verification recommended.

---
**Validation Note:** Initial calculation was corrected. *Daily cost was not multiplied by 30 for monthly cost*
```

---

## Visual Comparison

### Plain Text Display:
```
The estimated monthly cost for truck rental is based on the average daily 
rental costs found in multiple sources. The daily rental costs were £15 
(approximately EGP 957.37) according to Skyscanner, and $62 and $64...
```
- Dense wall of text
- Hard to scan
- No visual emphasis
- Links buried in text

### Markdown Display:
**Sources Used:**
- [Skyscanner](url): £15/day
- [Expedia](url): $62/day

**Calculation:**
- Daily: **1,621 EGP**
- Monthly: **48,630 EGP**

- Clear sections
- Easy to scan
- Key values pop out
- Links prominent

---

## How the LLM Formats It

The LLM is instructed to use this template:

```markdown
**Source:** According to [Source Name](URL), the cost is **X EGP**

**Calculation:**
- Step 1: **Value 1**
- Step 2: **Value 2**
- Result: **Final Value**

**Reasoning:** Why this estimate makes sense...

*Optional note: Additional context or caveats*
```

---

## Key Markdown Syntax Used

| Syntax | Purpose | Example |
|--------|---------|---------|
| `**text**` | Bold (key values) | **15,000 EGP** |
| `*text*` | Italic (notes) | *Price may vary* |
| `[text](url)` | Hyperlinks | [Source](url) |
| `` `code` `` | Technical terms | `setup fee` |
| `- item` | Bullet points | - Item 1 |
| `---` | Horizontal rule | Section separator |

---

## Benefits

### For Users:
✅ **Faster scanning** - Bold values catch the eye  
✅ **Clear structure** - Sections with headers  
✅ **Easy verification** - Clickable source links  
✅ **Professional** - Polished appearance  

### For Analysis:
✅ **Audit trail** - Clear calculation steps  
✅ **Source attribution** - Prominent links  
✅ **Transparency** - Show all reasoning  
✅ **Reproducible** - Can verify calculations  

### For Maintenance:
✅ **Consistent format** - All justifications follow template  
✅ **Easy styling** - CSS controls appearance  
✅ **Future-proof** - Can add more markdown features  

---

## Testing Checklist

### Visual Tests
- [ ] Bold text appears in Vodafone red
- [ ] Links are clickable and styled
- [ ] Bullet points are properly formatted
- [ ] Horizontal rules display as separators
- [ ] Spacing is appropriate
- [ ] Mobile display works correctly

### Content Tests
- [ ] Calculations are highlighted
- [ ] Sources are prominent
- [ ] Notes are italicized
- [ ] Code blocks (if used) are styled
- [ ] Legacy format still works

### Edge Cases
- [ ] Very long justifications
- [ ] Multiple links in one line
- [ ] Nested bold in italic
- [ ] Mixed markdown and plain text
- [ ] Special characters in links

---

**Markdown formatting is now live and will make cost justifications much more readable and professional!** 🎉
