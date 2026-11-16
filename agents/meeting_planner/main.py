import asyncio
import os
import re
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv

load_dotenv()

from jwt_guard import JWTGuard
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message
from google import genai
from google.genai import types
from jwt_guard import JWTCallContextBuilder

MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash")

MEETING_SKILLS = [
    ("generate_agenda", "Generate Agenda", "Create a focused agenda for a meeting.", ["agenda", "topics"], ["Create an agenda for a project kickoff", "Draft topics for a team catch-up"]),
    ("meeting_briefing", "Meeting Briefing", "Prepare a concise briefing for an upcoming meeting.", ["briefing", "prepare", "background"], ["Prepare a briefing for my client meeting", "Summarize what I should know before the review"]),
    ("summarize_notes", "Summarize Notes", "Summarize meeting notes into key points.", ["summarize", "notes", "minutes"], ["Summarize these meeting notes", "Turn these minutes into key points"]),
    ("extract_action_items", "Extract Action Items", "Find owners and action items in meeting notes.", ["action items", "tasks", "owners"], ["Extract action items from these notes", "List tasks and owners from the minutes"]),
    ("draft_followup_email", "Draft Follow-up Email", "Draft a follow-up email based on a meeting.", ["follow-up", "email", "followup"], ["Draft a follow-up email after our review", "Write an email recap for meeting attendees"]),
    ("detect_conflicts", "Detect Conflicts", "Identify scheduling conflicts in the supplied meeting details.", ["conflict", "overlap", "availability"], ["Check for calendar conflicts at 3 pm", "Find overlaps in these meeting times"]),
    ("calendar_write", "Calendar Write", "Simulate adding a meeting to a calendar without changing a real calendar.", ["calendar", "schedule", "add meeting", "book meeting"], ["Add a meeting with Ali to my calendar", "Schedule a catch-up with Sara tomorrow"]),
]


async def select_meeting_skill_id(user_text: str) -> str:
    """Ask Gemini for a meeting skill id and use keyword matching if it fails."""
    skill_ids = [skill[0] for skill in MEETING_SKILLS]
    try:
        llm = genai.Client()
        response = await asyncio.wait_for(llm.aio.models.generate_content(
            model=MODELS[0], contents=user_text,
            config=types.GenerateContentConfig(system_instruction="Choose one skill id and reply only with its id: " + ", ".join(skill_ids)),
        ), timeout=10)
        candidate = (response.text or "").strip().strip("` \"'")
        if candidate in skill_ids:
            return candidate
    except Exception:
        pass
    lowered = user_text.lower()
    ranked = [(sum(tag in lowered for tag in skill[3]), skill[0]) for skill in MEETING_SKILLS]
    score, skill_id = max(ranked, default=(0, "plan_meeting"))
    return skill_id if score else "plan_meeting"


class MeetingPlannerExecutor(AgentExecutor):
    """Agent ki asli logic. A2A SDK har incoming task par execute() call karta hai."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Client ka bheja hua text message nikalta hai
        user_text = context.get_user_input()
        selected_skill = await select_meeting_skill_id(user_text)
        if selected_skill == "calendar_write":
            call_state = context.call_context.state if context.call_context else {}
            scopes = set(call_state.get("iam_scopes", []))
            if os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"} and "meeting.write" not in scopes:
                reply = "This action requires write permission for your role."
            else:
                reply = f"SIMULATED: Calendar event confirmation. Reference: CALENDAR-{uuid4().hex[:8].upper()}"
            await event_queue.enqueue_event(new_agent_text_message(reply))
            return

        # User ke text se time nikalta hai (jaise "7:00 pm" ya "at 5"); na mile to confirm maangta hai
        m = re.search(r"(\d{1,2}(?::\d{2})?\s?(?:am|pm))|at\s+(\d{1,2}(?::\d{2})?)", user_text, re.I)
        time = (m.group(1) or m.group(2)) if m else "to be confirmed"

        reply = (
            f"Meeting plan for: '{user_text}'\n"
            f"- Proposed time: {time}\n"
            "- Date and place: confirmation required\n"
            "- Suggested agenda: catch-up, updates, next steps"
        )

        # Jawab event queue mein dalta hai; SDK isay client tak pohnchata hai
        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Cancel support nahi hai, is liye explicit error
        raise Exception("cancel not supported")


def build_agent_card() -> AgentCard:
    """Agent Card: discovery ke liye public identity. Client isi ke skills se selection karega."""
    skill = AgentSkill(
        id="plan_meeting",
        name="Meeting Planner",
        description="Plans a catch-up or meeting: time slot, agenda, and items needing confirmation.",
        tags=["meeting", "schedule", "catch-up", "time plan"],
        examples=["Time plan for catching up with Ali at 5", "Arrange a project check-in next week"],
    )
    skills = [skill] + [AgentSkill(id=sid, name=name, description=description, tags=tags, examples=examples)
                        for sid, name, description, tags, examples in MEETING_SKILLS]
    return AgentCard(
        name="Meeting Planner Agent",
        description="Creates time plans and agendas for meetings.",
        url="http://localhost:9001/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=skills,
        security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}} if os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"} else None,
        security=[{"bearerAuth": []}] if os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"} else None,
    )


def main() -> None:
    """Server assemble karke port 9001 par chalata hai."""
    handler = DefaultRequestHandler(
        agent_executor=MeetingPlannerExecutor(),
        task_store=InMemoryTaskStore(),  # tasks memory mein; POC ke liye kaafi
    )
    app = A2AStarletteApplication(agent_card=build_agent_card(), http_handler=handler, context_builder=JWTCallContextBuilder())
    starlette_app = app.build()
    # Middleware: is agent par sirf "agent:meeting" scope wala token chalega
    starlette_app.add_middleware(JWTGuard, required_scope="agent:meeting", audience="meeting-planner")
    uvicorn.run(starlette_app, host="0.0.0.0", port=9001)


if __name__ == "__main__":
    main()
