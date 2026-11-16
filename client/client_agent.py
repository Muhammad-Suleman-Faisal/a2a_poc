import asyncio
import json
import logging
import os
from uuid import uuid4

import httpx
import jwt
import uvicorn
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
from jwt import PyJWKClient
from pydantic import BaseModel

# Add the repository root so direct script launches can import shared packages.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.audit import read_events, write_event as write_audit_event

load_dotenv()  # root ki .env se GEMINI_API_KEY load karta hai (genai.Client() se pehle zaruri)

AUTH_URL = "http://localhost:9000/token"
AUTH_BASE = "http://localhost:9000"  # IAM endpoints (exchange, JWKS) ka base
IAM_ENABLED = os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
IAM_ISSUER = os.getenv("IAM_ISSUER", "a2a-poc-iam")
logger = logging.getLogger(__name__)
# Busy hone par agla model try hota hai
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3.8-flash"]
# Registered remote agents: base URL aur unka required scope
AGENTS = {
    "travel": {"url": "http://localhost:9002", "scope": "agent:travel"},
    "meeting": {"url": "http://localhost:9001", "scope": "agent:meeting"},
    "birthday": {"url": "http://localhost:9003", "scope": "agent:birthday"},
    "content": {"url": "http://localhost:9004", "scope": "agent:content"},
}

llm = genai.Client()  # key GEMINI_API_KEY env variable se leta hai
CARDS: dict = {}  # discovery cache: agent_key -> AgentCard
_jwks = PyJWKClient(f"{AUTH_BASE}/.well-known/jwks.json")  # public keys cache hoti hain (IAM mode)

app = FastAPI()
if IAM_ENABLED:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("DASHBOARD_ORIGIN", "http://localhost:5173")],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )
else:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    prompt: str


# ---------------------------- IAM helpers (sirf IAM_ENABLED par use hote hain) ----------------------------

def verify_user_token(token: str) -> dict:
    """User ka RS256 token JWKS se verify karta hai; claims return ya exception.
    'act' claim wala token (agent token) /chat par qabool nahi."""
    key = _jwks.get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=["RS256"], issuer=IAM_ISSUER,
                        options={"verify_aud": False})
    if "act" in claims:
        raise ValueError("agent token not allowed here")
    return claims


async def authenticate(request: Request) -> tuple[str, dict]:
    """Authorization header se user token nikal kar verify karta hai; fail par 401."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "login required")
    token = auth[7:]
    try:
        # PyJWKClient sync hai, is liye thread mein taake event loop na ruke
        return token, await asyncio.to_thread(verify_user_token, token)
    except Exception:
        raise HTTPException(401, "invalid or expired token")


def permitted_agents(claims: dict) -> set[str]:
    """User ke scopes mein jin agents ka '<key>.invoke' hai, unki keys."""
    scopes = set(claims.get("scope", "").split())
    return {k for k in AGENTS if f"{k}.invoke" in scopes}


async def exchange_token(user_token: str, key: str, cid: str) -> str:
    """Auth server se us agent ke liye 120s ka token leta hai (client creds + user token)."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{AUTH_BASE}/auth/token/exchange",
            json={"target_agent": f"{key}-planner"},  # aud: meeting-planner, travel-planner, ...
            headers={
                "Authorization": f"Bearer {user_token}",
                "X-Client-Id": os.getenv("CLIENT_AGENT_ID", ""),
                "X-Client-Secret": os.getenv("CLIENT_AGENT_SECRET", ""),
                "X-Correlation-ID": cid,
            },
        )
    if r.status_code != 200:
        raise RuntimeError(r.json().get("detail", f"exchange failed ({r.status_code})"))
    return r.json()["access_token"]


# ---------------------------- existing flow ----------------------------

async def ask_llm(system: str, user: str) -> str:
    """Gemini call; har model ko 15s milte hain, fail/timeout par agla model."""
    last_error = None
    for model in MODELS:
        try:
            # wait_for: atka hua call 15s baad cancel hota hai
            resp = await asyncio.wait_for(
                llm.aio.models.generate_content(
                    model=model,
                    contents=user,
                    config=types.GenerateContentConfig(system_instruction=system),
                ),
                timeout=15,
            )
            print(f"[LLM] {model} ok")
            return resp.text.strip()
        except Exception as e:
            last_error = e
            print(f"[LLM] {model} failed: {type(e).__name__}")
            await asyncio.sleep(1)  # agle model se pehle thora intezar
    raise last_error


