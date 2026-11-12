"""The chat assistant: answers questions about the household's money, from its own data and the web.

One turn = one question. The model (CHAT_MODEL) gets the conversation so far and the tools in chat_tools; the
loop runs the tools it asks for and sends the results back until it answers. If the model is overloaded or
unreachable, the request is retried on CHAT_FALLBACK_MODEL and the rest of the turn stays there.

Two providers, as for the rest of the app (LLM_PROVIDER):
- Claude: streamed, with web search (a server tool) and prompt caching.
- An OpenAI-compatible server (Ollama etc.): the same tools through function calling, one reply per round,
  no web search.

Every message is stored in Claude's message format, exactly as sent or received, and the thread is only ever
appended to, so replaying it next turn is valid and the cached prefix stays the same; for an OpenAI-compatible
server the replay is translated. A tool call is always stored together with its result. Thinking from earlier
turns is left out of the replay: the API binds a thinking block to the system prompt it was made under, and ours
changes every turn (the time, the remembered facts).
"""
import json
import logging
import re
import uuid
from collections.abc import Generator, Iterator
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import anthropic
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.household import load_household
from app.core.money import money
from app.core.taxonomy import DEFAULT_PURPOSE, budget_lines, household_description, purpose_guide
from app.db.base import SessionLocal
from app.db.models import ChatMemory, ChatMessage, ChatThread
from app.services.categories import category_names
from app.services.chat_tools import ToolContext, definitions, run_tool
from app.services.llm import CATEGORY_NOTES

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
# A local model's context is small, and its answers are short.
LOCAL_MAX_TOKENS = 4096
# Tool rounds per question; the last round has to answer with what it has.
MAX_ROUNDS = 8
# Past this, a thread is too long to replay cheaply: the apps start a new one.
MAX_QUESTIONS_PER_THREAD = 40
TITLE_LENGTH = 60
# Without citations (the dynamic-filtering web search reads results in code, so its answers cite nothing),
# the first few pages it searched stand in as sources.
MAX_SEARCHED_SOURCES = 5

STATUS = {
    "month_summary": "Checking the month's budgets",
    "spending_trend": "Looking at spending over time",
    "month_review": "Reviewing the month",
    "search_transactions": "Looking through transactions",
    "search_receipt_items": "Looking through receipts",
    "bills": "Checking bills",
    "savings_goals": "Checking savings goals",
    "net_worth": "Checking balances",
    "insights": "Checking insights",
    "remember": "Saving that",
    "forget": "Forgetting that",
}

WEB = """Use web search for outside facts (prices, rates, fees, rules) and say where they came from."""
NO_WEB = """You can't search the web. When a question needs outside facts (prices, rates, fees, rules), say so and
give what you know, clearly marked as general knowledge that may be out of date."""


def instructions(web_search: bool) -> str:
    example = money(Decimal(4000), whole_from=Decimal(1))
    return f"""You are the money assistant in Xpensimate, a self-hosted expense tracker for one household. You
talk with one person at a time; each person's chats are private to them.

Answer questions about the household's spending, budgets, bills, income and savings, and help plan future
expenses. Get facts from the tools rather than guessing: amounts are in {load_household().currency} unless a
transaction says otherwise, and the tools' totals are exact. {WEB if web_search else NO_WEB}
When a question is about plans or affordability, combine what they've spent (usually the last few months),
their budgets and the bills still due, and say what you assumed.

Changing data: you may change transactions, budgets and bills, and remember or forget things, but only when the
person clearly asks for that change, or clearly agrees to one you suggested. If what to change is ambiguous
(which transaction, what amount, which budget line), look it up and ask instead of guessing. Never change data
just because a question mentions it. After a change, say exactly what you changed.

Remember things when the person tells you about plans, goals, upcoming expenses or circumstances worth knowing
in later chats (e.g. "we're going on a trip in March, about {example}"), and when they ask you to. Store each as
one self-contained sentence with dates and amounts. Forget facts that turn out wrong or done.

Keep answers short and concrete: lead with the answer, then the few numbers behind it. Write plain text for a
phone screen; use short lists when they help, no tables or headings."""


