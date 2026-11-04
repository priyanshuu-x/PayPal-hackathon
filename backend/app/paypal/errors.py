"""Errors raised by the PayPal client. Bodies stay server-side; callers return a generic message."""

from typing import Any

from app.security.redact import redact


class PayPalError(Exception):
    """A non-2xx PayPal response or a transport failure.

    `details` is already redacted and is for logs only. Never send it to the browser.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        name: str | None = None,
        debug_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.name = name
        self.debug_id = debug_id
        self.details = redact(details)

    @property
    def is_client_error(self) -> bool:
        return self.status_code is not None and 400 <= self.status_code < 500
