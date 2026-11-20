# Lyftr AI — Resilient LLM Chatbot

## 1. Live URLs

Add the deployed frontend, backend, and public GitHub repository URLs here after deployment. This workspace has not been deployed because hosting accounts and real LLM API keys were not provided.

- Frontend: not deployed
- Backend: not deployed
- Repository: not published

## 2. Run locally:

### With Docker

Requirements: Docker Desktop with Docker Compose and GNU Make (or run the equivalent `docker compose` commands directly).

1. Copy `.env.example` to `.env`.
2. Set `LLM_API_KEYS` to two distinct, valid provider API keys. Keys are comma-separated and must not be committed.
3. Keep `ALLOWED_ORIGINS=http://localhost:3000` for local use.
4. From the repository root, run `make up`.
5. Open <http://localhost:3000>. Check backend readiness at <http://localhost:8000/health>.
6. Stop the stack with `make down`. The named `chatbot_data` volume retains SQLite history between restarts.

On Windows without Make, run `docker compose up --build` and `docker compose down` in PowerShell.

### Without Docker

Requirements: Python 3.12+, Node.js 20+, and npm.

1. Configure the variables from `.env.example` in your shell. Set `DATABASE_URL` to a writable SQLite file, for example `sqlite:///./app.db`, and set `ALLOWED_ORIGINS` to `http://localhost:5173`.
2. In `backend`, create and activate a virtual environment, then run `pip install -r requirements-dev.txt`.
3. Start the API with `uvicorn app.main:app --reload --port 8000`.
4. In a second terminal, run `npm install` and `npm run dev` from `frontend`.
5. Set `VITE_API_BASE_URL=http://localhost:8000` before starting Vite.

## 3. Design decisions:

- **Key fallback:** API keys are tried in configured order (`key_1`, `key_2`, ...). Each request uses LiteLLM for provider calls; transient/provider failures and simulated failures move to the next key. The API reports only the key ID, never the secret.
- **Caching:** LiteLLM's local in-memory response cache is enabled with a configurable TTL. Its cache key includes the model and complete message list. Cache hits reuse the reply and provider token usage, report `LLM_CACHE`, and have no key ID.
- The cache is process-local, as allowed by the assignment; run one backend instance if you expect cache hits across different sessions. SQLite and the local cache are not shared across replicas.
- **SQLite session storage:** sessions and ordered messages are persisted in SQLite. User and assistant messages are inserted only after a successful model response; the history endpoint does not expose system prompts. Docker mounts the named `chatbot_data` volume at `/data`.
- **Token counts:** Provider usage is returned per completion, not accumulated over the session. A tokenizer fallback is used only if a provider omits usage fields.
- **Configuration:** The backend reads model, keys, CORS origins, database path, prompt, failure-hook switch, and cache lifetime from environment variables.

## 4. Verification:

Run backend tests from `backend` with `pip install -r requirements-dev.txt` followed by `pytest -q`.

- **Health:** `GET /health` reports database reachability, key count, and cache status.
- **Chat and cache:** Send the same first message with the same system prompt in two new sessions. The second identical request should report `LLM_CACHE`. A changed message should use a different cache entry.
- **Failover:** With `ENABLE_FAILURE_SIMULATION=true`, send `X-Simulate-Failure: key_1=429` to `POST /chat`; a healthy second key should answer and metadata should show `key_2` and one fallback attempt. `X-Simulate-Failure: *=429` should return `503 {"detail":"all providers unavailable"}` without adding history.
- **History and token usage:** Send two messages with one session ID, then call `GET /sessions/{session_id}/messages`. Each assistant message includes its own input/output token counts.
- **Validation:** Empty or overlong messages return JSON `422` errors and are not saved.

The optional UI failover control adds the simulation header to that one request. It is intended for demonstrations, not as a substitute for provider-level quotas or production abuse controls.

## 5. Setup Used:

- Editor: Visual Studio Code
- AI tools: Copilot SDK in VS Code

## 6. Bonus features (if any):

- Optional persistent per-session system prompt.
- A compact request-level failover demonstration control in the frontend.
- SQLite persistence, responsive chat layout, and readable API error messages.

## Deployment, step by step

This project is deploy-ready, but deployment and public submission links require your accounts, API keys, and repository permissions.

### A. Prepare and publish the repository

1. Create a public GitHub repository and push this project.
2. Copy `.env.example` to `.env` only for local use; never commit `.env`.
3. Create two separate API keys with your chosen model provider (for the default Gemini model, create keys in Google AI Studio). Store them as `LLM_API_KEYS=key-one,key-two` in the backend host's secret/environment settings; do not send or commit the keys.
4. Review the model name in `LLM_MODEL`; it must match a model supported by your provider and LiteLLM.

### B. Deploy the backend (Render example)

1. Create a Render **Web Service** from the GitHub repository.
2. Set the root directory to `backend` and use the Docker runtime. The container listens on the platform-provided `PORT` (and defaults to `8000` locally).
3. Configure `LLM_API_KEYS`, `LLM_MODEL`, `ALLOWED_ORIGINS`, `ENABLE_FAILURE_SIMULATION=true`, `DATABASE_URL`, `DEFAULT_SYSTEM_PROMPT`, and `CACHE_TTL_SECONDS`. Do not set a fixed `PORT` on Render; let the platform provide it.
4. Set `DATABASE_URL=sqlite:////data/app.db`. Attach a persistent disk mounted at `/data`; SQLite history must be on persistent storage, not the ephemeral container filesystem.
5. Deploy and wait for `GET https://<backend-host>/health` to return `200` with at least two configured keys. Test the API before deploying the frontend.

> **Important:** Confirm that your backend host's plan provides a persistent disk. Do not use an ephemeral filesystem for the required SQLite history.
>
> This is a public demo API without user authentication. Keep provider-side spending limits enabled, do not expose credentials in frontend settings, and add authentication/rate limiting before using it as a production service. The required failure-simulation hook is intentionally enabled for the assignment demo.

### C. Deploy the frontend (Vercel example)

1. Import the same GitHub repository into Vercel.
2. Set the project root to `frontend` and framework preset to Vite.
3. Add the build environment variable `VITE_API_BASE_URL=https://<backend-host>` (no trailing slash).
4. Deploy and open the public frontend URL. Send a message and verify the backend responds.
5. Set the backend's `ALLOWED_ORIGINS` to the exact public frontend origin, redeploy the backend, then verify chat and browser requests again.

### D. Verify the deployed system

1. Open `/health` on the deployed backend.
2. Send a message from the public frontend and check that metadata contains source, model, latency, and per-request token counts.
3. Repeat the same first message in a new browser session to demonstrate a cache hit.
4. Send `X-Simulate-Failure: key_1=429` to `/chat` (or use the UI demonstration control) and verify the backup key answers.
5. Send `X-Simulate-Failure: *=429`; verify `503` and confirm no messages were stored for that session.
6. Call `/sessions/{session_id}/messages` to demonstrate persisted multi-turn history.

### E. Submit

1. Record a 2–3 minute demo with voiceover showing the live UI, health endpoint, a multi-turn exchange, a cache hit, failover, and all-keys-failed response.
2. Fill in the live frontend URL, backend URL, and public GitHub repository in the **Live URLs** section above.
3. Submit the repository, URLs, and demo video using the assignment submission form.
