"""The Case Copilot's PayPal tools: PayPal Agent Toolkit (LangChain integration), read-only.

Sources (confirmed against paypal-agent-toolkit 1.11.0, `shared/tools.py`):
- Quickstart: https://docs.paypal.ai/developer/tools/ai/agent-toolkit-quickstart
- Tool reference: https://docs.paypal.ai/developer/tools/ai/agent-tools-reference
- Package: https://pypi.org/project/paypal-agent-toolkit/

The toolkit decides which tools exist from `Configuration(actions=...)`. We enable only the
read actions below and, on top of that, `assert_read_only` refuses to let the app start if the
resulting tool set is anything other than `READ_ONLY_TOOLS` (CLAUDE.md rule 3). The toolkit's
write tools (create_order, pay_order, accept_dispute_claim, create/update_shipment_tracking,
the invoice tools, ...) are never configured and can never be reached by the model.
"""

from collections.abc import Iterable
from typing import Any

from app.config import SANDBOX_BASE_URL, Settings

# Names as the toolkit defines them. Note `get_order_details`, not `get_order`.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "get_order_details",
        "list_disputes",
        "get_dispute",
        "get_shipment_tracking",
        "list_transactions",
    }
)

# Toolkit action keys, one per tool above.
READ_ONLY_ACTIONS: dict[str, dict[str, bool]] = {
    "orders": {"get": True},
    "disputes": {"list": True, "get": True},
    "shipment": {"get": True},
    "transactions": {"list": True},
}


class ReadOnlyViolation(RuntimeError):
    """The Copilot's tool set is not exactly the read-only allowlist. The app must not start."""


def assert_read_only(tools: Iterable[Any]) -> None:
    names = {str(tool.name) for tool in tools}
    extra = sorted(names - READ_ONLY_TOOLS)
    missing = sorted(READ_ONLY_TOOLS - names)
    if extra or missing:
        raise ReadOnlyViolation(
            "Copilot tools must equal the read-only allowlist. "
            f"Not allowed: {extra or 'none'}. Missing: {missing or 'none'}."
        )


def build_toolkit_tools(settings: Settings) -> list[Any]:
    """The toolkit's LangChain tools for the allowlisted actions, in sandbox context only."""
    if (
        settings.paypal_base_url != SANDBOX_BASE_URL
    ):  # config already enforces this; belt and braces
        raise ReadOnlyViolation("The Copilot only runs against the PayPal sandbox.")
    # Imported here: the toolkit pulls in LangChain and takes a few seconds to import.
    from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
    from paypal_agent_toolkit.shared.configuration import Configuration, Context

    toolkit = PayPalToolkit(
        settings.paypal_client_id,
        settings.paypal_client_secret,
        Configuration(actions=READ_ONLY_ACTIONS, context=Context(sandbox=True)),
    )
    tools: list[Any] = list(toolkit.get_tools())
    assert_read_only(tools)
    return tools