async def get_token(http: httpx.AsyncClient) -> str:
    """Auth server se saare agents ke scopes ka token leta hai."""
    scopes = " ".join(a["scope"] for a in AGENTS.values())
    r = await http.post(AUTH_URL, data={
        "grant_type": "client_credentials", "client_id": "dashboard-client",
        "client_secret": "client-secret", "scope": scopes,
    })
    r.raise_for_status()
    return r.json()["access_token"]


async def discover(http: httpx.AsyncClient) -> None:
    """Saare Agent Cards ek saath fetch karta hai; jo down ho use skip karta hai."""
    CARDS.clear()

    async def one(key: str, a: dict):
        try:
            CARDS[key] = await A2ACardResolver(httpx_client=http, base_url=a["url"]).get_agent_card()
        except Exception:
            pass  # agent down hai; selection mein nahi aayega

    await asyncio.gather(*(one(k, a) for k, a in AGENTS.items()))


def skills_catalog(allowed: set[str] | None = None) -> str:
    """Cards ke skills ko LLM ke liye text catalog mein badalta hai.
    allowed diya ho (IAM mode) to sirf un agents ke skills catalog mein jate hain."""
    lines = []
    for key, card in CARDS.items():
        if allowed is not None and key not in allowed:
            continue  # policy filter: LLM ko woh agent dikhta hi nahi
        for s in card.skills:
            lines.append(f"- {key}: {s.name} | {s.description} | tags: {', '.join(s.tags)}")
    return "\n".join(lines)


def keyword_agent_picks(prompt: str, allowed: set[str] | None = None) -> list[dict]:
    """Fill router omissions when the request contains an advertised skill tag."""
    lowered = prompt.lower()
    picks = []
    for key, card in CARDS.items():
        if allowed is not None and key not in allowed:
            continue
        matched = any(tag.lower() in lowered for skill in card.skills for tag in skill.tags)
        if matched:
            picks.append({"agent": key, "subtask": prompt.strip()})
    return picks


async def select_agents(prompt: str, allowed: set[str] | None = None,
                        cid: str | None = None) -> list[dict]:
    """LLM se relevant agents aur har ek ka subtask chunwata hai (JSON list)."""
    system = (
        "You route user requests to remote agents using their advertised skills. "
        "Select EVERY relevant agent. For each, write the subtask in one sentence, "
        "using only details from the user's prompt. Reply ONLY with JSON: "
        '[{"agent": "<key>", "subtask": "<text>"}]. Use only keys from the catalog.'
    )
    raw = await ask_llm(system, f"Catalog:\n{skills_catalog(allowed)}\n\nUser prompt: {prompt}")
    raw = raw.replace("```json", "").replace("```", "").strip()  # markdown fences hatao
    picks = json.loads(raw)
    picks = [p for p in picks if p["agent"] in CARDS]  # unknown keys filter
    existing = {p["agent"] for p in picks}
    picks.extend(p for p in keyword_agent_picks(prompt, allowed) if p["agent"] not in existing)
    if allowed is not None:
        # Allowlist guard: LLM ne permitted set se bahar ka agent chuna to drop (prompt-injection safety)
        for p in picks:
            if p["agent"] not in allowed:
                logger.warning("IAM deny dropped_agent=%s correlation_id=%s", p["agent"], cid)
                write_audit_event("client", cid=cid, actor=None, role=None, event="llm_pick_dropped",
                                  agent=p["agent"], decision="deny", status=403, reason="agent not permitted")
        picks = [p for p in picks if p["agent"] in allowed]
    return picks


def extract_text(data: dict) -> str:
    """A2A response (message ya task) se plain text nikalta hai."""
    result = data.get("result", {})
    if result.get("kind") == "message":
        return "\n".join(p.get("text", "") for p in result["parts"])
    parts = []
    for art in result.get("artifacts", []):
        parts += [p.get("text", "") for p in art.get("parts", [])]
    return "\n".join(parts)


