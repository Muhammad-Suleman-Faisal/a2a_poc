import time

import jwt
import uvicorn
from fastapi import FastAPI, Form, HTTPException

SECRET = "poc-secret-change-me"  # POC: shared secret; remote agents isi se verify karenge
ISSUER = "a2a-poc-auth"
TOKEN_TTL = 900  # token ki umar: 15 minute

# Registered clients: client_id -> (secret, allowed scopes)
CLIENTS = {
    "dashboard-client": (
        "client-secret",
        {"agent:meeting", "agent:travel", "agent:birthday", "agent:content"},
    )
}

app = FastAPI()


@app.post("/token")
def issue_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: str = Form(""),
):
    """OAuth2 client_credentials: credentials verify karke scoped JWT deta hai."""
    if grant_type != "client_credentials":
        raise HTTPException(400, "unsupported_grant_type")

    client = CLIENTS.get(client_id)
    if not client or client[0] != client_secret:
        raise HTTPException(401, "invalid_client")

    # Sirf wahi scopes milenge jo client ko allowed hain
    granted = [s for s in scope.split() if s in client[1]]
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": client_id,
        "scope": " ".join(granted),
        "iat": now,
        "exp": now + TOKEN_TTL,
    }
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    return {"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_TTL}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)