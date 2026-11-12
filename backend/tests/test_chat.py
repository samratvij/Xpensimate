import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
from anthropic.types import Message
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import BudgetEnvelope, ChatMemory, ChatMessage, ChatThread, RecurringExpense, Transaction
from app.services import chat as chat_service
from main import app

THIS = datetime.now().strftime("%Y-%m")


def add(db, amount, merchant="LOBLAWS #1", category="Grocery", purpose="Shared", day=5, **kw):
    txn = Transaction(source="test", timestamp=datetime.fromisoformat(f"{THIS}-{day:02d}T17:00:00+00:00"),
                      amount=Decimal(amount), merchant_raw=merchant, merchant_normalized=merchant.title(),
                      category=category, purpose=purpose, categorized_by="rule", **kw)
    db.add(txn)
    db.commit()
    return txn


def text(t):
    return {"type": "text", "text": t}


def tool(name, tool_input, tool_id="toolu_1"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.message.content:
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)

    def get_final_message(self):
        return self.message


class FakeClaude:
    """Plays back scripted responses (content blocks + stop reason, or an exception) and records each request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def stream(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))  # a snapshot: the lists keep growing
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        content, stop = step if isinstance(step, tuple) else (step, None)
        stop = stop or ("tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn")
        return FakeStream(Message.model_validate({
            "id": "msg_1", "type": "message", "role": "assistant", "model": request["model"], "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        }))


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")

    def install(*script):
        fake = FakeClaude(*script)
        monkeypatch.setattr(chat_service, "chat_client", lambda: fake)
        return fake

    return install


@pytest.fixture
def sam():
    return TestClient(app, headers={"Authorization": "Bearer sam-test-token-0001"})


def ask(client, message, **body):
    return client.post("/api/v1/chat", json={"message": message, **body})


def overloaded():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.OverloadedError("Overloaded", response=httpx2.Response(529, request=request), body=None)


def test_needs_an_api_key(client):
    assert ask(client, "hi").status_code == 503
    assert client.get("/api/v1/chat/usage").json()["available"] is False


def test_answers_and_keeps_the_conversation(client, claude):
    fake = claude([text("Hello! Ask me anything about your spending.")], [text("You spent nothing yet.")])
    first = ask(client, "Hi there")
    assert first.status_code == 200, first.text
    reply = first.json()
    assert reply["text"] == "Hello! Ask me anything about your spending."
    assert reply["model"] == "claude-sonnet-5-5"
    assert reply["questions_left"] == 99

    request = fake.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["tools"][-1]["type"] == "web_search_20260209"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Alex" in request["system"][1]["text"]

    second = ask(client, "How much this month?", thread_id=reply["thread_id"]).json()
    assert second["thread_id"] == reply["thread_id"]
    replayed = fake.requests[1]["messages"]
    assert [m["role"] for m in replayed] == ["user", "assistant", "user"]
    assert replayed[1]["content"][0]["text"] == "Hello! Ask me anything about your spending."

    thread = client.get(f"/api/v1/chat/threads/{reply['thread_id']}").json()
    assert thread["title"] == "Hi there"
    assert [(m["kind"], m["text"]) for m in thread["messages"]] == [
        ("question", "Hi there"), ("answer", "Hello! Ask me anything about your spending."),
        ("question", "How much this month?"), ("answer", "You spent nothing yet."),
    ]
    assert client.get("/api/v1/chat/usage").json() == {"used": 2, "limit": 100, "left": 98, "available": True,
                                                       "web_search": True}


def thinking(t):
    return {"type": "thinking", "thinking": t, "signature": "sig-" + t}


def test_drops_earlier_turns_thinking(client, claude):
    # The API rejects an earlier turn's thinking once the system prompt (with the time in it) has changed;
    # the current turn's thinking still goes back between tool calls.
    fake = claude([thinking("plan"), tool("month_summary", {})], [thinking("sum up"), text("Nothing yet.")],
                  [text("Still nothing.")])
    reply = ask(client, "How much this month?").json()
    assert fake.requests[1]["messages"][1]["content"][0]["type"] == "thinking"

    assert ask(client, "And now?", thread_id=reply["thread_id"]).status_code == 200
    replayed = fake.requests[2]["messages"]
    assert not [b for m in replayed for b in m["content"] if b["type"] == "thinking"]
    assert [m["role"] for m in replayed] == ["user", "assistant", "user", "assistant", "user"]
    assert replayed[1]["content"][0]["type"] == "tool_use"
    assert replayed[3]["content"][0]["text"] == "Nothing yet."


def test_runs_tools_and_sends_back_their_results(client, db, claude):
    add(db, "45.10", merchant="COSTCO WHOLESALE")
    add(db, "54.90", merchant="COSTCO WHOLESALE", day=6, note="party supplies")
    add(db, "12.00", merchant="TIM HORTONS", category="Eating Out")
    fake = claude([tool("search_transactions", {"text": "costco"})], [text("You spent $100.00 at Costco.")])
    reply = ask(client, "How much at Costco this month?").json()
    assert reply["text"] == "You spent $100.00 at Costco."
    assert reply["actions"] == []

    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1" and not result["is_error"]
    data = json.loads(result["content"])
    assert (data["matching"], data["total"]) == (2, 100.0)
    assert data["listed"][0]["note"] == "party supplies"
    # The tool round is stored, so the next question replays it.
    kinds = [m.kind for m in db.scalars(select(ChatMessage).order_by(ChatMessage.seq))]
    assert kinds == ["question", "step", "step", "answer"]


def test_month_summary_tool_uses_the_budget_numbers(client, db, claude):
    client.put("/api/v1/budgets/Grocery", json={"monthly_limit": "500"})
    add(db, "120.00")
    fake = claude([tool("month_summary", {})], [text("ok")])
    ask(client, "How are we doing?")
    data = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])
    grocery = next(r for r in data["categories"] if r["category"] == "Grocery")
    assert (grocery["spent"], grocery["monthly_limit"], grocery["percent_used"]) == (120.0, 500.0, 24.0)


def test_can_change_data_when_asked(client, db, claude):
    txn = add(db, "80.00", merchant="SEPHORA")
    claude(
        [tool("set_budget", {"budget_line": "Eating Out", "monthly_limit": "300"}, "t1"),
         tool("update_transaction", {"transaction_id": str(txn.id), "purpose": "Sam", "category": "Personal Care",
                                     "remember": True}, "t2")],
        [text("Done: Eating Out is $300/month and the Sephora charge is now Sam's.")],
    )
    reply = ask(client, "Set eating out to 300 and the Sephora charge was mine (Sam)").json()
    assert [a["tool"] for a in reply["actions"]] == ["set_budget", "update_transaction"]
    assert "Eating Out: $300" in reply["actions"][0]["summary"]
    assert db.get(BudgetEnvelope, "Eating Out").monthly_limit == Decimal("300.00")
    db.expire_all()
    changed = db.get(Transaction, txn.id)
    assert (changed.category, changed.purpose, changed.edited_by) == ("Personal Care", "Sam", "Alex")
    # The answer keeps the record of what changed.
    thread = client.get(f"/api/v1/chat/threads/{reply['thread_id']}").json()
    assert len(thread["messages"][-1]["actions"]) == 2


def test_bills_can_be_added_and_marked_paid(client, db, claude):
    claude([tool("add_bill", {"name": "Rent", "amount": "2100", "cadence": "monthly", "anchor_date": f"{THIS}-01",
                              "category": "Rent + Parking"})], [text("Tracking rent.")])
    ask(client, "Track our rent, $2100 on the 1st")
    bill = db.scalar(select(RecurringExpense))
    assert (bill.name, bill.amount) == ("Rent", Decimal("2100.00"))

    claude([tool("mark_bill_paid", {"bill_id": str(bill.id), "paid_on": f"{THIS}-01"})], [text("Marked.")])
    reply = ask(client, "Rent's paid").json()
    assert reply["actions"][0]["tool"] == "mark_bill_paid"
    assert db.scalar(select(Transaction).where(Transaction.recurring_id == bill.id)).amount == Decimal("2100.00")


def test_tool_errors_go_back_to_claude(client, db, claude):
    fake = claude(
        [tool("set_budget", {"budget_line": "Yachts", "monthly_limit": "10"}, "t1"),
         tool("update_transaction", {"transaction_id": "not-an-id"}, "t2"),
         tool("set_budget", {"budget_line": "Grocery", "monthly_limit": -5}, "t3"),
         tool("no_such_tool", {}, "t4")],
        [text("Which budget did you mean?")],
    )
    reply = ask(client, "set a budget").json()
    assert reply["actions"] == []
    results = fake.requests[1]["messages"][-1]["content"]
    assert all(r["is_error"] for r in results)
    assert "Unknown budget line" in results[0]["content"]
    assert "not a transaction id" in results[1]["content"]
    assert "monthly_limit" in results[2]["content"]
    assert db.scalar(select(BudgetEnvelope)) is None


def test_remembers_facts_privately(client, sam, db, claude):
    fake = claude([tool("remember", {"fact": "Planning a trip to the coast in March 2027, about $4,000."})],
                  [text("Noted.")])
    reply = ask(sam, "We're going to the coast in March, around 4k").json()
    assert reply["actions"][0]["summary"].startswith("Remembered: Planning a trip to the coast")
    memory = db.scalar(select(ChatMemory))
    assert memory.owner == "Sam"
    assert [m["fact"] for m in sam.get("/api/v1/chat/memory").json()] == [memory.fact]
    assert client.get("/api/v1/chat/memory").json() == []

    claude([text("Your trip is in March.")])
    ask(sam, "When's my trip?")
    fake2 = chat_service.chat_client()
    assert "the coast in March 2027" in fake2.requests[0]["system"][1]["text"]
    assert str(memory.id) in fake2.requests[0]["system"][1]["text"]
    claude([text("hi")])
    ask(client, "hi")
    assert "coast" not in chat_service.chat_client().requests[0]["system"][1]["text"]

    claude([tool("forget", {"memory_id": str(memory.id)})], [text("Forgotten.")])
    ask(client, "forget it")  # Alex can't forget Sam's memory
    assert db.scalar(select(ChatMemory)) is not None
    assert sam.delete(f"/api/v1/chat/memory/{memory.id}").status_code == 204
    assert not fake.script


def test_memories_can_be_managed_directly(client):
    created = client.post("/api/v1/chat/memory", json={"fact": "Saving for a car by June."}).json()
    assert client.get("/api/v1/chat/memory").json()[0]["fact"] == "Saving for a car by June."
    assert client.delete(f"/api/v1/chat/memory/{created['id']}").status_code == 204
    assert client.get("/api/v1/chat/memory").json() == []


def test_conversations_are_private(client, sam, claude):
    claude([text("hello")])
    thread_id = ask(client, "secret plans").json()["thread_id"]
    assert [t["id"] for t in client.get("/api/v1/chat/threads").json()] == [thread_id]
    assert sam.get("/api/v1/chat/threads").json() == []
    assert sam.get(f"/api/v1/chat/threads/{thread_id}").status_code == 404
    assert ask(sam, "continue", thread_id=thread_id).status_code == 404
    assert sam.delete(f"/api/v1/chat/threads/{thread_id}").status_code == 404
    assert client.delete(f"/api/v1/chat/threads/{thread_id}").status_code == 204
    assert client.get("/api/v1/chat/threads").json() == []


def test_monthly_question_limit(client, sam, claude, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_monthly_limit", 2)
    claude([text("1")], [text("2")], [text("other person")])
    assert ask(client, "one").json()["questions_left"] == 1
    assert ask(client, "two").json()["questions_left"] == 0
    blocked = ask(client, "three")
    assert blocked.status_code == 429 and "2 questions" in blocked.json()["detail"]
    assert ask(sam, "mine").status_code == 200  # the limit is per person


def test_falls_back_to_haiku_when_sonnet_is_overloaded(client, claude):
    fake = claude(overloaded(), [tool("insights", {})], [text("Here's what stands out.")])
    reply = ask(client, "anything to watch?").json()
    assert reply["model"] == "claude-haiku-5-5"
    assert [r["model"] for r in fake.requests] == ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-5-5"]
    assert fake.requests[1]["tools"][-1]["type"] == "web_search_20250305"


def test_failure_on_both_models_leaves_nothing_behind(client, db, claude):
    claude(overloaded(), overloaded())
    assert ask(client, "hi").status_code == 502
    assert db.scalar(select(ChatThread)) is None
    assert client.get("/api/v1/chat/usage").json()["used"] == 0


def test_cut_off_tool_calls_are_answered_but_not_run(client, db, claude):
    fake = claude(([text("Let me set that."), tool("set_budget", {"budget_line": "Grocery", "monthly_limit": "1"})],
                   "max_tokens"), [text("next")])
    reply = ask(client, "budget").json()
    assert reply["text"] == "Let me set that."
    assert db.scalar(select(BudgetEnvelope)) is None
    ask(client, "again", thread_id=reply["thread_id"])
    replayed = fake.requests[1]["messages"]
    assert replayed[2]["content"][0]["tool_use_id"] == "toolu_1"  # the cut-off call has its result
    assert replayed[2]["content"][0]["is_error"] is True


def test_last_round_must_answer(client, claude, monkeypatch):
    monkeypatch.setattr(chat_service, "MAX_ROUNDS", 2)
    fake = claude([tool("insights", {})], [tool("insights", {})], [text("Best I can tell...")])
    assert ask(client, "dig deep").json()["text"] == "Best I can tell..."
    assert "tool_choice" not in fake.requests[1]
    assert fake.requests[2]["tool_choice"] == {"type": "none"}


def test_streams_server_sent_events(client, claude):
    claude([text("Checking. "), tool("bills", {})], [text("Rent is due on the 1st.")])
    with client.stream("POST", "/api/v1/chat", json={"message": "what's due?"},
                       headers={"Accept": "text/event-stream"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = "".join(response.iter_text())
    events = [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in raw.strip().split("\n\n")
    ]
    names = [name for name, _ in events]
    assert names == ["thread", "text", "status", "text", "text", "done"]
    assert "".join(data["text"] for name, data in events if name == "text") == "Checking. \n\nRent is due on the 1st."
    done = events[-1][1]
    assert done["text"] == "Checking. \n\nRent is due on the 1st." and done["questions_left"] == 99


def test_streamed_failure_reports_an_error(client, claude):
    claude(overloaded(), overloaded())
    with client.stream("POST", "/api/v1/chat", json={"message": "hi"},
                       headers={"Accept": "text/event-stream"}) as response:
        raw = "".join(response.iter_text())
    assert raw.split("\n\n")[-2].startswith("event: error")
    assert client.get("/api/v1/chat/threads").json() == []


def test_full_threads_must_start_over(client, claude, monkeypatch):
    monkeypatch.setattr(chat_service, "MAX_QUESTIONS_PER_THREAD", 1)
    claude([text("a")])
    thread_id = ask(client, "q").json()["thread_id"]
    assert client.get(f"/api/v1/chat/threads/{thread_id}").json()["can_continue"] is False
    assert ask(client, "more", thread_id=thread_id).status_code == 409


def test_web_sources_are_listed(client, claude):
    claude([{"type": "text", "text": "Flights are about $1,400.", "citations": [
        {"type": "web_search_result_location", "url": "https://example.com/fares", "title": "Fares",
         "cited_text": "from $1,400", "encrypted_index": "x"}]}])
    reply = ask(client, "flight prices for March?").json()
    assert reply["sources"] == [{"url": "https://example.com/fares", "title": "Fares"}]


def test_searched_pages_stand_in_when_nothing_is_cited(client, claude):
    def result(url, title):
        return {"type": "web_search_result", "url": url, "title": title, "encrypted_content": "x", "page_age": None}

    claude([{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "fares"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                result(f"https://example.com/{n}", f"Page {n}") for n in (1, 2, 1, 3, 4, 5, 6)]},
            text("Flights are about $1,400.")])
    reply = ask(client, "flight prices for March?").json()
    assert [s["url"] for s in reply["sources"]] == [f"https://example.com/{n}" for n in (1, 2, 3, 4, 5)]


def test_changing_a_budget_keeps_its_warning_threshold(client, db, claude):
    db.add(BudgetEnvelope(category="Grocery", monthly_limit=Decimal("800"), warning_threshold_percent=Decimal("90")))
    db.commit()
    claude([tool("set_budget", {"budget_line": "Grocery", "monthly_limit": "900"})], [text("Done.")])
    ask(client, "make groceries 900")
    db.expire_all()
    budget = db.get(BudgetEnvelope, "Grocery")
    assert (budget.monthly_limit, budget.warning_threshold_percent) == (Decimal("900.00"), Decimal("90.00"))


def test_web_search_looks_from_the_households_location(client, claude, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_location", "Lisbon, Lisboa, pt")
    fake = claude([text("ok")])
    ask(client, "hi")
    assert fake.requests[0]["tools"][-1]["user_location"] == {
        "type": "approximate", "timezone": "America/Toronto", "country": "PT", "city": "Lisbon", "region": "Lisboa"}


def test_web_search_can_be_turned_off(client, claude, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_web_searches", 0)
    fake = claude([text("ok")])
    ask(client, "hi")
    assert all(t.get("type") is None for t in fake.requests[0]["tools"])
    assert "can't search the web" in fake.requests[0]["system"][0]["text"]


def test_prompt_follows_the_households_currency(tmp_path, monkeypatch):
    path = tmp_path / "household.toml"
    path.write_text('description = "two people in Lisbon"\ncurrency = "EUR"\n[[purposes]]\nname = "Shared"\n')
    monkeypatch.setenv("HOUSEHOLD_FILE", str(path))
    prompt = chat_service.instructions(web_search=True)
    assert "amounts are in EUR" in prompt and "€4,000" in prompt and "CAD" not in prompt


# ---- A model on your own server (LLM_PROVIDER=openai) ----


class LocalServer:
    """An OpenAI-compatible /chat/completions that plays back scripted replies and records each request."""

    def __init__(self, monkeypatch, *script):
        self.script = list(script)
        self.requests = []
        monkeypatch.setattr(chat_service.httpx, "post", self.post)

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append(__import__("json").loads(__import__("json").dumps(json)))
        step = self.script.pop(0)
        if isinstance(step, httpx.Response):
            return step
        content, calls = step if isinstance(step, tuple) else (step, [])
        message = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = [{"id": f"call_{n}", "type": "function",
                                      "function": {"name": name, "arguments": __import__("json").dumps(args)}}
                                     for n, (name, args) in enumerate(calls)]
        return httpx.Response(200, json={"choices": [{"message": message,
                                                      "finish_reason": "tool_calls" if calls else "stop"}]})


@pytest.fixture
def local(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_base_url", "http://models.test/v1")
    return lambda *script: LocalServer(monkeypatch, *script)


def test_a_local_model_answers_with_the_same_tools(client, db, local):
    add(db, "45.10", merchant="COSTCO WHOLESALE")
    server = local(("", [("search_transactions", {"text": "costco"})]), "<think>add up</think>You spent $45.10 at Costco.")
    reply = ask(client, "How much at Costco?").json()
    assert reply["text"] == "You spent $45.10 at Costco."
    assert reply["model"] == "qwen3:8b"

    first = server.requests[0]
    assert first["model"] == "qwen3:8b"
    assert first["messages"][0]["role"] == "system" and "Alex" in first["messages"][0]["content"]
    assert "can't search the web" in first["messages"][0]["content"]
    assert {t["function"]["name"] for t in first["tools"]} >= {"search_transactions", "set_budget"}
    sent_back = server.requests[1]["messages"]
    assert [m["role"] for m in sent_back[1:]] == ["user", "assistant", "tool"]
    call_id = sent_back[2]["tool_calls"][0]["id"]
    assert call_id.startswith("toolu_") and sent_back[3]["tool_call_id"] == call_id
    assert json.loads(sent_back[3]["content"])["matching"] == 1

    # The next question replays the whole conversation, tool round included.
    local("Nothing else.")
    second = ask(client, "Anything else?", thread_id=reply["thread_id"])
    assert second.status_code == 200


def test_a_local_model_can_change_data(client, db, local):
    local(("", [("set_budget", {"budget_line": "Grocery", "monthly_limit": 650})]), "Grocery is now 650 a month.")
    reply = ask(client, "make groceries 650").json()
    assert reply["actions"][0]["tool"] == "set_budget"
    assert db.get(BudgetEnvelope, "Grocery").monthly_limit == Decimal("650.00")


def test_a_local_models_last_round_gets_no_tools(client, local, monkeypatch):
    monkeypatch.setattr(chat_service, "MAX_ROUNDS", 1)
    server = local(("", [("insights", {})]), "Here's what I found.")
    assert ask(client, "dig").json()["text"] == "Here's what I found."
    assert "tools" in server.requests[0] and "tools" not in server.requests[1]


def test_a_local_model_that_is_down_leaves_nothing_behind(client, db, local):
    local(httpx.Response(503, text="loading"))
    assert ask(client, "hi").status_code == 502
    assert db.scalar(select(ChatThread)) is None


def test_a_local_model_falls_back_when_one_is_set(client, local, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_fallback_model", "llama3.1:8b")
    server = local(httpx.Response(404, json={"error": "model not found"}), "hello")
    assert ask(client, "hi").json()["model"] == "llama3.1:8b"
    assert [r["model"] for r in server.requests] == ["qwen3:8b", "llama3.1:8b"]


def test_a_claude_conversation_replays_on_a_local_model(client, claude, monkeypatch):
    claude([{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "fares"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []},
            thinking("hmm"), text("About $1,400.")])
    thread_id = ask(client, "flight prices?").json()["thread_id"]
    monkeypatch.setattr(get_settings(), "llm_provider", "openai")
    monkeypatch.setattr(chat_service, "chat_client", lambda: None)
    server = LocalServer(monkeypatch, "Still about $1,400.")
    assert ask(client, "and now?", thread_id=thread_id).status_code == 200
    replayed = server.requests[0]["messages"][1:]
    assert replayed == [{"role": "user", "content": "flight prices?"}, {"role": "assistant", "content": "About $1,400."},
                        {"role": "user", "content": "and now?"}]


def test_chat_models_follow_the_provider(monkeypatch):
    settings = get_settings()
    assert settings.chat_models() == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    monkeypatch.setattr(settings, "chat_fallback_model", "none")
    assert settings.chat_models() == ["claude-sonnet-5-5"]
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "chat_fallback_model", "")
    assert settings.chat_models() == ["qwen3:8b"]