async def call_agent(http: httpx.AsyncClient, key: str, subtask: str) -> dict:
    """Ek agent ko A2A task bhejta hai; failure par status 'failed' return karta hai."""
    try:
        client = A2AClient(httpx_client=http, agent_card=CARDS[key])
        msg = {"role": "user", "parts": [{"kind": "text", "text": subtask}], "messageId": uuid4().hex}
        req = SendMessageRequest(id=str(uuid4()), params=MessageSendParams(message=msg))
        resp = await client.send_message(req)
        data = resp.model_dump(mode="json", exclude_none=True)
        if "error" in data:
            raise RuntimeError(data["error"])
        return {"agent": key, "name": CARDS[key].name, "status": "completed", "text": extract_text(data)}
    except Exception as e:
        return {"agent": key, "name": key, "status": "failed", "text": str(e)}


async def call_agent_iam(user_token: str, key: str, subtask: str, cid: str) -> dict:
    """IAM mode: pehle is agent ka token exchange, phir usi token ke saath call.
    Har agent ka alag httpx client, taake tokens aapas mein mix na hon."""
    try:
        agent_token = await exchange_token(user_token, key, cid)
    except Exception as e:
        logger.info("IAM deny exchange agent=%s correlation_id=%s reason=%s", key, cid, e)
        write_audit_event("client", cid=cid, event="exchange", agent=key, decision="deny",
                          status=getattr(getattr(e, "response", None), "status_code", 403), reason=str(e))
        return {"agent": key, "name": CARDS[key].name, "status": "failed", "text": f"access denied: {e}"}
    write_audit_event("client", cid=cid, event="exchange", agent=key, decision="allow", status=200)
    headers = {"Authorization": f"Bearer {agent_token}", "X-Correlation-ID": cid}
    async with httpx.AsyncClient(timeout=60, headers=headers) as h:
        result = await call_agent(h, key, subtask)
        if result["status"] == "failed":
            logger.warning("IAM deny agent=%s correlation_id=%s reason=%s", key, cid, result["text"])
            write_audit_event("client", cid=cid, event="agent_call", agent=key, decision="fail", status=403, reason=result["text"])
        else:
            write_audit_event("client", cid=cid, event="agent_call", agent=key, decision="allow", status=200)
        return result  # 401/403 yahan 'failed' result ban jate hain


async def merge_answer(prompt: str, results: list[dict]) -> str:
    """Agents ke jawab ek normal assistant-style answer mein merge karta hai."""
    system = (
        "Merge the agents' outputs into one clear, concise assistant answer with a short "
        "heading per topic. Do not invent facts. Mention any agent that failed."
    )
    body = "\n\n".join(f"[{r['name']} - {r['status']}]\n{r['text']}" for r in results)
    return await ask_llm(system, f"User prompt: {prompt}\n\nAgent outputs:\n{body}")


