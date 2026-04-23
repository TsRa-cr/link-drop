import asyncio
import json
import mimetypes
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal, get_db, init_db
from app.models import FileAsset, Message, Room


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"
UPLOAD_DIR = Path(os.getenv("LINKDROP_UPLOAD_DIR", str(BASE_DIR / "uploads"))).resolve()
THUMBNAIL_DIR = UPLOAD_DIR / ".thumbs"
MAX_UPLOAD_MB = int(os.getenv("LINKDROP_MAX_UPLOAD_MB", "128"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_LENGTH = int(os.getenv("LINKDROP_MAX_MESSAGE_LENGTH", "4000"))
MAX_PREVIEW_CHARS = int(os.getenv("LINKDROP_MAX_PREVIEW_CHARS", "2400"))
ROOM_CODE_LENGTH = int(os.getenv("LINKDROP_ROOM_CODE_LENGTH", "6"))

TEXT_PREVIEW_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".jsonc",
    ".xml",
    ".csv",
    ".tsv",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".py",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".swift",
    ".php",
    ".c",
    ".h",
    ".hpp",
    ".cpp",
    ".cs",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".sql",
    ".vue",
    ".svelte",
    ".dockerfile",
    ".editorconfig",
}

TEXT_PREVIEW_NAMES = {
    ".env",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    "dockerfile",
    "makefile",
    "procfile",
    "license",
    "readme",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title="LinkDrop", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, room_code: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(room_code, set()).add(websocket)

    async def disconnect(self, room_code: str, websocket: WebSocket) -> None:
        async with self._lock:
            room_connections = self._connections.get(room_code)
            if not room_connections:
                return
            room_connections.discard(websocket)
            if not room_connections:
                self._connections.pop(room_code, None)

    async def broadcast(self, room_code: str, payload: dict[str, Any]) -> None:
        room_connections = list(self._connections.get(room_code, set()))
        if not room_connections:
            return

        stale_connections: list[WebSocket] = []
        for websocket in room_connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale_connections.append(websocket)

        for websocket in stale_connections:
            await self.disconnect(room_code, websocket)


manager = ConnectionManager()


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def normalize_nickname(raw_value: str | None) -> str:
    value = re.sub(r"\s+", " ", (raw_value or "").strip())
    if not value:
        return f"Guest-{secrets.randbelow(9000) + 1000}"
    return value[:32]


def normalize_room_code(raw_value: str | None) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]", "", (raw_value or "").strip().upper())
    return value[:24]


def normalize_message_content(raw_value: str | None) -> str:
    value = (raw_value or "").replace("\r\n", "\n").strip()
    return value[:MAX_MESSAGE_LENGTH]


def sanitize_filename(raw_filename: str | None) -> str:
    source_name = Path(raw_filename or "file").name
    source_name = re.sub(r"[^\w.\-() ]+", "_", source_name).strip(" .")
    return source_name[:180] or "file"


def format_bytes(size: int) -> str:
    value = float(size)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def build_room_code(db: Session) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(ROOM_CODE_LENGTH))
        exists = db.query(Room.id).filter(Room.code == candidate).first()
        if not exists:
            return candidate


def get_room_by_code(db: Session, room_code: str) -> Room | None:
    normalized = normalize_room_code(room_code)
    if not normalized:
        return None
    return db.query(Room).filter(Room.code == normalized).first()


def should_preview_text(filename: str, mime_type: str) -> bool:
    name = Path(filename).name.lower()
    suffix = Path(filename).suffix.lower()
    if mime_type.startswith("text/"):
        return True
    if name in TEXT_PREVIEW_NAMES:
        return True
    return suffix in TEXT_PREVIEW_SUFFIXES


def extract_text_preview(file_path: Path) -> str | None:
    try:
        with file_path.open("rb") as handle:
            raw_bytes = handle.read(MAX_PREVIEW_CHARS * 4)
    except OSError:
        return None

    raw_text = raw_bytes.decode("utf-8", errors="replace")
    preview = raw_text[:MAX_PREVIEW_CHARS].strip()
    if not preview:
        return None
    if len(raw_text) >= MAX_PREVIEW_CHARS:
        preview += "\n\n... preview truncated ..."
    return preview


def create_thumbnail(original_path: Path, stored_name: str) -> str | None:
    thumbnail_name = f"{Path(stored_name).stem}.webp"
    thumbnail_path = THUMBNAIL_DIR / thumbnail_name
    try:
        with Image.open(original_path) as image:
            normalized = ImageOps.exif_transpose(image)
            normalized.thumbnail((720, 520))
            normalized.save(thumbnail_path, format="WEBP", quality=82, method=6)
    except (OSError, UnidentifiedImageError):
        return None
    return thumbnail_name


