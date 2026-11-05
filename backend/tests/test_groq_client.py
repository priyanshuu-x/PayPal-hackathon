import json

import httpx
import pytest
import respx
from sqlmodel import Session

from app.config import Settings
from app.db.models import LlmUsage
from app.db.session import create_db_and_tables, make_engine
from app.llm.budget import try_consume
from app.llm.groq_client import GROQ_BASE_URL, GroqClient, LLMError
from tests.conftest import FAKE_GROQ_KEY


async def _no_sleep(_: float) -> None:
    return None


def _client(key: str = FAKE_GROQ_KEY) -> GroqClient:
    return GroqClient(Settings(_env_file=None, groq_api_key=key), sleep=_no_sleep)


def _ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.fixture
def groq() -> respx.MockRouter:
    with respx.mock(base_url=GROQ_BASE_URL, assert_all_called=False) as router:
        yield router


async def test_chat_sends_temperature_zero_json_mode_and_no_tools(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(return_value=_ok('{"a": 1}'))
    client = _client()
    text = await client.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert text == '{"a": 1}'
    sent = json.loads(route.calls.last.request.content)
    assert sent["temperature"] == 0
    assert sent["response_format"] == {"type": "json_object"}
    assert "tools" not in sent and "tool_choice" not in sent
    assert route.calls.last.request.headers["authorization"] == "Bearer " + FAKE_GROQ_KEY
    assert FAKE_GROQ_KEY not in route.calls.last.request.content.decode()
    await client.aclose()


async def test_plain_mode_omits_response_format(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(return_value=_ok("hello"))
    client = _client()
    await client.chat(model="m", messages=[], json_mode=False)
    assert "response_format" not in json.loads(route.calls.last.request.content)
    await client.aclose()


async def test_missing_key_fails_without_calling_the_network(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(return_value=_ok("x"))
    client = _client(key="")
    with pytest.raises(LLMError, match="not configured"):
        await client.chat(model="m", messages=[])
    assert route.call_count == 0
    await client.aclose()


async def test_retries_429_and_5xx_then_succeeds(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(503),
            httpx.Response(500, headers={"Retry-After": "soon"}),
            _ok("done"),
        ]
    )
    client = _client()
    assert await client.chat(model="m", messages=[]) == "done"
    assert route.call_count == 4
    await client.aclose()


async def test_gives_up_after_the_retry_budget(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(return_value=httpx.Response(429))
    client = _client()
    with pytest.raises(LLMError) as err:
        await client.chat(model="m", messages=[])
    assert route.call_count == 4 and err.value.status_code == 429
    await client.aclose()


async def test_other_4xx_is_not_retried_and_hides_the_body(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "secret detail gsk_abc"}})
    )
    client = _client()
    with pytest.raises(LLMError) as err:
        await client.chat(model="m", messages=[])
    assert route.call_count == 1
    assert "secret detail" not in str(err.value) and err.value.status_code == 400
    await client.aclose()


async def test_transport_errors_are_retried_then_raised(groq: respx.MockRouter) -> None:
    route = groq.post("/chat/completions").mock(side_effect=httpx.ConnectTimeout("slow"))
    client = _client()
    with pytest.raises(LLMError, match="could not be reached"):
        await client.chat(model="m", messages=[])
    assert route.call_count == 4
    await client.aclose()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_unexpected_response_shapes_raise(
    groq: respx.MockRouter, response: httpx.Response
) -> None:
    groq.post("/chat/completions").mock(return_value=response)
    client = _client()
    with pytest.raises(LLMError, match="unexpected"):
        await client.chat(model="m", messages=[])
    await client.aclose()


def test_daily_cap(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'b.db'}")
    create_db_and_tables(engine)
    with Session(engine) as db:
        assert try_consume(db, cap=2) and try_consume(db, cap=2)
        assert not try_consume(db, cap=2)  # third call is refused and not counted
        assert db.exec(__import__("sqlmodel").select(LlmUsage)).one().calls == 2
        assert not try_consume(db, cap=0)
