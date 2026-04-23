from pydantic import BaseModel


class FilePayload(BaseModel):
    original_name: str
    size_bytes: int
    size_human: str
    mime_type: str
    download_url: str
    media_url: str
    thumbnail_url: str | None = None
    preview_text: str | None = None
    is_image: bool = False
    is_audio: bool = False
    is_video: bool = False
    is_text_previewable: bool = False


class MessagePayload(BaseModel):
    id: int
    kind: str
    sender_name: str
    content: str | None = None
    created_at_iso: str
    file: FilePayload | None = None


class WebSocketIncomingMessage(BaseModel):
    type: str
    content: str | None = None