def serialize_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": message.id,
        "kind": message.kind,
        "sender_name": message.sender_name,
        "content": message.content,
        "created_at_iso": message.created_at.replace(microsecond=0).isoformat() + "Z",
        "file": None,
    }

    if message.file:
        file_record = message.file
        payload["file"] = {
            "original_name": file_record.original_name,
            "size_bytes": file_record.size_bytes,
            "size_human": format_bytes(file_record.size_bytes),
            "mime_type": file_record.mime_type or "application/octet-stream",
            "download_url": f"/download/{quote(file_record.stored_name)}",
            "media_url": f"/media/{quote(file_record.stored_name)}",
            "thumbnail_url": f"/thumbs/{quote(file_record.thumbnail_name)}" if file_record.thumbnail_name else None,
            "preview_text": file_record.preview_text,
            "is_image": file_record.is_image,
            "is_audio": file_record.is_audio,
            "is_video": file_record.is_video,
            "is_text_previewable": file_record.is_text_previewable,
        }

    return payload


async def persist_upload(upload: UploadFile) -> tuple[str, Path, int]:
    original_name = sanitize_filename(upload.filename)
    suffix = Path(original_name).suffix.lower()
    stored_name = f"{uuid4().hex}{suffix}"
    destination = UPLOAD_DIR / stored_name
    total_size = 0

    try:
        with destination.open("wb") as output_file:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_BYTES:
                    output_file.close()
                    destination.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"Single file limit is {MAX_UPLOAD_MB} MB.")
                output_file.write(chunk)
    finally:
        await upload.close()

    if total_size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Empty files cannot be uploaded.")

    return original_name, destination, total_size


def create_file_message(
    db: Session,
    room: Room,
    sender_name: str,
    original_name: str,
    content_type: str | None,
    stored_name: str,
    saved_path: Path,
    size_bytes: int,
) -> Message:
    mime_type = (content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream").lower()
    is_image = mime_type.startswith("image/")
    is_audio = mime_type.startswith("audio/")
    is_video = mime_type.startswith("video/")
    is_text_previewable = should_preview_text(original_name, mime_type)
    preview_text = extract_text_preview(saved_path) if is_text_previewable else None
    thumbnail_name = create_thumbnail(saved_path, stored_name) if is_image else None

    file_asset = FileAsset(
        original_name=original_name,
        stored_name=stored_name,
        relative_path=saved_path.name,
        thumbnail_name=thumbnail_name,
        size_bytes=size_bytes,
        mime_type=mime_type,
        preview_text=preview_text,
        is_image=is_image,
        is_audio=is_audio,
        is_video=is_video,
        is_text_previewable=is_text_previewable,
    )
    db.add(file_asset)
    db.flush()

    message = Message(
        room_id=room.id,
        sender_name=sender_name,
        kind="file",
        file_id=file_asset.id,
    )
    message.file = file_asset
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": None,
            "nickname_value": "",
            "room_code_value": "",
            "page_title": "LinkDrop",
        },
    )


@app.post("/rooms/create")
def create_room(nickname: str = Form(""), db: Session = Depends(get_db)):
    sender_name = normalize_nickname(nickname)
    room = Room(code=build_room_code(db))
    db.add(room)
    db.commit()
    return RedirectResponse(url=f"/rooms/{room.code}?nickname={quote(sender_name)}", status_code=303)


@app.post("/rooms/join", response_class=HTMLResponse)
def join_room(
    request: Request,
    nickname: str = Form(""),
    room_code: str = Form(""),
    db: Session = Depends(get_db),
):
    sender_name = normalize_nickname(nickname)
    normalized_code = normalize_room_code(room_code)
    room = get_room_by_code(db, normalized_code)

    if not normalized_code or not room:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "error": "房间不存在，请检查房间号后再试。",
                "nickname_value": sender_name,
                "room_code_value": normalized_code,
                "page_title": "LinkDrop",
            },
            status_code=404,
        )

    return RedirectResponse(url=f"/rooms/{room.code}?nickname={quote(sender_name)}", status_code=303)


