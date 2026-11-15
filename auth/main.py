import os
import logging
import secrets
import sys
from pathlib import Path
import time
import uuid

import jwt
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, taake `auth.*` imports script run par bhi chalen
from auth.jwks import jwks_document

load_dotenv()

SECRET = "poc-secret-change-me"  # POC: shared secret; remote agents isi se verify karenge
ISSUER = "a2a-poc-auth"
TOKEN_TTL = 900  # token ki umar: 15 minute
IAM_ENABLED = os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

# Registered clients: client_id -> (secret, allowed scopes)
CLIENTS = {
    "dashboard-client": (
        "client-secret",
        {"agent:meeting", "agent:travel", "agent:birthday", "agent:content"},
    )
}

app = FastAPI()
logger = logging.getLogger(__name__)
if IAM_ENABLED:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("DASHBOARD_ORIGIN", "http://localhost:5173")],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )
else:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

if IAM_ENABLED:
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from auth.iam import initialize_iam, router as iam_router
    from auth import iam as iam_mod, rbac  # ADJUST: exchange ke helpers yahin se aate hain
    from audit.audit import write_event as write_audit_event

    initialize_iam()

    EXCHANGE_TTL = 120

    @app.get("/.well-known/jwks.json")
    def jwks():
        """Expose the current public JWKS for IAM token verification."""
        return jwks_document()

    app.include_router(iam_router)

    class ExchangeRequest(BaseModel):
        target_agent: str  # e.g. "travel-planner"

    @app.post("/auth/token/exchange")
    def token_exchange(
        body: ExchangeRequest,
        authorization: str = Header(None),
        x_client_id: str = Header(None),
        x_client_secret: str = Header(None),
        x_correlation_id: str = Header(None),
    ):
        """User token -> per-agent short-lived token. Do cheezein lazmi hain:
        valid user token AND client-agent ki apni credentials, taake sirf chori hua
        user token kaafi na ho."""
        # 1) Client agent authenticate (constant-time compare; env missing ho to fail-closed)
        exp_id = os.getenv("CLIENT_AGENT_ID", "")
        exp_secret = os.getenv("CLIENT_AGENT_SECRET", "")
        if not (exp_secret and x_client_id and x_client_secret
                and secrets.compare_digest(x_client_id.encode(), exp_id.encode())
                and secrets.compare_digest(x_client_secret.encode(), exp_secret.encode())):
            raise HTTPException(401, "invalid client credentials")

        # 2) User token verify (signature/exp), agent token exchange nahi ho sakta
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing user token")
        try:
            claims = iam_mod.decode_access_token(authorization[7:])  # ADJUST
        except Exception:
            raise HTTPException(401, "invalid or expired user token")
        if "act" in claims:
            raise HTTPException(401, "agent tokens cannot be exchanged")
        if iam_mod.is_revoked(claims["jti"]):  # ADJUST
            raise HTTPException(401, "token revoked")

        # 3) User abhi active hai? Role DB se aata hai, token se nahi (role badle to turant asar)
        user = iam_mod.get_user(claims["sub"])  # ADJUST
        if not user or not user["active"]:
            raise HTTPException(401, "user inactive")

        # 4) RBAC: sirf us agent ke woh scopes jo is role ko milte hain
        if body.target_agent not in rbac.AGENT_PREFIX:
            raise HTTPException(400, "unknown agent")
        scopes = rbac.scopes_for_agent(list(rbac.scopes_for_role(user["role"])), body.target_agent)
        if not scopes:
            logger.info("exchange deny subject=%s agent=%s correlation_id=%s", user["username"], body.target_agent, x_correlation_id)
            write_audit_event("auth", cid=x_correlation_id, actor=user["username"], role=user["role"],
                              event="exchange", agent=body.target_agent, scope=" ".join(scopes),
                              decision="deny", status=403, reason="not allowed for your role")
            raise HTTPException(403, "not allowed for your role")

        # 5) Agent token issue: sub=user, act=client-agent, aud=target agent
        now = int(time.time())
        agent_claims = {
            "iss": iam_mod.ISSUER,
            "sub": user["username"],
            "act": {"sub": "client-agent"},
            "aud": body.target_agent,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + EXCHANGE_TTL,
            "jti": str(uuid.uuid4()),
        }
        logger.info("exchange allow subject=%s agent=%s scopes=%s correlation_id=%s", user["username"], body.target_agent, scopes, x_correlation_id)
        write_audit_event("auth", cid=x_correlation_id, actor=user["username"], role=user["role"],
                          event="exchange", agent=body.target_agent, scope=" ".join(scopes),
                          decision="allow", status=200)
        return {"access_token": iam_mod.sign_token(agent_claims),
                "token_type": "Bearer", "expires_in": EXCHANGE_TTL}


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
    print(f"[AUTH] requested={scope!r} granted={granted}")
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
