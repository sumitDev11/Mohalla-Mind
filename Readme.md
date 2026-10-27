# Mohalla Mind

AI business partner for Paytm merchants: every kirana gets a personal agent, and a Mohalla Orchestrator lets nearby shops forecast, buy together, swap stock and sell surplus. Nothing happens without the merchant's "haan". See [CLAUDE.md](CLAUDE.md) for the full build guide.

## Run the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

API at http://localhost:8000, docs at http://localhost:8000/docs, health at `/health`.

## Run the frontend

```powershell
cd frontend
copy .env.local.example .env.local
npm install
npm run dev
```

Open http://localhost:3000.

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
```
