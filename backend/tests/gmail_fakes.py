import base64
from types import SimpleNamespace

import httplib2
from googleapiclient.errors import HttpError

# 2026-11-03T15:30:00Z
DEFAULT_INTERNAL_MS = 1793719800000


def gmail_message(message_id, sender, subject, body, *, html=False, labels=("INBOX",), internal_ms=DEFAULT_INTERNAL_MS):
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "id": message_id,
        "labelIds": list(labels),
        "internalDate": str(internal_ms),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
            "parts": [{"mimeType": "text/html" if html else "text/plain", "body": {"data": data}}],
        },
    }


def _call(fn):
    return SimpleNamespace(execute=fn)


class FakeGmail:
    """Just enough of googleapiclient's Gmail resource for gmail_sync."""

    def __init__(self, messages=(), history_pages=(), history_error=None, profile_history_id=500, search_ids=None):
        self.store = {m["id"]: m for m in messages}
        self.history_pages = list(history_pages)
        self.history_error = history_error
        self.profile_history_id = profile_history_id
        self.search_ids = list(self.store) if search_ids is None else search_ids
        self.fetched: list[str] = []
        self.watch_calls: list[dict] = []
        self.history_starts: list[int] = []

    def users(self):
        return SimpleNamespace(
            messages=lambda: SimpleNamespace(get=self._get, list=self._list),
            history=lambda: SimpleNamespace(list=self._history),
            getProfile=lambda userId: _call(
                lambda: {"emailAddress": "me@example.com", "historyId": str(self.profile_history_id)}
            ),
            watch=self._watch,
        )

    def _get(self, userId, id, format):
        def run():
            self.fetched.append(id)
            if id not in self.store:
                raise not_found()
            return self.store[id]

        return _call(run)

    def _list(self, userId, q, pageToken=None):
        return _call(lambda: {"messages": [{"id": i} for i in self.search_ids]})

    def _history(self, userId, startHistoryId, historyTypes, pageToken=None):
        def run():
            self.history_starts.append(startHistoryId)
            if self.history_error is not None:
                raise self.history_error
            return self.history_pages[int(pageToken or 0)]

        return _call(run)

    def _watch(self, userId, body):
        self.watch_calls.append(body)
        return _call(lambda: {"historyId": "100", "expiration": "1794324600000"})


def history_page(*message_ids, history_id, next_page=None):
    page = {"history": [{"messagesAdded": [{"message": {"id": i}}]} for i in message_ids], "historyId": str(history_id)}
    if next_page is not None:
        page["nextPageToken"] = str(next_page)
    return page


def not_found():
    return HttpError(httplib2.Response({"status": 404}), b"history expired")
