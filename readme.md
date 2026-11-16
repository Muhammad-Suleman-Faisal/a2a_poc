# A2A POC

Local proof of concept for the **Agent2Agent (A2A) protocol**. A React dashboard sends prompts to a client agent, which discovers remote agents through their Agent Cards, selects relevant agents with Gemini, calls them over A2A, and merges their replies.

> Learning demo, not a production service. Legacy demo credentials in `auth/main.py` are POC-only.

## Architecture

```
React Dashboard -> Client Agent (FastAPI) -> 4 Remote Agents (A2A)
                        |                         |
                        +------> Auth Server <-----+
```

## Flow

1. User enters a prompt in the dashboard.
2. Client agent discovers agents through their Agent Cards.
3. Gemini selects relevant agents and writes a subtask for each.
4. The client obtains a scoped credential and calls selected agents in parallel.
5. Gemini merges the results into one answer.
6. The dashboard shows the answer, status summary, and debug details.

## Services

| Service | Port | Notes |
|---|---:|---|
| Dashboard (React + Vite) | 5173 | UI |
| Client agent (FastAPI) | 8100 | `/chat`, `/agents`; `/audit` is admin-only when IAM is enabled |
| Auth server (FastAPI) | 9000 | IAM endpoints listed below |
| Meeting Planner | 9001 | Meeting planning |
| Travel Planner | 9002 | Travel planning |
| Birthday Planner | 9003 | Birthday ideas |
| Content/Outline Planner | 9004 | Content outlines |

Each remote agent serves its Agent Card at `/.well-known/agent-card.json`.

## Tech stack

Python, FastAPI, Starlette, `a2a-sdk` 0.3.x, PyJWT, Google Gemini (`google-genai`), React, Vite.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd dashboard
npm install
cd ..
```

Copy `.env.example` to `.env` and set `GEMINI_API_KEY`. Set the IAM credentials and signing secret to local values before enabling IAM. In `dashboard/.env`, set `VITE_IAM_ENABLED` to control the dashboard IAM UI. Never commit environment files.

## Run

Start the auth server, agents, and client with `.\start_all.ps1`; it starts auth on 9000, agents on 9001-9004, and the client on 8100. Start the dashboard separately:

```powershell
cd dashboard
npm run dev
```

Open http://localhost:5173. The VS Code `ALL SERVICES` launcher claim is outdated: `.vscode/launch.json` does not start the full stack. Also, `vite.config.js` proxies `/chat` to port 8000, but that proxy is unused because `App.jsx` calls `http://localhost:8100` directly.

## IAM

Set `IAM_ENABLED` in the root `.env` and `VITE_IAM_ENABLED` in `dashboard/.env`. When false, the app uses its original behavior.

| Role | Scopes |
|---|---|
| admin | All scopes below |
| user | `meeting.invoke`, `travel.invoke`, `birthday.invoke`, `content.invoke`, `meeting.read` |
| viewer | `meeting.read` |

Available scopes are `<agent>.invoke`; `<agent>.write` for meeting, travel, and birthday; and `meeting.read`. Write skills are simulated stubs.

Auth endpoints: `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/token/exchange`, and `/.well-known/jwks.json`. The client exposes `/chat`, `/agents`, and admin-only `/audit`.

```text
Dashboard login -> user JWT -> Client Agent (policy filter, allowlist)
  -> token exchange (aud=agent, 120s) -> agent (JWKS verify, aud+scope)
  -> audit log
```

Audit events are written to `audit/auth.jsonl` and `audit/client.jsonl`. CORS is limited to `DASHBOARD_ORIGIN`. Demo users are seeded on first database creation; login locks out after 5 failures.

Environment keys in `.env.example` (names only): `GEMINI_API_KEY`, `IAM_ENABLED`, `IAM_DB_PATH`, `IAM_ISSUER`, `IAM_TOKEN_SECRET`, `IAM_ADMIN_USERNAME`, `IAM_ADMIN_PASSWORD`, `IAM_USER_USERNAME`, `IAM_USER_PASSWORD`, `IAM_VIEWER_USERNAME`, `IAM_VIEWER_PASSWORD`, `CLIENT_AGENT_ID`, `CLIENT_AGENT_SECRET`, `AUTH_URL`, `DASHBOARD_ORIGIN`. The dashboard also uses `VITE_IAM_ENABLED` from `dashboard/.env.example`.

To change role access, edit `ROLE_SCOPES` in `auth/rbac.py`, restart auth, and log in again.

| Agent | Skills |
|---|---|
| Meeting Planner | Meeting planning and time extraction; new skills are listed in its Agent Card |
| Travel Planner | Travel planning; new skills are listed in its Agent Card |
| Birthday Planner | Birthday ideas; new skills are listed in its Agent Card |
| Content/Outline Planner | Content outlines; new skills are listed in its Agent Card |

## Security (legacy mode)

With IAM disabled, the original demo uses OAuth2 client credentials and HS256 JWTs with a 15-minute expiry. Each agent requires its own scope; Agent Cards stay public for discovery. Responses: no token = `401`, invalid token = `401`, wrong scope = `403`.

## Example prompts

| Prompt | Expected |
|---|---|
| Plan a 5-day trip to Hunza and schedule a meeting with Ali at 7:00 pm. | Travel, Meeting |
| Birthday party ideas for my son and a structure for his birthday card. | Birthday, Content |
| Plan a Murree getaway, meet Ali at 5 pm, child birthday ideas, and a loving greeting structure. | All four |
| Tell me a joke. | No suitable agent found |

## Tests

With IAM-on services running, run `python client/test_iam_e2e.py`. For legacy mode with IAM off, run `python client/test_client.py`. Dashboard tests: `cd dashboard; npm test`.

## Project structure

```
agents/               Remote agents
auth/                 Auth server, RBAC, IAM database/keys (local)
client/               Client agent and tests
dashboard/            React app
audit/                Local JSONL audit logs
requirements.txt      Python dependencies
start_all.ps1         Starts auth, agents, and client
```

## Notes

- Remote agents use Gemini for replies, with fixed fallbacks if the LLM fails.
- Meeting Planner extracts the time with a regex, with no LLM.
- Tasks and chat are in memory; only IAM uses SQLite.
- `requirements.txt` is present; `a2a-sdk` is pinned below 1.0 because 1.x changed the server API.





