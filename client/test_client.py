import asyncio
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest

AGENT_URL = "http://localhost:9001"

async def get_token(http: httpx.AsyncClient) -> str:
    """Auth server se client_credentials token leta hai."""
    r = await http.post(
        "http://localhost:9000/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "dashboard-client",
            "client_secret": "client-secret",
            "scope": "agent:meeting",
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]

async def fetch_card(http: httpx.AsyncClient):
    """Agent ka Card fetch karta hai (discovery step)."""
    resolver = A2ACardResolver(httpx_client=http, base_url=AGENT_URL)
    return await resolver.get_agent_card()


async def send_task(http: httpx.AsyncClient, card, text: str):
    """Card ke mutabiq agent ko A2A message bhejta hai aur response return karta hai."""
    client = A2AClient(httpx_client=http, agent_card=card)
    message = {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "messageId": uuid4().hex,  # har message ki unique id
    }
    request = SendMessageRequest(
        id=str(uuid4()), params=MessageSendParams(message=message)
    )
    return await client.send_message(request)


async def main():
    """Card fetch -> skills print -> task bhejo -> jawab print."""
    async with httpx.AsyncClient(timeout=30) as http:
        card = await fetch_card(http)
        print("Agent:", card.name, "| Skills:", [s.name for s in card.skills])

        token = await get_token(http)
        http.headers["Authorization"] = f"Bearer {token}"  # har request par token

        response = await send_task(http, card, "Time plan for catching up with Ali at 5")
        print(response.model_dump(mode="json", exclude_none=True))


if __name__ == "__main__":
    asyncio.run(main())