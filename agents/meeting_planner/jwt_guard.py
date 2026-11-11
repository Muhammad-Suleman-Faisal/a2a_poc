import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

SECRET = "poc-secret-change-me"  # auth server wala hi secret
ISSUER = "a2a-poc-auth"


class JWTGuard(BaseHTTPMiddleware):
    """Har request par Bearer JWT verify karta hai; Agent Card public rehta hai."""

    def __init__(self, app, required_scope: str):
        super().__init__(app)
        self.required_scope = required_scope  # is agent ke liye zaruri scope

    async def dispatch(self, request, call_next):
        # Discovery ke liye Agent Card bina token ke accessible
        if request.url.path.startswith("/.well-known/"):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"error": "missing_token"}, status_code=401)

        try:
            # Signature, expiry aur issuer teeno check hote hain
            claims = jwt.decode(
                header[7:], SECRET, algorithms=["HS256"], issuer=ISSUER
            )
        except jwt.PyJWTError:
            return JSONResponse({"error": "invalid_token"}, status_code=401)

        # Scope check: token is agent ko call karne ki ijazat deta hai ya nahi
        if self.required_scope not in claims.get("scope", "").split():
            return JSONResponse({"error": "insufficient_scope"}, status_code=403)

        return await call_next(request)