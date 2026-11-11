# A2A POC

Local proof of concept for the **Agent2Agent (A2A) protocol**. A React dashboard sends a prompt to a client agent, which discovers remote agents through their Agent Cards, selects the relevant ones with Gemini, calls them over A2A with OAuth2/JWT, and returns one merged answer.

> Learning demo, not a production service. Secrets are hardcoded for demo purposes.

## Architecture

```
React Dashboard -> Client Agent (FastAPI) -> 4 Remote Agents (A2A, Bearer JWT)
                        |
                        +-> Auth Server (OAuth2 client_credentials)
```

## Flow

1. User enters a prompt in the dashboard.
2. Client agent fetches all Agent Cards (discovery).
3. Gemini picks every relevant agent and writes a subtask for each.
4. Client agent gets a scoped JWT from the auth server.
5. Selected agents are called in parallel over A2A.
6. Gemini merges the results into one answer.
7. Dashboard shows the answer, a status summary and debug details.

## Services

| Service | Port | Notes |
|---|---|---|
| Dashboard (React + Vite) | 5173 | UI |
| Client agent (FastAPI) | 8100 | `POST /chat` |
| Auth server (FastAPI) | 9000 | `POST /token` |
| Meeting Planner | 9001 | scope `agent:meeting` |
| Travel Planner | 9002 | scope `agent:travel` |
| Birthday Planner | 9003 | scope `agent:birthday` |
| Content/Outline Planner | 9004 | scope `agent:content` |

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

Create `.env` in the project root:

```
GEMINI_API_KEY=your-key
```

Get a free key from Google AI Studio. Never commit `.env`.

## Run

**VS Code (recommended):** Run and Debug, choose **ALL SERVICES**, press `F5`.

**Manual** (one terminal each, venv active, `GEMINI_API_KEY` set):

```powershell
python auth\main.py
python agents\meeting_planner\main.py
python agents\other_agents.py travel
python agents\other_agents.py birthday
python agents\other_agents.py content
python client\client_agent.py
cd dashboard; npm run dev
```

Open http://localhost:5173

## Security (OAuth2/JWT)

- Flow: `client_credentials`, JWT signed with HS256, 15 minute expiry.
- Each agent requires its own scope; Agent Cards stay public for discovery.
- Responses: no token = `401`, invalid token = `401`, wrong scope = `403`.

## Example prompts

| Prompt | Expected |
|---|---|
| Plan a 5-day trip to Hunza and schedule a meeting with Ali at 7:00 pm. | Travel, Meeting |
| Birthday party ideas for my son and a structure for his birthday card. | Birthday, Content |
| Plan a Murree getaway, meet Ali at 5 pm, child birthday ideas, and a loving greeting structure. | All four |
| Tell me a joke. | No suitable agent found |

## Project structure

```
agents/
  meeting_planner/   main.py, jwt_guard.py
  other_agents.py    travel, birthday, content
auth/main.py         token server
client/client_agent.py
dashboard/           React app
.vscode/launch.json  ALL SERVICES launcher
```

## Notes

- Remote agents use Gemini to generate their replies, with fixed fallbacks if the LLM fails.
- Meeting Planner extracts the time with a regex, with no LLM.
- `a2a-sdk` is pinned below 1.0 because 1.x changed the server API.