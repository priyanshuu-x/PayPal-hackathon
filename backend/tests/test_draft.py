import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from app.config import Settings
from app.drafting.draft import draft_refund_note, facts_from_snapshot, prompt_facts
from app.drafting.grounding import GroundingFacts, GroundingItem
from app.drafting.prompts import DRAFT_SYSTEM, DRAFT_VERSION
from app.drafting.templates import TEMPLATE_VERSION
from app.llm.groq_client import GROQ_BASE_URL, GroqClient
from tests.conftest import FAKE_GROQ_KEY

ORDER = "5O190127TN364715T"
MODEL = "openai/gpt-oss-120b"
STORE = "Lotus & Loom Demo Store"
GOOD_NOTE = (
    f"Your refund of 52.99 USD for order {ORDER} is on its way. Thank you for shopping with us."
)


def facts() -> GroundingFacts:
    return GroundingFacts(
        order_id=ORDER,
        amount=Decimal("52.99"),
        currency="USD",
        order_date=date(2026, 11, 3),
        capture_ids=("3GG279541U471931P",),
        items=(GroundingItem("Lotus Linen Tote", 2, Decimal("24.00")),),
    )


def note_reply(text: str | None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.fixture
def groq():  # type: ignore[no-untyped-def]
    async def no_sleep(_: float) -> None:
        return None

    settings = Settings(_env_file=None, groq_api_key=FAKE_GROQ_KEY)
    with respx.mock(base_url=GROQ_BASE_URL, assert_all_called=False) as router:
        yield GroqClient(settings, sleep=no_sleep), router.post("/chat/completions")


async def _draft(client: GroqClient, budget: int = 99):  # type: ignore[no-untyped-def]
    remaining = [budget]

    def consume() -> bool:
        remaining[0] -= 1
        return remaining[0] >= 0

    return await draft_refund_note(
        client, model=MODEL, facts=facts(), store_name=STORE, consume_budget=consume
    )


async def test_a_grounded_reply_is_used_as_written(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(return_value=note_reply(GOOD_NOTE))
    result = await _draft(client)
    assert (result.text, result.grounded) == (GOOD_NOTE, True)
    assert (result.prompt_version, result.model_id) == (DRAFT_VERSION, MODEL)
    assert route.call_count == 1


async def test_the_prompt_carries_record_facts_only(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(return_value=note_reply(GOOD_NOTE))
    await _draft(client)
    sent = json.loads(route.calls.last.request.content)
    assert "tools" not in sent and "response_format" not in sent and sent["temperature"] == 0
    system, user = sent["messages"]
    assert system["content"] == DRAFT_SYSTEM
    body = json.loads(user["content"].split("Facts:\n")[1].split("\n\nWrite")[0])
    assert set(body) == {
        "store_name", "order_id", "refund_amount", "currency", "order_date", "items",
    }  # fmt: skip
    assert body["items"] == [{"name": "Lotus Linen Tote", "quantity": 2}]
    everything = json.dumps(sent)
    for forbidden in (FAKE_GROQ_KEY, "buyer@", "Bearer", "client_secret"):
        assert forbidden not in everything


def test_prompt_facts_never_include_buyer_details_or_ids_of_payments() -> None:
    text = prompt_facts(facts(), STORE)
    assert "3GG279541U471931P" not in text and "@" not in text


async def test_an_invented_number_is_rejected_and_fed_back(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(
        side_effect=[
            note_reply("Your refund of 52.99 USD arrives in 5 business days."),
            note_reply(GOOD_NOTE),
        ]
    )
    result = await _draft(client)
    assert result.text == GOOD_NOTE and result.model_id == MODEL
    second = json.loads(route.calls[1].request.content)["messages"][1]["content"]
    assert "'5' is not in the facts" in second and "rejected" in second
    assert route.call_count == 2


async def test_accusing_wording_is_rejected(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(
        side_effect=[note_reply("We refunded you despite the fake receipt."), note_reply(GOOD_NOTE)]
    )
    result = await _draft(client)
    assert result.text == GOOD_NOTE
    assert "fake" in json.loads(route.calls[1].request.content)["messages"][1]["content"]


async def test_three_bad_tries_fall_back_to_the_template(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(return_value=note_reply("Refund of $1.00 for order ZZ99ZZ99ZZ."))
    result = await _draft(client)
    assert route.call_count == 3
    assert result.prompt_version == TEMPLATE_VERSION and result.model_id is None
    assert result.grounded and "52.99 USD" in result.text and ORDER in result.text


async def test_an_empty_reply_counts_as_a_failed_try(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(
        side_effect=[note_reply(""), note_reply(None), note_reply("   "), note_reply(GOOD_NOTE)]
    )
    result = await _draft(client)
    assert result.prompt_version == TEMPLATE_VERSION  # three empties used up the tries
    assert "empty" in json.loads(route.calls[1].request.content)["messages"][1]["content"]


async def test_think_blocks_quotes_and_line_breaks_are_cleaned(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(return_value=note_reply(f'<think>hmm</think>\n"{GOOD_NOTE.replace(". ", chr(10))}"'))
    result = await _draft(client)
    assert result.text == GOOD_NOTE.replace(". ", " ") and result.model_id == MODEL


async def test_a_model_outage_falls_back_to_the_template(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(return_value=httpx.Response(400, json={"error": "bad"}))
    result = await _draft(client)
    assert route.call_count == 1 and result.prompt_version == TEMPLATE_VERSION


async def test_the_daily_cap_is_respected_for_every_try(groq) -> None:  # type: ignore[no-untyped-def]
    client, route = groq
    route.mock(return_value=note_reply("Refund of $1.00."))
    result = await _draft(client, budget=0)
    assert route.call_count == 0 and result.prompt_version == TEMPLATE_VERSION
    result = await _draft(client, budget=2)
    assert route.call_count == 2 and result.prompt_version == TEMPLATE_VERSION


SNAPSHOT = {
    "order_id": ORDER,
    "amount": "52.99",
    "currency": "USD",
    "created": "2026-11-03T10:15:00Z",
    "items": [{"name": "Lotus Linen Tote", "quantity": 2, "unit_price": "24.00"}],
    "tracking_number": "1Z999",
}


def test_facts_from_a_snapshot() -> None:
    built = facts_from_snapshot(SNAPSHOT, "CAP-1")
    assert built is not None
    assert (built.order_id, built.amount, built.order_date) == (
        ORDER,
        Decimal("52.99"),
        date(2026, 11, 3),
    )
    assert built.capture_ids == ("CAP-1",) and built.tracking_number == "1Z999"
    assert built.items[0].unit_price == Decimal("24.00")
    assert facts_from_snapshot(SNAPSHOT, None).capture_ids == ()  # type: ignore[union-attr]
    offset = facts_from_snapshot({**SNAPSHOT, "created": "2026-11-03T22:00:00-08:00"}, None)
    assert offset is not None and offset.order_date == date(2026, 11, 4)  # UTC date
    naive = facts_from_snapshot({**SNAPSHOT, "created": "2026-11-03T10:15:00"}, None)
    assert naive is not None and naive.order_date == date(2026, 11, 3)
    no_items = {k: v for k, v in SNAPSHOT.items() if k != "items"}
    assert facts_from_snapshot(no_items, None).items == ()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {**SNAPSHOT, "amount": "abc"},
        {**SNAPSHOT, "created": "yesterday"},
        {**SNAPSHOT, "items": [{"name": "x"}]},
    ],
)
def test_a_damaged_snapshot_gives_no_facts(broken: dict[str, object]) -> None:
    assert facts_from_snapshot(broken, "CAP-1") is None