class ChatFailed(Exception):
    """The model couldn't be reached (on any of the chat models)."""


class RoundFailed(Exception):
    """One request failed; `can_fall_back` says whether another model may take it over."""

    def __init__(self, message: str, can_fall_back: bool):
        super().__init__(message)
        self.can_fall_back = can_fall_back


@lru_cache
def claude_chat_client() -> anthropic.Anthropic:
    # Streamed, so the timeout is the longest wait between chunks, not for the whole answer.
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=120.0, max_retries=2)


def chat_client() -> anthropic.Anthropic | None:
    """The Claude client, or None with an OpenAI-compatible server (which is reached over plain HTTP)."""
    return claude_chat_client() if get_settings().llm_provider == "anthropic" else None


def web_search_enabled() -> bool:
    settings = get_settings()
    return settings.llm_provider == "anthropic" and settings.chat_web_searches > 0


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def questions_this_month(db: Session, user: str) -> int:
    start = datetime.now(_tz()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count()).select_from(ChatMessage).join(ChatThread).where(
            ChatThread.owner == user, ChatMessage.kind == "question", ChatMessage.created_at >= start
        )
    )


def questions_left(db: Session, user: str) -> int | None:
    """None when there's no monthly limit."""
    limit = get_settings().chat_monthly_limit
    return max(0, limit - questions_this_month(db, user)) if limit > 0 else None


def thread_questions(db: Session, thread: ChatThread) -> int:
    return db.scalar(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.thread_id == thread.id, ChatMessage.kind == "question"))


def household_context(db: Session) -> str:
    """Stable for weeks at a time, so it's cached with the tools."""
    categories = category_names(db)
    notes = "\n".join(f'- "{c}": {n}.' for c, n in CATEGORY_NOTES.items() if c in categories)
    return f"""{instructions(web_search_enabled())}

The household is {household_description()}. Each transaction has a category (what the money was spent on) and a
purpose (who it was for); together they give the budget line it counts against. "{DEFAULT_PURPOSE}" spending is
budgeted by category; personal and family spending by person.

Categories: {", ".join(categories)}.
{notes}

Purposes:
{purpose_guide()}

Budget lines: {", ".join(budget_lines(categories))}.

Transactions marked not an expense (card payments, moves between their own accounts) are left out of all totals.
Income (net pay landing in their accounts) is tracked separately from spending: month_summary has it.
Receipt-itemized charges count each line under its own budget line."""


def personal_context(db: Session, user: str) -> str:
    now = datetime.now(_tz())
    memories = list(db.scalars(select(ChatMemory).where(ChatMemory.owner == user).order_by(ChatMemory.created_at)))
    remembered = "\n".join(f"- [{m.id}] {m.fact} (noted {m.created_at.astimezone(_tz()):%Y-%m-%d})" for m in memories)
    return f"""You're talking with {user}. Today is {now:%A %Y-%m-%d}, {now:%H:%M} local time ({get_settings().timezone}).

What {user} asked you to keep in mind (id in brackets, for forget):
{remembered or "- nothing yet"}"""


def user_location() -> dict[str, str]:
    """Where web searches should look from: CHAT_LOCATION ("City, Region, CC" or "CC") and the time zone."""
    location = {"type": "approximate", "timezone": get_settings().timezone}
    parts = [p.strip() for p in get_settings().chat_location.split(",") if p.strip()]
    if parts and re.fullmatch(r"[A-Za-z]{2}", parts[-1]):
        location["country"] = parts.pop().upper()
    if parts:
        location["city"] = parts[0]
    if len(parts) > 1:
        location["region"] = parts[1]
    return location


def _web_search(model: str) -> dict:
    # The dynamic-filtering version needs Sonnet/Opus; Haiku takes the basic one.
    kind = "web_search_20250305" if model.startswith("claude-haiku") else "web_search_20260209"
    return {"type": kind, "name": "web_search", "max_uses": get_settings().chat_web_searches,
            "user_location": user_location()}


