# Chat Flow for Activity Description - User Guide

## Overview

The **AI Assistant Chat Flow** helps you create comprehensive activity descriptions through a conversational interface. Instead of writing the description yourself, the AI guides you through all necessary details to ensure accurate cost estimation.

---

## How to Access

1. **From Main Page**: Click the **"💬 Use AI Assistant"** button next to the activity description field
2. **Direct URL**: Navigate to `/chat` or `http://localhost:8000/chat`

---

## Chat Flow Process

### 1️⃣ Initial Greeting

When you open the chat, the AI will greet you and ask you to describe the activity in your own words.

**Example:**
```
AI: Welcome to the Design-to-Cost Activity Description Assistant! 🎯

I'll help you create a comprehensive activity description for accurate cost estimation.

To get started, please describe the activity you want to estimate costs for.

For example:
- "Roll out 50 new LTE sites in Cairo and Giza"
- "Optimize network performance for 100 existing sites"
- "Install fiber optic cables across 20km"

I'll then ask you a few follow-up questions to ensure we capture all necessary details.
```

### 2️⃣ Initial Description

**You type:** A brief description of your activity.

**Example:**
```
Roll out 50 new 5G sites across Greater Cairo
```

### 3️⃣ AI Analysis

The AI checks if your description covers all **required points**:

1. **Phasing** - High-level phases (Design → Install → Test → Handover)
2. **Final Deliverable** - What is delivered at the end
3. **Primary Volume Driver** - Main cost/effort metric (e.g., number of sites)
4. **Labor Composition** - Types and seniority of people involved
5. **Location/Geography** - Geographical scope and access difficulty
6. **Transportation** - Transportation needs
7. **Accommodation** - Overnight stays required?
8. **Assets/Tools** - Key equipment, software, licenses needed

### 4️⃣ Follow-up Questions

If any required points are missing, the AI asks **one focused question at a time**.

**Example Conversation:**
```
AI: What types of team members and their seniority levels will be involved in this rollout?

You: 1 Senior Network Engineer, 3 Field Engineers, and 2 Installation Technicians per site

AI: Great! What are the key tools, equipment, and licenses required for this activity?

You: Drive test kits, spectrum analyzers, Ericsson ENM licenses, cranes for antenna installation

AI: Will overnight accommodation be required for any of the sites, or can all work be done with daily commutes?

You: Most sites are in Cairo so daily commutes, but 5 sites in remote areas will need hotel accommodation
```

### 5️⃣ Generated Description

Once all points are covered, the AI generates a **detailed, comprehensive activity description**.

**Example:**
```
AI: 📝 Generated Activity Description:

The activity involves the rollout of 50 new 5G sites across Greater Cairo, following a structured phasing approach: Design → Site Survey → Installation → Integration → Testing → Handover. The final deliverable is 50 fully operational, acceptance-tested 5G sites handed over to network operations. The primary volume driver is the number of sites (50 total).

The labor composition includes 1 Senior Network Engineer, 3 Field Engineers, and 2 Installation Technicians per site. The geographical scope covers Greater Cairo urban area, with 5 sites located in remote areas requiring special access considerations. Daily transportation between sites and office will be provided for urban sites, while the 5 remote sites will require overnight hotel accommodation.

Key assets and tools include drive test kits, spectrum analyzers, Ericsson ENM licenses, and cranes for antenna installation and positioning.

---

Does this look good?

• Type 'submit' or 'yes' to proceed with cost estimation
• Type 'edit' to modify the description
• Or type your corrections directly, and I'll update it
```

### 6️⃣ Review and Edit

You have three options:

#### Option A: Accept and Submit
**You type:** `submit` or `yes` or `confirm`

**Result:** Automatically redirects you to the main cost estimation page and starts the workflow.

#### Option B: Request Edit
**You type:** `edit`

**AI Response:**
```
AI: Please type the corrected activity description below, and I'll update it for you.
```

#### Option C: Direct Correction
**You type:** Your corrected description directly

**Example:**
```
The activity involves the rollout of 50 new 5G sites across Greater Cairo and Giza...
[your full corrected description]
```

