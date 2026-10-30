"""Low-level HTTP protections, written as plain ASGI middleware.

BodySizeLimit counts bytes AS THEY ARRIVE, so a client cannot bypass the limit by omitting
Content-Length (chunked transfer encoding): the request fails as soon as the running total
exceeds the cap, before the whole body is buffered.

SecurityHeaders adds standard browser hardening headers to every response.
"""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

TOO_LARGE = "request body too large"


class BodySizeLimit:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised while FastAPI reads the body; its exception handling turns it into a 413.
                    raise HTTPException(status_code=413, detail=TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)


class SameOriginWrites:
    """Refuse state-changing requests whose Origin header names another site.

    Browsers send Origin on cross-site POSTs, so this blocks cross-site form posts and
    "login CSRF" before any cookie is used. Requests without Origin (curl, server-to-server)
    pass; authenticated ones still need the per-session CSRF token.
    """

    UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app, extra_origins: tuple[str, ...] = ()):
        self.app = app
        self.extra_origins = {o.rstrip("/").lower() for o in extra_origins}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in self.UNSAFE:
            headers = dict(scope.get("headers") or [])
            origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/").lower()
            host = headers.get(b"host", b"").decode("latin-1").lower()
            if origin and origin not in self.extra_origins and origin.split("://", 1)[-1] != host:
                response = JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Strict policy for the app itself: everything is served from our own origin.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# FastAPI's interactive docs load their UI from a CDN, so they keep FastAPI's defaults.
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        https = scope.get("scheme") == "https"  # set from X-Forwarded-Proto behind the platform proxy

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}

                def add(name: str, value: str):
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))

                add("x-content-type-options", "nosniff")
                add("referrer-policy", "strict-origin-when-cross-origin")
                add("x-frame-options", "DENY")
                add("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()")
                add("cross-origin-opener-policy", "same-origin")
                if not path.startswith(DOCS_PATHS):
                    add("content-security-policy", CONTENT_SECURITY_POLICY)
                if https:
                    add("strict-transport-security", "max-age=31536000; includeSubDomains")
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
