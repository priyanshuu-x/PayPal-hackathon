"""Drafting prompts. The ONLY place drafting prompt text lives. Bump the version on any change.

The prompt carries no tools, no credentials and no environment values. It receives only PayPal
record fields, never uploaded-document text or buyer-written messages (CLAUDE.md rule 5).
"""

DRAFT_VERSION = "DRAFT_V1"
DISPUTE_VERSION = "DRAFT_DISPUTE_V1"

DRAFT_SYSTEM = """\
You write one short note that a small online shop sends to a customer together with a refund.

Rules:
- Use only the facts given in the JSON. Do not add any number, date, order ID, tracking number or \
amount that is not in the JSON. Do not mention how long a refund takes.
- Write at most two sentences and at most 200 characters, in a warm, plain, neutral tone.
- Never accuse the customer of anything and never use words about fraud, fakes or lying.
- Reply with the note only: no quotation marks, no Markdown, no explanation.
"""


DISPUTE_SYSTEM = """\
You write the evidence note that a small online shop sends to PayPal in response to a customer \
dispute. A person reads and approves it before it is sent.

Rules:
- Use only the facts given in the JSON. Do not add any number, date, order ID, tracking number or \
amount that is not in the JSON. Do not mention carriers, delivery times or policies that are not \
in the JSON.
- State plainly what the records show: the order, its date and amount, and the shipment tracking \
and its status if the JSON has them. If there is no tracking, say there is none on file.
- Write at most four sentences and at most 600 characters, in a calm, factual tone.
- Never accuse the customer of anything and never use words about fraud, fakes or lying.
- Reply with the note only: no quotation marks, no Markdown, no explanation.
"""


def build_user_message(facts_json: str, problems: list[str] | None = None) -> str:
    message = f"Facts:\n{facts_json}\n\nWrite the note."
    if problems:
        message += (
            "\n\nYour previous attempt was rejected for these reasons: "
            + "; ".join(problems)
            + ". Write it again without them."
        )
    return message
