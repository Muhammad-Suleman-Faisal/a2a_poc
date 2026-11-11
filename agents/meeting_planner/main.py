import re

import uvicorn
from jwt_guard import JWTGuard
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message


class MeetingPlannerExecutor(AgentExecutor):
    """Agent ki asli logic. A2A SDK har incoming task par execute() call karta hai."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Client ka bheja hua text message nikalta hai
        user_text = context.get_user_input()

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
        examples=["Time plan for catching up with Ali at 5"],
    )
    return AgentCard(
        name="Meeting Planner Agent",
        description="Creates time plans and agendas for meetings.",
        url="http://localhost:9001/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )


def main() -> None:
    """Server assemble karke port 9001 par chalata hai."""
    handler = DefaultRequestHandler(
        agent_executor=MeetingPlannerExecutor(),
        task_store=InMemoryTaskStore(),  # tasks memory mein; POC ke liye kaafi
    )
    app = A2AStarletteApplication(agent_card=build_agent_card(), http_handler=handler)
    starlette_app = app.build()
    # Middleware: is agent par sirf "agent:meeting" scope wala token chalega
    starlette_app.add_middleware(JWTGuard, required_scope="agent:meeting")
    uvicorn.run(starlette_app, host="0.0.0.0", port=9001)


if __name__ == "__main__":
    main()