async def run_chat(prompt: str, user_token: str | None = None,
                   claims: dict | None = None, cid: str | None = None) -> dict:
    """Poora flow: discover -> (policy) -> select -> token -> parallel calls -> merge -> summary.
    user_token/claims/cid sirf IAM mode mein milte hain; warna purana flow."""
    iam = IAM_ENABLED and claims is not None
    async with httpx.AsyncClient(timeout=60) as http:
        await discover(http)
        allowed = permitted_agents(claims) if iam else None
        if iam:
            removed = sorted(set(AGENTS) - allowed)
            write_audit_event("client", cid=cid, actor=claims.get("username"), role=claims.get("role"),
                              event="policy_filter", agent=",".join(removed), decision="deny" if removed else "allow",
                              status=403 if removed else 200, reason="agents removed by role policy" if removed else None)
        if iam and not allowed:
            # Policy: is role ko koi agent invoke karne ki ijazat nahi (LLM call bhi nahi hoti)
            logger.info("IAM deny no_permitted_agents subject=%s correlation_id=%s", claims.get("sub"), cid)
            return {"answer": "You are not permitted to use any agent with your current role.",
                    "summary": "0 agents permitted", "agents": [], "debug": {}, "correlation_id": cid}
        picks = await select_agents(prompt, allowed, cid)
        if not picks:
            answer = "No suitable agent found for this request. Your role does not permit any matching agent." if iam else "No suitable agent found for this request."
            out = {"answer": answer,
                   "summary": "0 agents selected", "agents": [], "debug": {}}
            if iam:
                out["correlation_id"] = cid
            return out

        if iam:
            # Har selected agent ke liye alag exchanged token; ek ka failure baqi ko nahi rokta
            results = await asyncio.gather(
                *(call_agent_iam(user_token, p["agent"], p["subtask"], cid) for p in picks))
        else:
            http.headers["Authorization"] = f"Bearer {await get_token(http)}"
            # Selected agents ko parallel call; ek ka failure baqi ko nahi rokta
            results = await asyncio.gather(*(call_agent(http, p["agent"], p["subtask"]) for p in picks))

    done = sum(r["status"] == "completed" for r in results)
    out = {
        "answer": await merge_answer(prompt, results),
        "summary": f"{done}/{len(results)} agents completed",
        "agents": results,
        "debug": {"selection": picks},
    }
    if iam:
        out["correlation_id"] = cid  # naya optional field, UI ignore kar sakta hai
    return out


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Dashboard ka endpoint; error ko JSON mein wapas bhejta hai taake UI par nazar aaye.
    IAM mode mein pehle user token verify hota hai (401 agar na ho)."""
    user_token = claims = cid = None
    if IAM_ENABLED:
        cid = uuid4().hex
        user_token, claims = await authenticate(request)  # try se bahar: 401 asli status ke saath jaye
        logger.info("IAM allow chat subject=%s role=%s correlation_id=%s", claims.get("sub"), claims.get("role"), cid)
        write_audit_event("client", cid=cid, actor=claims.get("username"), role=claims.get("role"),
                          event="chat_start", decision="allow", status=200)
    try:
        result = await run_chat(req.prompt, user_token, claims, cid)
        if IAM_ENABLED:
            write_audit_event("client", cid=cid, actor=claims.get("username"), role=claims.get("role"),
                              event="chat_end", decision="allow", status=200)
        return result
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
        if IAM_ENABLED:
            result["correlation_id"] = cid
            write_audit_event("client", cid=cid, actor=claims.get("username") if claims else None,
                              role=claims.get("role") if claims else None, event="chat_end",
                              decision="fail", status=500, reason=type(e).__name__)
        return result


@app.get("/agents")
async def list_agents():
    """Har registered agent ka status aur skill (uske Agent Card se) dashboard ko deta hai."""
    async with httpx.AsyncClient(timeout=5) as http:

        async def one(key: str, a: dict) -> dict:
            # Card mil gaya = online; na mile = offline (agent band hai)
            try:
                card = await A2ACardResolver(httpx_client=http, base_url=a["url"]).get_agent_card()
                skill = card.skills[0] if card.skills else None
                return {
                    "key": key, "name": card.name, "online": True,
                    "description": skill.description if skill else card.description,
                    "tags": skill.tags if skill else [],
                }
            except Exception:
                return {"key": key, "name": f"{key.capitalize()} agent", "online": False,
                        "description": "", "tags": []}

        # Sab agents ek saath check hote hain; order AGENTS dict wala rehta hai
        return await asyncio.gather(*(one(k, a) for k, a in AGENTS.items()))


@app.get("/audit")
async def list_audit(request: Request, limit: int = 100, user: str | None = None,
                     decision: str | None = None, agent: str | None = None) -> dict:
    """Return filtered recent audit events to an authenticated IAM administrator."""
    if not IAM_ENABLED:
        raise HTTPException(404, "not found")
    _, claims = await authenticate(request)
    role = claims.get("role")
    if role is None and "*.write" in claims.get("scope", "").split():
        role = "admin"
    if role != "admin":
        raise HTTPException(403, "admin role required")
    if limit < 1 or limit > 500:
        raise HTTPException(422, "limit must be between 1 and 500")
    records = sorted(read_events(), key=lambda row: row.get("ts", ""), reverse=True)
    if user is not None:
        records = [row for row in records if row.get("actor") == user]
    if decision is not None:
        records = [row for row in records if row.get("decision") == decision]
    if agent is not None:
        records = [row for row in records if agent in (row.get("agent") or "").split(",")]
    return {"events": records[:limit], "count": min(len(records), limit)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)
