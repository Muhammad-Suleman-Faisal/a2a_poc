"""Run IAM boundary checks against the six locally running services.

Credentials are read from .env and are never printed. Set IAM_ENABLED=true
and restart the services before running this script.
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

AUTH = "http://localhost:9000"
CLIENT = "http://localhost:8100"
EXPECTED_SKILLS = {
    "meeting": {"generate_agenda", "meeting_briefing", "summarize_notes", "extract_action_items", "draft_followup_email", "detect_conflicts", "calendar_write"},
    "travel": {"plan_itinerary", "estimate_budget", "packing_checklist", "route_and_transport", "best_season_advice", "visa_document_checklist", "compare_destinations", "book_trip"},
    "birthday": {"plan_party", "gift_suggestions", "party_budget", "guest_list_and_invites", "menu_and_cake_planner", "surprise_plan", "schedule_reminder"},
    "content": {"create_outline", "content_calendar", "headline_and_hook", "seo_keywords", "repurpose_content", "tone_rewrite", "proofread_and_improve"},
}


def chat(client: httpx.Client, token: str, prompt: str) -> dict:
    """Send one authenticated prompt to the live client agent and validate its response."""
    response = client.post(f"{CLIENT}/chat", json={"prompt": prompt}, headers={"Authorization": f"Bearer {token}"})
    require(response.status_code == 200, f"chat returned HTTP {response.status_code}")
    result = response.json()
    require("error" not in result, "chat returned an error response")
    return result


def assert_agent_completed(result: dict, agent: str) -> None:
    """Require a completed result from the expected routed agent."""
    item = next((row for row in result.get("agents", []) if row.get("agent") == agent), None)
    require(item is not None and item.get("status") == "completed", f"{agent} did not complete; status={item.get('status') if item else 'not selected'}")


def require(condition: bool, message: str) -> None:
    """Stop with a concise diagnostic when an end-to-end assertion fails."""
    if not condition:
        raise AssertionError(message)


def login(client: httpx.Client, prefix: str) -> dict:
    """Create a session using credentials configured for the requested role."""
    username = os.environ[f"IAM_{prefix}_USERNAME"]
    password = os.environ[f"IAM_{prefix}_PASSWORD"]
    response = client.post(f"{AUTH}/auth/login", json={"username": username, "password": password})
    require(response.status_code == 200, f"{prefix.lower()} login failed: HTTP {response.status_code}")
    return response.json()


def run() -> None:
    """Exercise login, refresh rotation, RBAC, audit access, and unauthenticated boundaries."""
    require(os.getenv("IAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}, "Set IAM_ENABLED=true and restart services first")
    with httpx.Client(timeout=300) as client:
        cards = client.get("http://localhost:9001/.well-known/agent-card.json")
        require(cards.status_code == 200, "meeting Agent Card should be public")
        for key, port in (("meeting", 9001), ("travel", 9002), ("birthday", 9003), ("content", 9004)):
            card_response = client.get(f"http://localhost:{port}/.well-known/agent-card.json")
            require(card_response.status_code == 200, f"{key} Agent Card should be public")
            card = card_response.json()
            skills = card.get("skills", [])
            expected_ids = EXPECTED_SKILLS[key]
            require(skills[0]["id"] in {"plan_meeting", "plan_trip", "plan_birthday", "outline_content"}, f"{key} original skill must remain first")
            require(expected_ids.issubset({skill["id"] for skill in skills}), f"{key} new skill ids are incomplete")
            require(all(len(skill.get("examples", [])) >= 2 for skill in skills), f"{key} skill examples missing")
            require(bool(card.get("securitySchemes")), f"{key} securitySchemes missing")
        protected_agent = client.post("http://localhost:9001/", json={})
        require(protected_agent.status_code == 401, "meeting A2A endpoint should reject a missing token")
        protected_chat = client.post(f"{CLIENT}/chat", json={"prompt": "hello"})
        require(protected_chat.status_code == 401, "client /chat should reject a missing token")

        user_session = login(client, "USER")
        user_headers = {"Authorization": f"Bearer {user_session['access_token']}"}
        user_audit = client.get(f"{CLIENT}/audit", headers=user_headers)
        require(user_audit.status_code == 403, "non-admin user should not read audit records")

        for prompt, agent in (("Plan a two day itinerary for Hunza", "travel"),
                              ("Plan a meeting with Ali at 5 pm", "meeting"),
                              ("Suggest a birthday party theme for a child", "birthday"),
                              ("Create an outline for a birthday greeting", "content")):
            assert_agent_completed(chat(client, user_session["access_token"], prompt), agent)

        refresh_token = user_session["refresh_token"]
        rotated = client.post(f"{AUTH}/auth/refresh", json={"refresh_token": refresh_token})
        require(rotated.status_code == 200, "valid refresh token should rotate")
        replay = client.post(f"{AUTH}/auth/refresh", json={"refresh_token": refresh_token})
        require(replay.status_code == 401, "rotated refresh token should not be reusable")
        client.post(f"{AUTH}/auth/logout", json={"refresh_token": rotated.json()["refresh_token"]})

        admin_session = login(client, "ADMIN")
        admin_headers = {"Authorization": f"Bearer {admin_session['access_token']}"}
        admin_audit = client.get(f"{CLIENT}/audit?limit=5", headers=admin_headers)
        require(admin_audit.status_code == 200, "admin should read audit records")
        require(isinstance(admin_audit.json().get("events"), list), "audit response should contain events")
        admin_booking = chat(client, admin_session["access_token"], "book a trip to Hunza")
        travel_result = next((item for item in admin_booking.get("agents", []) if item.get("agent") == "travel"), None)
        require(travel_result and travel_result["status"] == "completed" and travel_result["text"].startswith("SIMULATED:") and "TRIP-" in travel_result["text"], "admin book_trip should return a simulated confirmation")
        user_booking = chat(client, user_session["access_token"], "book a trip to Hunza")
        travel_result = next((item for item in user_booking.get("agents", []) if item.get("agent") == "travel"), None)
        require(travel_result and travel_result["status"] == "completed" and travel_result["text"] == "This action requires write permission for your role.", "user book_trip should return completed write-permission text")
        compound = chat(client, user_session["access_token"], "Plan a two day trip to Hunza and a meeting with Ali at 5 pm")
        require(compound.get("summary") == "2/2 agents completed", "compound trip and meeting prompt should complete 2/2 agents")

    print("IAM e2e checks passed: cards, skill metadata, normal routing, write stubs, compound request, auth and audit boundaries.")


if __name__ == "__main__":
    run()