def _request(model: str, system: list[dict], messages: list[dict], final_round: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
        "tools": definitions() + ([_web_search(model)] if web_search_enabled() else []),
        "output_config": {"effort": get_settings().chat_effort},
        # Caches the conversation so far; the tools + household context have their own breakpoint.
        "cache_control": {"type": "ephemeral"},
    }
    if final_round:
        request["tool_choice"] = {"type": "none"}
    return request


def _should_fall_back(error: Exception) -> bool:
    if isinstance(error, (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.NotFoundError)):
        return True
    return isinstance(error, anthropic.APIStatusError) and error.status_code >= 500


Round = Generator[dict, None, tuple[list[dict], str]]


def claude_round(client: anthropic.Anthropic, model: str, system: list[dict], messages: list[dict],
                 final_round: bool) -> Round:
    """One streamed request to Claude: yields text and status events, returns (content blocks, stop reason)."""
    streamed = False
    try:
        with client.messages.stream(**_request(model, system, messages, final_round)) as stream:
            for event in stream:
                if event.type == "text" and event.text:
                    streamed = True
                    yield {"type": "text", "text": event.text}
                elif (event.type == "content_block_start" and event.content_block.type == "server_tool_use"
                      and event.content_block.name == "web_search"):
                    yield {"type": "status", "text": "Searching the web"}
            response = stream.get_final_message()
    except anthropic.APIError as e:
        log.warning("Chat request failed on %s (%s): %s", model, getattr(e, "status_code", None), e)
        raise RoundFailed(str(e), can_fall_back=not streamed and _should_fall_back(e)) from e
    return [block.to_dict(mode="json") for block in response.content], response.stop_reason


def openai_messages(system: list[dict], messages: list[dict]) -> list[dict]:
    """The thread (Claude's format) as chat completion messages. Web search blocks and thinking are left out."""
    out = [{"role": "system", "content": "\n\n".join(block["text"] for block in system)}]
    for message in messages:
        blocks = message["content"]
        texts = "\n\n".join(b["text"] for b in blocks if b.get("type") == "text")
        if message["role"] == "user":
            for result in (b for b in blocks if b.get("type") == "tool_result"):
                content = result.get("content")
                out.append({"role": "tool", "tool_call_id": result["tool_use_id"],
                            "content": content if isinstance(content, str) else json.dumps(content)})
            if texts:
                out.append({"role": "user", "content": texts})
            continue
        calls = [{"id": b["id"], "type": "function",
                  "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                 for b in blocks if b.get("type") == "tool_use"]
        if texts or calls:
            out.append({"role": "assistant", "content": texts, **({"tool_calls": calls} if calls else {})})
    return out


def _openai_tools() -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in definitions()]


def _without_thinking(text: str) -> str:
    """A reasoning model's <think> block isn't part of the answer."""
    return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.DOTALL).strip()


