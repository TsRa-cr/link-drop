from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(24), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    messages = relationship("Message", back_populates="room", cascade="all, delete-orphan")


class FileAsset(Base):
    __tablename__ = "file_assets"

    id = Column(Integer, primary_key=True, index=True)
    original_name = Column(String(255), nullable=False)
    stored_name = Column(String(255), unique=True, nullable=False, index=True)
    relative_path = Column(String(255), nullable=False)
    thumbnail_name = Column(String(255), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    mime_type = Column(String(255), nullable=True)
    preview_text = Column(Text, nullable=True)
    is_image = Column(Boolean, default=False, nullable=False)
    is_audio = Column(Boolean, default=False, nullable=False)
    is_video = Column(Boolean, default=False, nullable=False)
    is_text_previewable = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    messages = relationship("Message", back_populates="file")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_name = Column(String(60), nullable=False)
    kind = Column(String(16), nullable=False, default="text")
    content = Column(Text, nullable=True)
    file_id = Column(Integer, ForeignKey("file_assets.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    room = relationship("Room", back_populates="messages")
    file = relationship("FileAsset", back_populates="messages")
