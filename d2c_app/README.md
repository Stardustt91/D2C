# D2C Cost Estimation Web App

Web interface for the D2C cost estimation workflow (Vodafone Egypt).

## Run the app

From the **project root** (D2C Final):

```bash
uvicorn d2c_app.app:app --reload --host 0.0.0.0 --port 8000
```

Or from inside `d2c_app`:

```bash
cd d2c_app
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Then open: **http://localhost:8000**

## Requirements

- Python with FastAPI, uvicorn
- Project dependencies (langchain, etc.) and `agents` module
- `vector_dbs` folder at project root (for FAISS)

## Usage

1. Enter an activity description in the text area.
2. Click **Estimate costs**.
3. Watch the cost structure tree update as drivers, components, inputs, and costs are generated.
4. Click any box to open a detail modal:
   - **Cost driver**: justification
   - **Cost component**: justification, quantity
   - **Cost input**: justification, quantity, cost, currency, cost justification, source, confidence