def openai_round(model: str, system: list[dict], messages: list[dict], final_round: bool) -> Round:
    """One request to an OpenAI-compatible server, translated to and from Claude's message format."""
    settings = get_settings()
    body: dict[str, Any] = {"model": model, "max_tokens": LOCAL_MAX_TOKENS, "temperature": 0.2,
                            "messages": openai_messages(system, messages)}
    if not final_round:  # the last round has to answer, so it gets no tools
        body["tools"] = _openai_tools()
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=settings.llm_timeout)
    except httpx.HTTPError as e:
        log.warning("Chat request to %s failed: %s", settings.llm_base_url, e)
        raise RoundFailed(f"Could not reach the model server ({type(e).__name__})", can_fall_back=True) from e
    if response.status_code >= 400:
        log.warning("Chat request failed on %s (%s): %s", model, response.status_code, response.text[:300])
        raise RoundFailed(f"Model server error {response.status_code}",
                          can_fall_back=response.status_code in (404, 429) or response.status_code >= 500)
    try:
        choice = response.json()["choices"][0]
        message = choice["message"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RoundFailed("The model server's answer wasn't a chat completion", can_fall_back=False) from e

    content: list[dict] = []
    text = _without_thinking(message.get("content") or "")
    if text:
        content.append({"type": "text", "text": text})
        yield {"type": "text", "text": text}
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = None
        # Our own ids: Claude's format (which the thread is stored in) only allows letters, digits, _ and -.
        content.append({"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:24]}", "name": function.get("name", ""),
                        "input": arguments if isinstance(arguments, dict) else {}})
    stop = {"length": "max_tokens", "tool_calls": "tool_use"}.get(choice.get("finish_reason"), "end_turn")
    if any(b["type"] == "tool_use" for b in content) and stop == "end_turn":
        stop = "tool_use"
    return content, stop


def _append(db: Session, thread: ChatThread, role: str, kind: str, content: list[dict], **extra) -> ChatMessage:
    seq = db.scalar(select(func.coalesce(func.max(ChatMessage.seq), -1)).where(ChatMessage.thread_id == thread.id)) + 1
    message = ChatMessage(thread_id=thread.id, seq=seq, role=role, kind=kind, content=content, **extra)
    db.add(message)
    thread.updated_at = func.now()
    db.commit()
    return message


THINKING = {"thinking", "redacted_thinking"}


def replay(thread_messages: list[ChatMessage]) -> list[dict]:
    """Earlier turns as API messages (display-only rows have no content), without their thinking.

    Only the current turn's thinking has to go back (between tool calls); an earlier turn's is rejected once the
    system prompt has changed, which it does every turn.
    """
    messages = []
    for m in thread_messages:
        content = [b for b in m.content or [] if b.get("type") not in THINKING]
        if content:
            messages.append({"role": m.role, "content": content})
    return messages


def _sources(blocks: list[dict]) -> list[dict]:
    found = {}
    for block in blocks:
        for citation in block.get("citations") or []:
            if citation.get("url"):
                found.setdefault(citation["url"], {"url": citation["url"], "title": citation.get("title")})
    return list(found.values())


def _searched(blocks: list[dict]) -> list[dict]:
    """The pages web searches returned, in order."""
    found = []
    for block in blocks:
        if block["type"] == "web_search_tool_result" and isinstance(block.get("content"), list):
            found += [{"url": r["url"], "title": r.get("title")} for r in block["content"] if r.get("url")]
    return found


def _turn_sources(cited: list[dict], searched: list[dict]) -> list[dict]:
    if cited:
        return cited
    unique = list({s["url"]: s for s in searched}.values())
    return unique[:MAX_SEARCHED_SOURCES]


def run_turn(db: Session, client: anthropic.Anthropic | None, user: str, thread: ChatThread,
             question: str) -> Iterator[dict]:
    """Answers one question, yielding events for the app as it goes:

    {"type": "status", "text"} while tools run, {"type": "text", "text"} pieces of the answer,
    {"type": "action", "summary"} for each change made, and finally {"type": "done", "message_id", "text",
    "actions", "sources", "model"}. Raises ChatFailed if no chat model can be reached. `client` is Claude's;
    None sends the turn to the OpenAI-compatible server.
    """
    settings = get_settings()
    messages = replay(thread.messages)
    asked = _append(db, thread, "user", "question", [{"type": "text", "text": question}], text=question)
    messages.append({"role": "user", "content": asked.content})

    system = [
        {"type": "text", "text": household_context(db), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": personal_context(db, user)},
    ]
    ctx = ToolContext(db=db, user=user, tz=_tz())
    models = settings.chat_models()
    texts: list[str] = []
    sources: list[dict] = []
    searched: list[dict] = []
    needs_break = False

    def one_round(model: str, final_round: bool) -> Round:
        if client is None:
            return openai_round(model, system, messages, final_round)
        return claude_round(client, model, system, messages, final_round)

    for round_no in range(MAX_ROUNDS + 1):
        result = None
        while result is None:
            model = models[0]
            try:
                events = one_round(model, round_no == MAX_ROUNDS)
                while True:
                    try:
                        event = next(events)
                    except StopIteration as stop:
                        result = stop.value
                        break
                    if event["type"] == "text" and needs_break:
                        yield {"type": "text", "text": "\n\n"}
                        needs_break = False
                    yield event
            except RoundFailed as e:
                if len(models) == 1 or not e.can_fall_back:
                    if round_no == 0:  # nothing happened yet: the question doesn't count
                        db.delete(asked)
                        db.commit()
                    raise ChatFailed(str(e)) from e
                log.warning("Chat falling back from %s: %s", model, e)
                models.pop(0)  # the rest of this turn stays on the fallback model
        content, stop_reason = result

        round_text = "".join(b["text"] for b in content if b["type"] == "text")
        if round_text:
            texts.append(round_text)
            needs_break = True
        sources += [s for s in _sources(content) if s not in sources]
        searched += _searched(content)

        if stop_reason == "pause_turn":  # a long server-side web search: send it back to continue
            _append(db, thread, "assistant", "step", content, model=model)
            messages.append({"role": "assistant", "content": content})
            continue

        tool_uses = [b for b in content if b["type"] == "tool_use"]
        finished = not tool_uses or stop_reason in ("max_tokens", "refusal") or round_no == MAX_ROUNDS
        if not tool_uses:
            text = "\n\n".join(texts) or "Sorry, I couldn't come up with an answer to that."
            if stop_reason == "refusal" and not texts:
                text = "Sorry, I can't help with that one."
            sources = _turn_sources(sources, searched)
            answer = _append(db, thread, "assistant", "answer", content, text=text, actions=ctx.actions,
                             sources=sources, model=model)
            yield {"type": "done", "message_id": str(answer.id), "text": text, "actions": ctx.actions,
                   "sources": sources, "model": model}
            return

        results = []
        for call in tool_uses:
            if finished:  # cut off mid-call: answer it so the thread stays valid, but don't run it
                results.append({"type": "tool_result", "tool_use_id": call["id"], "is_error": True,
                                "content": "Not run: the response was cut off."})
                continue
            if call["name"] in STATUS:
                yield {"type": "status", "text": STATUS[call["name"]]}
            before = len(ctx.actions)
            output, is_error = run_tool(ctx, call["name"], call.get("input"))
            for action in ctx.actions[before:]:
                yield {"type": "action", "summary": action["summary"]}
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output, "is_error": is_error})
        _append(db, thread, "assistant", "step", content, model=model)
        _append(db, thread, "user", "step", results)
        messages += [{"role": "assistant", "content": content}, {"role": "user", "content": results}]

        if finished:
            text = "\n\n".join(texts) or "Sorry, that took too long to work out. Try asking a narrower question."
            sources = _turn_sources(sources, searched)
            answer = _append(db, thread, "assistant", "answer", [], text=text, actions=ctx.actions, sources=sources,
                             model=model)
            yield {"type": "done", "message_id": str(answer.id), "text": text, "actions": ctx.actions,
                   "sources": sources, "model": model}
            return


def new_thread(db: Session, user: str, question: str) -> ChatThread:
    title = " ".join(question.split())
    if len(title) > TITLE_LENGTH:
        title = title[: TITLE_LENGTH - 1].rsplit(" ", 1)[0] + "…"
    thread = ChatThread(owner=user, title=title)
    db.add(thread)
    db.commit()
    return thread


def discard_if_empty(thread_id: uuid.UUID) -> None:
    """A new conversation whose first question failed leaves nothing behind."""
    with SessionLocal() as db:
        thread = db.get(ChatThread, thread_id)
        if thread is not None and not db.scalar(select(ChatMessage.id).where(ChatMessage.thread_id == thread_id).limit(1)):
            db.delete(thread)
            db.commit()
