import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()  # root ki .env se GEMINI_API_KEY load karta hai (genai.Client() se pehle zaruri)

# jwt_guard meeting_planner folder mein hai; wahi se import
sys.path.insert(0, str(Path(__file__).parent / "meeting_planner"))
from jwt_guard import JWTGuard  # noqa: E402
from jwt_guard import JWTCallContextBuilder  # noqa: E402

# Har agent ki config: port, scope, skill, LLM role, aur fallback jawab
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
        "reply": "Getaway itinerary:\n- Day 1: arrival, local sights, evening walk\n- Day 2: viewpoints, local food, return",
        "extra_skills": [
            ("plan_itinerary", "Plan Itinerary", "Create a destination itinerary for the requested dates and pace.", ["itinerary", "trip plan", "days"]),
            ("estimate_budget", "Estimate Budget", "Estimate travel costs by transport, stay, food, and activities.", ["budget", "cost", "expenses"]),
            ("packing_checklist", "Packing Checklist", "Prepare a practical packing list for destination and season.", ["packing", "pack", "checklist"]),
            ("route_and_transport", "Route and Transport", "Suggest routes and local transport options.", ["route", "transport", "drive", "flight"]),
            ("best_season_advice", "Best Season Advice", "Recommend a travel season based on weather and activities.", ["best time", "season", "weather"]),
            ("visa_document_checklist", "Visa and Document Checklist", "List travel documents and visa preparation steps.", ["visa", "passport", "documents"]),
            ("compare_destinations", "Compare Destinations", "Compare destinations against stated preferences.", ["compare", "versus", "destination"]),
            ("book_trip", "Book Trip", "Simulate a booking confirmation without a real reservation.", ["book", "book a trip", "reserve", "booking"]),
        ],
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
        "reply": "Birthday ideas:\n- Theme: favourite cartoon or superheroes\n- Activities: games, cake cutting, return gifts",
        "extra_skills": [
            ("plan_party", "Plan Party", "Plan a birthday theme, activities, and schedule.", ["party", "theme", "activities"]),
            ("gift_suggestions", "Gift Suggestions", "Suggest age-appropriate birthday gifts.", ["gift", "present", "gifts"]),
            ("party_budget", "Party Budget", "Estimate a birthday celebration budget.", ["budget", "cost", "spend"]),
            ("guest_list_and_invites", "Guest List and Invites", "Organize guests and draft invitation wording.", ["guest list", "invite", "invitation"]),
            ("menu_and_cake_planner", "Menu and Cake Planner", "Suggest a birthday menu and cake ideas.", ["menu", "cake", "food"]),
            ("surprise_plan", "Surprise Plan", "Design a practical surprise celebration plan.", ["surprise", "secret"]),
            ("schedule_reminder", "Schedule Reminder", "Simulate a reminder without creating one.", ["reminder", "remind", "schedule reminder"]),
        ],
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
        "extra_skills": [
            ("create_outline", "Create Outline", "Structure a write-up into a clear outline.", ["outline", "structure"]),
            ("content_calendar", "Content Calendar", "Plan a publishing calendar across channels and dates.", ["calendar", "schedule", "publishing"]),
            ("headline_and_hook", "Headline and Hook", "Draft headlines and opening hooks.", ["headline", "hook", "title"]),
            ("seo_keywords", "SEO Keywords", "Suggest search keywords and related phrases.", ["seo", "keywords", "search terms"]),
            ("repurpose_content", "Repurpose Content", "Adapt source content for other formats and channels.", ["repurpose", "adapt", "turn into"]),
            ("tone_rewrite", "Tone Rewrite", "Rewrite text in the requested tone while preserving meaning.", ["tone", "rewrite", "style"]),
            ("proofread_and_improve", "Proofread and Improve", "Correct grammar and improve clarity.", ["proofread", "grammar", "improve", "edit"]),
        ],
    },
}

# Lite models pehle (tez); fail ya timeout par agla model
MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash")


def skill_catalog(cfg: dict) -> list[dict]:
    """Return card skill data with examples while preserving the original skill first."""
    sid, name, description, tags = cfg["skill"]
    catalog = [{"id": sid, "name": name, "description": description, "tags": tags,
                "examples": [f"Help me with {name.lower()}", f"Create a plan using {name.lower()}"]}]
    for sid, name, description, tags in cfg["extra_skills"]:
        catalog.append({"id": sid, "name": name, "description": description, "tags": tags,
                        "examples": [f"Help me with {name.lower()}", f"Create a plan using {name.lower()}"]})
    return catalog