@app.get("/rooms/{room_code}", response_class=HTMLResponse)
def room_page(request: Request, room_code: str, nickname: str = "", db: Session = Depends(get_db)):
    room = get_room_by_code(db, room_code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found.")

    sender_name = normalize_nickname(nickname)
    messages = (
        db.query(Message)
        .options(joinedload(Message.file))
        .filter(Message.room_id == room.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    payloads = [serialize_message(message) for message in messages]

    state = {
        "roomCode": room.code,
        "nickname": sender_name,
        "maxUploadMb": MAX_UPLOAD_MB,
        "messages": payloads,
    }

    return templates.TemplateResponse(
        request=request,
        name="room.html",
        context={
            "room": room,
            "nickname": sender_name,
            "state": state,
            "page_title": f"Room {room.code} - LinkDrop",
        },
    )


@app.post("/api/rooms/{room_code}/files")
async def upload_file(
    room_code: str,
    nickname: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    room = get_room_by_code(db, room_code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found.")

    sender_name = normalize_nickname(nickname)
    original_name, saved_path, size_bytes = await persist_upload(file)
    try:
        message = create_file_message(
            db,
            room,
            sender_name,
            original_name,
            file.content_type,
            saved_path.name,
            saved_path,
            size_bytes,
        )
    except Exception:
        saved_path.unlink(missing_ok=True)
        possible_thumbnail = THUMBNAIL_DIR / f"{Path(saved_path.name).stem}.webp"
        possible_thumbnail.unlink(missing_ok=True)
        raise
    if not message.file:
        raise HTTPException(status_code=500, detail="Failed to create file metadata.")

    payload = serialize_message(message)
    await manager.broadcast(room.code, {"type": "message", "message": payload})
    return JSONResponse({"ok": True, "message": payload, "uploaded_name": original_name})


@app.websocket("/ws/rooms/{room_code}")
async def room_socket(websocket: WebSocket, room_code: str, nickname: str = ""):
    normalized_code = normalize_room_code(room_code)
    sender_name = normalize_nickname(nickname)

    with SessionLocal() as db:
        room = get_room_by_code(db, normalized_code)
        if not room:
            await websocket.close(code=4404)
            return

    await manager.connect(normalized_code, websocket)
    await websocket.send_json({"type": "ready"})

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                incoming = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid message payload."})
                continue

            if incoming.get("type") != "message":
                continue

            content = normalize_message_content(incoming.get("content"))
            if not content:
                continue

            with SessionLocal() as db:
                room = get_room_by_code(db, normalized_code)
                if not room:
                    await websocket.send_json({"type": "error", "detail": "Room is unavailable."})
                    continue

                message = Message(room_id=room.id, sender_name=sender_name, kind="text", content=content)
                db.add(message)
                db.commit()
                db.refresh(message)
                payload = serialize_message(message)

            await manager.broadcast(normalized_code, {"type": "message", "message": payload})
    except WebSocketDisconnect:
        await manager.disconnect(normalized_code, websocket)
    except Exception:
        await manager.disconnect(normalized_code, websocket)


@app.get("/download/{stored_name}")
def download_file(stored_name: str, db: Session = Depends(get_db)):
    file_asset = db.query(FileAsset).filter(FileAsset.stored_name == stored_name).first()
    if not file_asset:
        raise HTTPException(status_code=404, detail="File not found.")

    absolute_path = UPLOAD_DIR / file_asset.relative_path
    if not absolute_path.exists():
        raise HTTPException(status_code=404, detail="Stored file is missing.")

    return FileResponse(
        path=str(absolute_path),
        media_type="application/octet-stream",
        filename=file_asset.original_name,
    )


@app.get("/media/{stored_name}")
def open_media(stored_name: str, db: Session = Depends(get_db)):
    file_asset = db.query(FileAsset).filter(FileAsset.stored_name == stored_name).first()
    if not file_asset:
        raise HTTPException(status_code=404, detail="File not found.")

    absolute_path = UPLOAD_DIR / file_asset.relative_path
    if not absolute_path.exists():
        raise HTTPException(status_code=404, detail="Stored file is missing.")

    return FileResponse(path=str(absolute_path), media_type=file_asset.mime_type or "application/octet-stream")


@app.get("/thumbs/{thumbnail_name}")
def thumbnail_image(thumbnail_name: str):
    normalized_name = Path(thumbnail_name).name
    absolute_path = THUMBNAIL_DIR / normalized_name
    if not absolute_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(path=str(absolute_path), media_type="image/webp")


@app.get("/healthz")
def health_check():
    return {"ok": True, "timestamp": datetime.utcnow().isoformat() + "Z"}
