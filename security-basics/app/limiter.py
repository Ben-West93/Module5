# exercises/security-basics/app/limiter.py
# L10 — The SlowAPI limiter
#
# It lives in its own module because both main.py (which registers it and
# handles its 429s) and the routers (which decorate endpoints with it) need
# it, and main.py imports the routers.

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    # The client is the socket's peer address. X-Forwarded-For is ignored,
    # because any client can put any value in it and would get a fresh
    # budget per request. Behind a real proxy, run uvicorn with
    # --proxy-headers --forwarded-allow-ips=<proxy address>.
    key_func=get_remote_address,
    # "moving-window" rather than SlowAPI's default fixed window. A fixed
    # window resets on the minute, so 5 logins at 0:59 and 5 more at 1:00 are
    # all allowed: 10 guesses in two seconds. A moving window never allows
    # more than the limit in ANY 60-second span.
    strategy="moving-window",
    # One count per endpoint function. The default ("url") keys on the path,
    # which is the same "/items" for both GET and POST.
    key_style="endpoint",
    storage_uri="memory://",
    # Not SlowAPI's own header injection: it requires a `response` argument
    # on every endpoint and adds Retry-After even to successful responses.
    # main.py adds the headers instead; see add_rate_limit_headers().
    headers_enabled=False,
)
