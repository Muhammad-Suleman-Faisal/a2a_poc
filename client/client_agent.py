import asyncio
import json
from uuid import uuid4

import httpx
import uvicorn
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
from pydantic import BaseModel

AUTH_URL = "http://localhost:9000/token"
# Busy hone par agla model try hota hai
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3.8-flash"]
AGENTS = {
    "travel": {"url": "http://localhost:9002", "scope": "agent:travel"},
    "meeting": {"url": "http://localhost:9001", "scope": "agent:meeting"},
    "birthday": {"url": "http://localhost:9003", "scope": "agent:birthday"},
    "content": {"url": "http://localhost:9004", "scope": "agent:content"},
}

llm = genai.Client()  # key GEMINI_API_KEY env variable se leta hai
CARDS: dict = {}  # discovery cache: agent_key -> AgentCard

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    prompt: str


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
            await asyncio.sleep(1)
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


def skills_catalog() -> str:
    """Cards ke skills ko LLM ke liye text catalog mein badalta hai."""
    lines = []
    for key, card in CARDS.items():
        for s in card.skills:
            lines.append(f"- {key}: {s.name} | {s.description} | tags: {', '.join(s.tags)}")
    return "\n".join(lines)


async def select_agents(prompt: str) -> list[dict]:
    """LLM se relevant agents aur har ek ka subtask chunwata hai (JSON list)."""
    system = (
        "You route user requests to remote agents using their advertised skills. "
        "Select EVERY relevant agent. For each, write the subtask in one sentence, "
        "using only details from the user's prompt. Reply ONLY with JSON: "
        '[{"agent": "<key>", "subtask": "<text>"}]. Use only keys from the catalog.'
    )
    raw = await ask_llm(system, f"Catalog:\n{skills_catalog()}\n\nUser prompt: {prompt}")
    raw = raw.replace("```json", "").replace("```", "").strip()  # markdown fences hatao
    picks = json.loads(raw)
    return [p for p in picks if p["agent"] in CARDS]  # unknown keys filter


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


async def merge_answer(prompt: str, results: list[dict]) -> str:
    """Agents ke jawab ek normal assistant-style answer mein merge karta hai."""
    system = (
        "Merge the agents' outputs into one clear, concise assistant answer with a short "
        "heading per topic. Do not invent facts. Mention any agent that failed."
    )
    body = "\n\n".join(f"[{r['name']} - {r['status']}]\n{r['text']}" for r in results)
    return await ask_llm(system, f"User prompt: {prompt}\n\nAgent outputs:\n{body}")


@app.post("/chat")
async def chat(req: ChatRequest):
    """Poora flow: discover -> select -> token -> parallel calls -> merge -> summary."""
    async with httpx.AsyncClient(timeout=60) as http:
        await discover(http)
        picks = await select_agents(req.prompt)
        if not picks:
            return {"answer": "No suitable agent found for this request.",
                    "summary": "0 agents selected", "agents": [], "debug": {}}

        http.headers["Authorization"] = f"Bearer {await get_token(http)}"
        # Selected agents ko parallel call; ek ka failure baqi ko nahi rokta
        results = await asyncio.gather(*(call_agent(http, p["agent"], p["subtask"]) for p in picks))

    done = sum(r["status"] == "completed" for r in results)
    return {
        "answer": await merge_answer(req.prompt, results),
        "summary": f"{done}/{len(results)} agents completed",
        "agents": results,
        "debug": {"selection": picks},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)