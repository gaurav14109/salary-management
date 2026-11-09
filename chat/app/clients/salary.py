"""The only way this service reaches salary data.

Every call carries the asking user's own claims, so the salary service applies the
same role check it applies to a request from the UI. The chat service therefore adds
no new data-access path: anything the assistant can read, the person asking could
have read by clicking, and anything they could not, it cannot.

It also carries the gateway key, because the salary API refuses anything that did
not come from inside the trust boundary.
"""

from __future__ import annotations

import logging

import httpx

from app.access import Principal
from app.config import get_settings
from app.errors import UpstreamUnavailable

log = logging.getLogger("chat.salary")

TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class SalaryClient:
    def __init__(self, *, base_url: str | None = None, http: httpx.Client | None = None) -> None:
        settings = get_settings()
        self._base = (base_url or settings.salary_api_url).rstrip("/")
        self._key = settings.gateway_key
        self._http = http or httpx.Client(timeout=TIMEOUT)

    def get(self, path: str, params: dict, *, caller: Principal) -> dict:
        headers = {
            "X-Gateway-Key": self._key,
            "X-User-Sub": caller.sub,
            "X-User-Roles": ",".join(sorted(caller.roles)),
        }
        if caller.display_name:
            headers["X-User-Name"] = caller.display_name
        if caller.email:
            headers["X-User-Email"] = caller.email

        try:
            response = self._http.get(f"{self._base}{path}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("salary_unreachable", extra={"path": path, "error": str(exc)})
            raise UpstreamUnavailable() from exc

        if response.status_code >= 400:
            # Including 403: the user asked for something their role does not allow,
            # and the tool reports that rather than pretending the data is missing.
            log.warning("salary_refused", extra={"path": path, "status": response.status_code})
            raise UpstreamUnavailable(
                f"The salary service returned {response.status_code} for {path}."
            )

        return response.json()
