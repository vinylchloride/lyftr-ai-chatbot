# Lyftr AI — Resilient LLM Chatbot

## 1. Live URLs

- Frontend: Add the public Vercel URL here
- Backend: https://lyftr-ai-chatbot.onrender.com
- Health check: https://lyftr-ai-chatbot.onrender.com/health
- Repository: https://github.com/vinylchloride/lyftr-ai-chatbot

## 2. Run locally: with Docker and without

### With Docker

Requirements:

- Docker Desktop
- Docker Compose
- Make is optional on Windows

Run:

```powershell
Copy-Item .env.example .env
notepad .env
```

Replace the placeholders in `.env` with two private API keys, then run:

```powershell
docker compose up --build
```

Open:

- Frontend: http://localhost:3000
- Backend health: http://localhost:8000/health

Stop the stack:

```powershell
docker compose down
```

The Docker volume `chatbot_data` is mounted at `/data`, and SQLite is stored at `/data/app.db`.

On Windows, you can run Docker Compose directly if `make` is unavailable:

```powershell
docker compose up --build
docker compose down
```

### Without Docker

Requirements:

- Python 3.12+
- Node.js 20+
- npm

Start the backend:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

In a second terminal, start the frontend:

```powershell
Set-Location frontend
npm install
$env:VITE_API_BASE_URL="http://localhost:8000"
npm run dev
```

Open the Vite URL printed in the terminal. For local Vite use, set:

```dotenv
DATABASE_URL=sqlite:///./app.db
ALLOWED_ORIGINS=http://localhost:5173
VITE_API_BASE_URL=http://localhost:8000
```

Never commit `.env` or real API keys.

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key fallback

The backend reads a comma-separated key pool from `LLM_API_KEYS`. Keys are tried in order:

- `key_1`
- `key_2`
- Any additional configured keys

Requests that receive 429, quota exhaustion, or a 5xx response move to the next key. The API never exposes provider secrets; it reports only the key ID.

The optional `X-Simulate-Failure` header tests failover within a single request. It is enabled only when `ENABLE_FAILURE_SIMULATION=true`.

### Caching

LiteLLM uses a local in-memory cache. The cache key is based on the model and the complete message list sent to the provider. Identical requests reuse the same reply and stored token usage.

- `LIVE_INFERENCE`: a provider generated the reply
- `LLM_CACHE`: LiteLLM returned a cached reply

The cache is process-local. A backend restart clears cache entries, while SQLite chat history remains separate.

### SQLite session storage

The backend creates the SQLite tables automatically at startup. User and assistant messages are stored in the `messages` table for a `session_id`. Only successful chat responses are saved.

The Docker setup mounts the named `chatbot_data` volume at `/data`, with SQLite at `/data/app.db`. The `/sessions/{session_id}/messages` endpoint returns oldest-first history without the system prompt.

## 4. Verification: how to see failover, cache hits and per-message token usage

Run backend tests:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q
```

### Health check

Open:

```text
https://lyftr-ai-chatbot.onrender.com/health
```

An expected response is:

```json
{"status":"ok","keys_configured":2,"cache_enabled":true}
```

### Cache hit

1. Reload the frontend.
2. Send the same first message in two separate sessions.
3. The first response should show `LIVE_INFERENCE`.
4. The identical second response should show `LLM_CACHE`.

### Key failover

With failure simulation enabled, send:

```text
X-Simulate-Failure: key_1=429
```

The response should use `key_2` and report `fallback_attempts: 1`.

To fail every key for one request, send:

```text
X-Simulate-Failure: *=429
```

The response is `503` with:

```json
{"detail":"all providers unavailable"}
```

No user or assistant message is stored for that failed turn.

### Session history and token usage

1. Send a message in the public frontend.
2. Open the browser developer tools.
3. Find the `/chat` request and copy its `session_id`.
4. Open:

```text
https://lyftr-ai-chatbot.onrender.com/sessions/SESSION_ID/messages
```

Each assistant message includes its own input and output token counts. Token values are never accumulated into a conversation total.

## 5. Setup Used: your editor and AI tools

- Editor: Visual Studio Code
- AI tools: GitHub Copilot and Copilot SDK in VS Code
- Backend: Python 3.12, FastAPI, LiteLLM, SQLite
- Frontend: React, TypeScript, Vite
- Deployment: Docker, Render, Vercel
- Source control: Git and GitHub

## 6. Bonus features (if any): what you added beyond the requirements and why

- Modern responsive chat interface with readable desktop and mobile layouts.
- Guest recent-chat history in browser local storage.
- Recent chats can reopen existing SQLite sessions without a login.
- Real backend health/status indicator in the frontend.
- Optional session system-prompt input.
- Request-level failover test toggle.
- Professional welcome screen and practical suggested prompts.

These features improve usability and help demonstrate backend behavior without changing the required API contract.
