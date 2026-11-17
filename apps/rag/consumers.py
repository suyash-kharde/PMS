import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.accounts.permissions import has_cap
from apps.projects.selectors import visible_projects

from .models import ChatMessage, ChatSession
from .services.agent import ask_stream_events


class ChatConsumer(AsyncWebsocketConsumer):
    """One socket per open chat page. A Channels consumer already runs on its
    own asyncio event loop for the life of the connection, so — unlike the
    HTTP streaming endpoint this replaces — there's no sync/async bridge to
    build here: `ask_stream_events` can just be awaited directly."""

    async def connect(self):
        self.project_id = int(self.scope["url_route"]["kwargs"]["pk"])
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.project = await database_sync_to_async(
            lambda: visible_projects(self.user).filter(pk=self.project_id).first()
        )()
        if self.project is None:
            await self.close(code=4004)
            return
        if not await database_sync_to_async(has_cap)(self.user, "chat.view", self.project):
            await self.close(code=4003)
            return

        await self.accept()

    async def receive(self, text_data):
        payload = json.loads(text_data)
        question = (payload.get("question") or "").strip()
        use_rag = bool(payload.get("use_rag", True))
        use_sql = bool(payload.get("use_sql", True))
        if not question:
            await self.send(json.dumps({"type": "error", "error": "Ask something first."}))
            return

        session = await database_sync_to_async(self._get_session)()
        history = await database_sync_to_async(self._get_history)(session)
        await database_sync_to_async(ChatMessage.objects.create)(session=session, role="user", content=question)

        answer_parts = []
        try:
            async for event in ask_stream_events(
                self.project, question, history=history, use_rag=use_rag, use_sql=use_sql
            ):
                if event["type"] == "delta":
                    answer_parts.append(event["text"])
                    await self.send(json.dumps(event))
                elif event["type"] == "done":
                    trace = event["trace"]
                    sources = [
                        {"source_type": s["source_type"], "label": s["label"]} for s in trace.get("sources", [])
                    ]
                    sql_used = "\n".join(trace.get("sql", []))
                    await database_sync_to_async(ChatMessage.objects.create)(
                        session=session, role="assistant", content=event["answer"],
                        sources=sources, sql_used=sql_used,
                    )
                    await database_sync_to_async(session.save)(update_fields=["updated_at"])
                    await self.send(json.dumps({"type": "done", "sources": sources, "sql_used": sql_used}))
                elif event["type"] == "error":
                    await self._save_failure(session, answer_parts, event["error"])
                    await self.send(json.dumps(event))
        except Exception as exc:  # noqa: BLE001 - keep the socket alive, surface the failure instead
            await self._save_failure(session, answer_parts, f"{type(exc).__name__}: {exc}")
            await self.send(json.dumps({"type": "error", "error": f"{type(exc).__name__}: {exc}"}))

    async def _save_failure(self, session, answer_parts, error_text):
        partial = "".join(answer_parts).strip()
        content = f"{partial}\n\n[cut short: {error_text}]" if partial else f"Something went wrong: {error_text}"
        await database_sync_to_async(ChatMessage.objects.create)(session=session, role="assistant", content=content)
        await database_sync_to_async(session.save)(update_fields=["updated_at"])

    def _get_session(self):
        session, _ = ChatSession.objects.get_or_create(
            project=self.project, user=self.user, defaults={"title": f"Chat about {self.project.key}"}
        )
        return session

    def _get_history(self, session):
        return [(m.role, m.content) for m in session.messages.order_by("-created_at")[:6]][::-1]
