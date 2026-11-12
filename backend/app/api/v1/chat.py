"""The chat assistant. Conversations and memories are private: each person only sees their own."""
import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.security import require_token
from app.db.base import SessionLocal, get_db
from app.db.models import ChatMemory, ChatThread
from app.schemas import (
    ChatIn,
    ChatMemoryIn,
    ChatMemoryOut,
    ChatReply,
    ChatThreadDetail,
    ChatThreadOut,
    ChatUsageOut,
)
from app.services import chat as chat_service

router = APIRouter(prefix="/chat", tags=["chat"])
UNAVAILABLE = "The AI model is unavailable right now, try again soon"


def _own_thread(db: Session, thread_id: uuid.UUID, user: str) -> ChatThread:
    thread = db.scalar(select(ChatThread).where(ChatThread.id == thread_id).options(selectinload(ChatThread.messages)))
    if thread is None or thread.owner != user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return thread


@router.post("", response_model=ChatReply, responses={200: {"content": {"text/event-stream": {}}}})
def ask(body: ChatIn, request: Request, db: Session = Depends(get_db), user: str = Depends(require_token)):
    """Ask a question (or continue a conversation with thread_id).

    With `Accept: text/event-stream` the answer streams as server-sent events: `thread` ({thread_id, title}) first,
    then `status` ({text}: what it's looking at), `text` ({text}: the next piece of the answer) and `action`
    ({summary}: a change it made), ending with `done` (the ChatReply) or `error` ({detail}). Otherwise the
    reply comes back as JSON once it's complete.
    """
    if not get_settings().ai_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Chat needs an AI model: ANTHROPIC_API_KEY, or a model server (LLM_PROVIDER=openai)")
    if chat_service.questions_left(db, user) == 0:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"You've asked {get_settings().chat_monthly_limit} questions this month: the limit resets "
                            "on the 1st")
    if body.thread_id is not None:
        thread = _own_thread(db, body.thread_id, user)
        if chat_service.thread_questions(db, thread) >= chat_service.MAX_QUESTIONS_PER_THREAD:
            raise HTTPException(status.HTTP_409_CONFLICT, "This conversation is full: start a new one")
        thread_id = thread.id
    else:
        thread_id = chat_service.new_thread(db, user, body.message).id

    def events() -> Iterator[dict]:
        # Its own session: the request's closes before a streamed response finishes.
        with SessionLocal() as session:
            thread = _own_thread(session, thread_id, user)
            yield {"type": "thread", "thread_id": str(thread.id), "title": thread.title}
            for event in chat_service.run_turn(session, chat_service.chat_client(), user, thread, body.message):
                if event["type"] == "done":
                    event = {**event, "thread_id": str(thread.id),
                             "questions_left": chat_service.questions_left(session, user)}
                yield event

    if "text/event-stream" in request.headers.get("accept", ""):
        def sse() -> Iterator[str]:
            try:
                for event in events():
                    name = event.pop("type")
                    yield f"event: {name}\ndata: {json.dumps(event)}\n\n"
            except chat_service.ChatFailed:
                chat_service.discard_if_empty(thread_id)
                yield f"event: error\ndata: {json.dumps({'detail': UNAVAILABLE})}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        done = [e for e in events() if e["type"] == "done"][-1]
    except chat_service.ChatFailed:
        chat_service.discard_if_empty(thread_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, UNAVAILABLE)
    return ChatReply.model_validate(done)


@router.get("/usage", response_model=ChatUsageOut)
def usage(db: Session = Depends(get_db), user: str = Depends(require_token)) -> ChatUsageOut:
    limit = get_settings().chat_monthly_limit
    return ChatUsageOut(used=chat_service.questions_this_month(db, user), limit=limit or None,
                        left=chat_service.questions_left(db, user), available=get_settings().ai_enabled(),
                        web_search=chat_service.web_search_enabled())


@router.get("/threads", response_model=list[ChatThreadOut])
def list_threads(db: Session = Depends(get_db), user: str = Depends(require_token)) -> list[ChatThread]:
    return list(db.scalars(select(ChatThread).where(ChatThread.owner == user).order_by(ChatThread.updated_at.desc())))


@router.get("/threads/{thread_id}", response_model=ChatThreadDetail)
def get_thread(thread_id: uuid.UUID, db: Session = Depends(get_db), user: str = Depends(require_token)) -> ChatThreadDetail:
    thread = _own_thread(db, thread_id, user)
    shown = [m for m in thread.messages if m.kind in ("question", "answer")]
    return ChatThreadDetail(
        id=thread.id, title=thread.title, created_at=thread.created_at, updated_at=thread.updated_at,
        messages=shown,
        can_continue=sum(m.kind == "question" for m in shown) < chat_service.MAX_QUESTIONS_PER_THREAD,
    )


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(thread_id: uuid.UUID, db: Session = Depends(get_db), user: str = Depends(require_token)) -> None:
    db.delete(_own_thread(db, thread_id, user))
    db.commit()


@router.get("/memory", response_model=list[ChatMemoryOut])
def list_memory(db: Session = Depends(get_db), user: str = Depends(require_token)) -> list[ChatMemory]:
    return list(db.scalars(select(ChatMemory).where(ChatMemory.owner == user).order_by(ChatMemory.created_at)))


@router.post("/memory", response_model=ChatMemoryOut, status_code=status.HTTP_201_CREATED)
def add_memory(body: ChatMemoryIn, db: Session = Depends(get_db), user: str = Depends(require_token)) -> ChatMemory:
    memory = ChatMemory(owner=user, fact=body.fact.strip())
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: uuid.UUID, db: Session = Depends(get_db), user: str = Depends(require_token)) -> None:
    memory = db.get(ChatMemory, memory_id)
    if memory is None or memory.owner != user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    db.delete(memory)
    db.commit()