The AI will show you the updated description and ask for confirmation again.

---

## After Submission

When you submit:

1. ✅ The activity description is saved
2. 🔄 You're redirected to the main cost estimation page
3. 🚀 The cost estimation workflow starts automatically
4. 📊 You'll see the cost structure being built in real-time

---

## Tips for Best Results

### ✅ DO:
- Be specific about quantities (e.g., "50 sites" not "some sites")
- Mention locations clearly (e.g., "Greater Cairo" not "around the city")
- Specify team composition (roles + numbers)
- Include key equipment/tools you know are needed
- Answer questions honestly - say "N/A" or "not needed" if something doesn't apply

### ❌ DON'T:
- Skip questions (answer each one, even briefly)
- Be too vague (e.g., "a few engineers" → say "3 Field Engineers")
- Worry about perfection - you can edit the final description
- Rush - take time to provide accurate information

---

## Keyboard Shortcuts

- **Enter**: Send message
- **Shift + Enter**: New line in message (without sending)

---

## Technical Details

### Session Management
- Each chat session is unique and isolated
- Sessions are stored in-memory (will be lost if server restarts)
- Session ID is automatically generated and maintained

### Data Flow
1. **Frontend** (`chat.html` + `chat.js`): User interface
2. **Backend** (`app.py`): Endpoints `/chat/init` and `/chat/message`
3. **AI Agent** (`description_extraction.py`): LLM-powered extraction logic
4. **Cost Estimation** (`/` route): Receives the final description

### API Endpoints

#### `POST /chat/init`
- Initializes a new chat session
- Returns: `session_id`, `bot_message`, `done`

#### `POST /chat/message`
- Processes user messages
- Body: `{"session_id": "...", "message": "..."}`
- Returns: `{"bot_message": "...", "done": bool, "activity_description": "..."}`

---

## Troubleshooting

### Issue: "Session not found"
**Solution**: Refresh the page to start a new session.

### Issue: AI keeps asking the same question
**Solution**: Provide a more detailed answer. The AI needs sufficient information to move forward.

### Issue: Can't submit the description
**Solution**: Make sure to type keywords like "submit", "yes", or "confirm" (case-insensitive).

### Issue: Chat doesn't redirect after submission
**Solution**: Check browser console for errors. Clear localStorage and try again.

---

## Example Complete Chat Session

```
🤖 AI: Welcome to the Design-to-Cost Activity Description Assistant! 🎯
       Please describe the activity you want to estimate costs for.

👤 You: Roll out 50 new 5G sites in Cairo

🤖 AI: What are the high-level phases of this work, from start to finish?

👤 You: Design, site survey, installation, testing, handover

🤖 AI: What types of team members and their seniority will be involved?

👤 You: 1 Senior Engineer, 3 Field Engineers, 2 Technicians per site

🤖 AI: What are the key tools and equipment required?

👤 You: Drive test kits, spectrum analyzers, cranes, Ericsson licenses

🤖 AI: Will overnight accommodation be needed?

👤 You: No, all sites are within daily commute distance

🤖 AI: 📝 Generated Activity Description:
       
       The activity involves rolling out 50 new 5G sites across Cairo...
       [full description shown]
       
       Does this look good?
       • Type 'submit' to proceed
       • Type 'edit' to modify

👤 You: submit

🤖 AI: Perfect! Redirecting you to the cost estimation interface...
       [Automatic redirect in 2 seconds]
```

---

## Benefits of Using the AI Assistant

✅ **Comprehensive Coverage**: Ensures all required points are captured  
✅ **No Missing Details**: Guided questions prevent information gaps  
✅ **Consistent Format**: Generates descriptions in the optimal format for cost estimation  
✅ **Time-Saving**: Faster than writing descriptions from scratch  
✅ **Quality Assurance**: AI validates completeness before proceeding  

---

## Next Steps

After the chat flow completes and cost estimation finishes:
- Review the generated cost structure
- Edit any cost entities using the CRUD features
- Export or share the cost breakdown
- Use the structure for project planning and budgeting

---

**Ready to try it?** Click **"💬 Use AI Assistant"** on the main page!
