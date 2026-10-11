import json
import logging
import sys

from django.conf import settings

logger = logging.getLogger("api_debug")


class ApiDebugLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        enabled = settings.DEBUG and getattr(settings, "DEBUG_LOG_API", False)

        if enabled and request.path.startswith("/api/"):
            self._log_request(request)

        response = self.get_response(request)

        if enabled and request.path.startswith("/api/"):
            self._log_response(request, response)

        return response

    def _log_request(self, request):
        meta = {
            "method": request.method,
            "path": request.path,
            "query": request.META.get("QUERY_STRING", ""),
            "remote_addr": request.META.get("REMOTE_ADDR", ""),
            "content_type": request.content_type or "",
            "content_length": request.META.get("CONTENT_LENGTH", "0"),
        }

        body = ""
        content_type = request.content_type or ""

        if request.method in ("POST", "PUT", "PATCH"):
            if "application/json" in content_type:
                try:
                    body = request.body.decode("utf-8")
                    parsed = json.loads(body)
                    body = json.dumps(parsed, ensure_ascii=False, indent=2)
                except Exception:
                    body = request.body.decode("utf-8", errors="replace")
            elif "multipart/form-data" in content_type:
                body_parts = []
                for key, value in request.POST.items():
                    body_parts.append(f"    {key}: {value}")
                for key in request.FILES:
                    f = request.FILES[key]
                    body_parts.append(f"    {key}: <file: {f.name} ({f.size} bytes)>")
                body = "\n".join(body_parts) if body_parts else "(files uploaded)"
            elif "application/x-www-form-urlencoded" in content_type:
                body = "\n".join(
                    f"    {k}: {v}" for k, v in request.POST.items()
                )
            else:
                try:
                    body = request.body.decode("utf-8")
                except Exception:
                    body = f"<binary: {request.META.get('CONTENT_LENGTH', '?')} bytes>"

        cookie_header = request.META.get("HTTP_COOKIE", "")
        has_session = "sessionid" in cookie_header
        lines = [
            "",
            "=" * 72,
            f">>> REQUEST  {meta['method']} {meta['path']}",
            f"    Remote:  {meta['remote_addr']}",
            f"    Query:   {meta['query']}" if meta["query"] else "",
            f"    Content-Type: {meta['content_type']}",
            f"    Content-Length: {meta['content_length']}",
            f"    Cookie:  {'sessionid=***' if has_session else '(none)'}",
        ]
        if body:
            lines.append(f"    Body:")
            for bline in body.split("\n"):
                lines.append(f"      {bline}")

        lines.append("-" * 72)

        for line in lines:
            if line:
                self._print(line)

    def _log_response(self, request, response):
        body_preview = ""
        content_type = response.get("Content-Type", "")
        if "application/json" in content_type and hasattr(response, "content"):
            try:
                body = response.content.decode("utf-8")
                if len(body) > 2000:
                    body_preview = body[:2000] + "...(truncated)"
                else:
                    body_preview = body
            except Exception:
                body_preview = "<binary content>"

        lines = [
            f"<<< RESPONSE  {request.method} {request.path}  ->  {response.status_code}",
            f"    Content-Type: {content_type}",
        ]
        for key, value in response.items():
            if key.lower() == "set-cookie":
                lines.append(f"    Set-Cookie: {value.split(';')[0]};...")
            elif key.lower().startswith("access-control"):
                lines.append(f"    {key}: {value}")
        if hasattr(response, "cookies"):
            for key, morsel in response.cookies.items():
                lines.append(f"    Set-Cookie: {key}=***;...")
        if body_preview:
            lines.append(f"    Body: {body_preview}")

        if request.COOKIES:
            session_id = request.COOKIES.get("sessionid", "")
            lines.append(f"    DEBUG sessionid in request.COOKIES: {'YES:' + session_id[:8] + '...' if session_id else 'NONE'}")

        lines.append("=" * 72)

        for line in lines:
            self._print(line)

    def _print(self, message):
        print(message, file=sys.stderr, flush=True)
