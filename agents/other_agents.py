import os
from google import genai
from google.genai import types

import sys
from pathlib import Path

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

# jwt_guard meeting_planner folder mein hai; wahi se import
sys.path.insert(0, str(Path(__file__).parent / "meeting_planner"))
from jwt_guard import JWTGuard  # noqa: E402

# Har agent ki config: port, scope, skill, aur mock jawab
AGENTS = {
    "travel": {
        "port": 9002,
        "scope": "agent:travel",
        "name": "Travel Planner Agent",
        "skill": ("plan_trip", "Travel Planner",
                  "Creates short getaway itineraries for a destination.",
                  ["travel", "trip", "itinerary", "getaway"]),
        "role": "You are a travel planner. Create an itinerary for exactly the destination and number of days requested (default 2 days if unspecified). Be concise, bullet points.",          
        "reply": "Getaway itinerary:\n- Day 1: arrival, local sights, evening walk\n- Day 2: viewpoints, local food, return",
    },
    "birthday": {
        "port": 9003,
        "scope": "agent:birthday",
        "name": "Birthday Planner Agent",
        "skill": ("plan_birthday", "Birthday Planner",
                  "Suggests birthday celebration themes and activities.",
                  ["birthday", "party", "celebration", "child"]),
        "role": "You are a birthday planner. Suggest themes and activities for the occasion described. Be concise, bullet points.",          
        "reply": "Birthday ideas:\n- Theme: favourite cartoon or superheroes\n- Activities: games, cake cutting, return gifts",
    },
    "content": {
        "port": 9004,
        "scope": "agent:content",
        "name": "Content Outline Agent",
        "skill": ("outline_content", "Content/Outline Planner",
                  "Builds outlines and structures for greetings, messages and write-ups.",
                  ["content", "outline", "greeting", "message", "writing"]),
        "role": "You are a content writer. Provide the requested outline or message structure. Be concise.",
        "reply": "Greeting structure:\n1. Warm opening\n2. Personal memory\n3. Wishes for the year ahead\n4. Loving closing",
    },
}


class LLMExecutor(AgentExecutor):
    """Har agent apna jawab Gemini se banata hai (agent ki role instruction ke saath)."""

    def __init__(self, role: str, fallback: str):
        self.role = role          # is agent ki zimmedari (system prompt)
        self.fallback = fallback  # LLM fail ho to fixed jawab
        self.llm = genai.Client()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = context.get_user_input()  # client ka subtask
        reply = self.fallback
        # Lite models pehle (tez); fail hone par agla model
        for model in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash"):
            try:
                resp = await self.llm.aio.models.generate_content(
                    model=model,
                    contents=user_text,
                    config=types.GenerateContentConfig(system_instruction=self.role),
                )
                reply = resp.text.strip()
                break
            except Exception:
                continue
        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Cancel support nahi hai
        raise Exception("cancel not supported")


def build_card(cfg: dict) -> AgentCard:
    """Config se Agent Card banata hai (discovery ke liye)."""
    sid, sname, sdesc, tags = cfg["skill"]
    skill = AgentSkill(id=sid, name=sname, description=sdesc, tags=tags)
    return AgentCard(
        name=cfg["name"],
        description=sdesc,
        url=f"http://localhost:{cfg['port']}/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main() -> None:
    """Command-line argument se agent chunta hai aur server chalata hai."""
    key = sys.argv[1]
    cfg = AGENTS[key]
    handler = DefaultRequestHandler(
        agent_executor=LLMExecutor(cfg["role"], cfg["reply"]),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(agent_card=build_card(cfg), http_handler=handler).build()
    # Is agent par sirf apne scope wala token chalega
    app.add_middleware(JWTGuard, required_scope=cfg["scope"])
    uvicorn.run(app, host="0.0.0.0", port=cfg["port"])


if __name__ == "__main__":
    main()