import os
import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

SECRET = "poc-secret-change-me"  # auth server wala hi secret
ISSUER = "a2a-poc-auth"
IAM_ISSUER = os.getenv("IAM_ISSUER", "a2a-poc-iam")
IAM_ENABLED = os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
IAM_JWKS = PyJWKClient(os.getenv("AUTH_URL", "http://localhost:9000") + "/.well-known/jwks.json")


class JWTGuard(BaseHTTPMiddleware):
    """Har request par Bearer JWT verify karta hai; Agent Card public rehta hai."""

    def __init__(self, app, required_scope: str, audience: str | None = None):
        """Configure legacy scope checks and optional IAM audience validation."""
        super().__init__(app)
        self.required_scope = required_scope  # is agent ke liye zaruri scope
        self.audience = audience

    async def dispatch(self, request, call_next):
        """Authenticate agent requests using the configured IAM or legacy token path."""
        # Discovery ke liye Agent Card bina token ke accessible
        if request.url.path.startswith("/.well-known/"):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"error": "missing_token"}, status_code=401)

        try:
            if IAM_ENABLED:
                signing_key = IAM_JWKS.get_signing_key_from_jwt(header[7:]).key
                claims = jwt.decode(
                    header[7:], signing_key, algorithms=["RS256"], issuer=IAM_ISSUER,
                    audience=self.audience,
                )
                prefix = self.audience.removesuffix("-planner").replace("-", "_")
                required = f"{prefix}.invoke"
                if required not in claims.get("scope", "").split():
                    return JSONResponse({"error": "insufficient_scope"}, status_code=403)
                return await call_next(request)
            # Signature, expiry aur issuer teeno check hote hain
            claims = jwt.decode(
                header[7:], SECRET, algorithms=["HS256"], issuer=ISSUER
            )
        except jwt.InvalidAudienceError:
            return JSONResponse({"error": "wrong_audience"}, status_code=403)
        except jwt.PyJWTError:
            return JSONResponse({"error": "invalid_token"}, status_code=401)

        # Scope check: token is agent ko call karne ki ijazat deta hai ya nahi
        if self.required_scope not in claims.get("scope", "").split():
            return JSONResponse({"error": "insufficient_scope"}, status_code=403)

        return await call_next(request)