async def select_skill_id(llm, cfg: dict, user_text: str) -> str:
    """Use Gemini to select a skill id, then fall back to matching its keywords."""
    skills = skill_catalog(cfg)
    skill_ids = [skill["id"] for skill in skills]
    try:
        response = await asyncio.wait_for(llm.aio.models.generate_content(
            model=MODELS[0], contents=user_text,
            config=types.GenerateContentConfig(system_instruction="Choose one skill id and reply with only its id: " + ", ".join(skill_ids)),
        ), timeout=10)
        candidate = (response.text or "").strip().strip("` \"'")
        if candidate in skill_ids:
            return candidate
    except Exception:
        pass
    lowered = user_text.lower()
    ranked = [(sum(tag.lower() in lowered for tag in skill["tags"]), skill["id"]) for skill in skills[1:]]
    score, skill_id = max(ranked, default=(0, skill_ids[0]))
    return skill_id if score else skill_ids[0]


class LLMExecutor(AgentExecutor):
    """Har agent apna jawab Gemini se banata hai (agent ki role instruction ke saath)."""

    def __init__(self, role: str, fallback: str, cfg: dict):
        self.role = role          # is agent ki zimmedari (system prompt)
        self.fallback = fallback  # LLM fail ho to fixed jawab
        self.cfg = cfg
        self.llm = genai.Client()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = context.get_user_input()  # client ka subtask
        selected_skill = await select_skill_id(self.llm, self.cfg, user_text)
        write_scopes = {"book_trip": "travel.write", "schedule_reminder": "birthday.write"}
        if selected_skill in write_scopes:
            scope_state = context.call_context.state if context.call_context else {}
            scopes = set(scope_state.get("iam_scopes", []))
            if os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"} and write_scopes[selected_skill] not in scopes:
                reply = "This action requires write permission for your role."
            else:
                reference = f"{selected_skill.upper()}-{uuid4().hex[:8].upper()}"
                reply = f"SIMULATED: {selected_skill.replace('_', ' ').title()} confirmation. Reference: {reference}"
            await event_queue.enqueue_event(new_agent_text_message(reply))
            return
        reply = self.fallback
        for model in MODELS:
            try:
                # wait_for: atka hua call 15s baad cancel, taake agent hang na ho
                resp = await asyncio.wait_for(
                    self.llm.aio.models.generate_content(
                        model=model,
                        contents=user_text,
                        config=types.GenerateContentConfig(system_instruction=f"{self.role} Selected skill: {selected_skill}."),
                    ),
                    timeout=15,
                )
                reply = resp.text.strip()
                break
            except Exception:
                continue  # agla model try karo; sab fail hon to fallback jawab
        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Cancel support nahi hai
        raise Exception("cancel not supported")


def build_card(cfg: dict) -> AgentCard:
    """Config se Agent Card banata hai (discovery ke liye)."""
    sid, sname, sdesc, tags = cfg["skill"]
    skills = [AgentSkill(id=item["id"], name=item["name"], description=item["description"], tags=item["tags"], examples=item["examples"]) for item in skill_catalog(cfg)]
    return AgentCard(
        name=cfg["name"],
        description=sdesc,
        url=f"http://localhost:{cfg['port']}/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=skills,
        security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}} if os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"} else None,
        security=[{"bearerAuth": []}] if os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"} else None,
    )


def main() -> None:
    """Command-line argument se agent chunta hai aur server chalata hai."""
    key = sys.argv[1]
    cfg = AGENTS[key]
    handler = DefaultRequestHandler(
        agent_executor=LLMExecutor(cfg["role"], cfg["reply"], cfg),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(agent_card=build_card(cfg), http_handler=handler, context_builder=JWTCallContextBuilder()).build()
    # Is agent par sirf apne scope wala token chalega
    app.add_middleware(JWTGuard, required_scope=cfg["scope"], audience=f"{key}-planner")
    uvicorn.run(app, host="0.0.0.0", port=cfg["port"])


if __name__ == "__main__":
    main()
