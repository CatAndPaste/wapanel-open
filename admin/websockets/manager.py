import json
from typing import Set, Dict
from fastapi import WebSocket


class WSManager:
    def __init__(self):
        self._conns: dict[WebSocket, set[int] | None] = {}  # None -> full_access

    async def connect(self, ws: WebSocket, allowed: set[int] | None):
        await ws.accept()
        self._conns[ws] = allowed

    def disconnect(self, ws: WebSocket):
        self._conns.pop(ws, None)

    async def broadcast(self, raw_payload: str):
        data = json.loads(raw_payload)
        inst_id = data.get("id")

        to_drop = []
        for ws, allowed in self._conns.items():
            try:
                if allowed is None or inst_id is None or inst_id in allowed:
                    await ws.send_text(raw_payload)
            except Exception:
                to_drop.append(ws)

        for ws in to_drop:
            self.disconnect(ws)


class ChatWSManager:
    """
    Key - websocket, value – (instance_id, chat_id) or None (listens to all)
    """
    def __init__(self) -> None:
        self._conns: Dict[WebSocket, tuple[int, str] | None] = {}

    async def connect(self, ws: WebSocket,
                      inst_id: int, chat_id: str) -> None:
        await ws.accept()
        self._conns[ws] = (inst_id, chat_id)

    def disconnect(self, ws: WebSocket) -> None:
        self._conns.pop(ws, None)

    async def broadcast(self, raw: str) -> None:
        data = json.loads(raw)
        inst_id = data["inst_id"]
        chat_id = data["chat_id"]

        drop: Set[WebSocket] = set()
        for ws, scope in self._conns.items():
            try:
                if scope is None:
                    await ws.send_text(raw)
                else:
                    i, c = scope
                    if i == inst_id and c == chat_id:
                        await ws.send_text(raw)
            except Exception:
                drop.add(ws)

        for ws in drop:
            self.disconnect(ws)
