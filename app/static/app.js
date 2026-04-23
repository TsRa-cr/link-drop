function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatLocalTime(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function autosizeTextarea(textarea) {
  textarea.style.height = "0px";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

function renderFileContent(file) {
  const downloadButton = `<a class="inline-link" href="${file.download_url}" download>下载</a>`;
  const openButton = `<a class="inline-link" href="${file.media_url}" target="_blank" rel="noopener noreferrer">打开</a>`;
  const stats = `${escapeHtml(file.size_human)} · ${escapeHtml(file.mime_type || "application/octet-stream")}`;
  let preview = "";

  if (file.is_image && file.thumbnail_url) {
    preview = `<a href="${file.media_url}" target="_blank" rel="noopener noreferrer"><img class="file-preview-image" src="${file.thumbnail_url}" alt="${escapeHtml(file.original_name)}" loading="lazy" /></a>`;
  } else if (file.is_video) {
    preview = `<video controls preload="metadata" src="${file.media_url}"></video>`;
  } else if (file.is_audio) {
    preview = `<audio controls preload="metadata" src="${file.media_url}"></audio>`;
  } else if (file.is_text_previewable && file.preview_text) {
    preview = `<pre class="file-preview-text">${escapeHtml(file.preview_text)}</pre>`;
  } else {
    preview = `<div class="file-hint">该文件类型不做在线解析，可直接下载或在新标签页打开。</div>`;
  }

  return `
    <div class="file-card">
      ${preview}
      <div class="file-row">
        <div>
          <div class="file-name">${escapeHtml(file.original_name)}</div>
          <div class="file-stats">${stats}</div>
        </div>
        <div class="file-actions">
          ${openButton}
          ${downloadButton}
        </div>
      </div>
    </div>
  `;
}

function renderMessage(message, ownNickname) {
  const isSelf = message.sender_name === ownNickname;
  const body = message.kind === "file" && message.file
    ? renderFileContent(message.file)
    : `<p class="bubble-text">${escapeHtml(message.content || "").replaceAll("\n", "<br />")}</p>`;

  return `
    <article class="message-item ${isSelf ? "self" : "other"}" data-message-id="${message.id}">
      <div class="message-card">
        <div class="message-meta">
          <span class="sender-name">${escapeHtml(message.sender_name)}</span>
          <time class="message-time">${formatLocalTime(message.created_at_iso)}</time>
        </div>
        ${body}
      </div>
    </article>
  `;
}

function initRoomPage() {
  const state = window.LINKDROP_STATE;
  if (!state) {
    return;
  }

  const messageStream = document.querySelector("#message-stream");
  const composer = document.querySelector("#composer");
  const textarea = document.querySelector("#message-input");
  const fileInput = document.querySelector("#file-input");
  const uploadTrigger = document.querySelector("#upload-trigger");
  const sendTrigger = document.querySelector("#send-trigger");
  const dropzone = document.querySelector("#dropzone");
  const dropHint = document.querySelector("#drop-hint");
  const statusPill = document.querySelector("#connection-status");

  let socket = null;
  let reconnectDelay = 1200;
  let reconnectTimer = null;

  function setStatus(text, stateName) {
    statusPill.textContent = text;
    statusPill.dataset.state = stateName;
  }

  function scrollToBottom(force = false) {
    const distance = messageStream.scrollHeight - messageStream.scrollTop - messageStream.clientHeight;
    if (force || distance < 160) {
      messageStream.scrollTop = messageStream.scrollHeight;
    }
  }

  function renderEmptyState() {
    if (messageStream.children.length > 0) {
      return;
    }
    messageStream.innerHTML = `
      <div class="empty-state">
        当前房间还没有消息。发送第一条文字，或直接上传文件开始共享。
      </div>
    `;
  }

  function appendMessage(message, { forceScroll = false } = {}) {
    const emptyState = messageStream.querySelector(".empty-state");
    if (emptyState) {
      emptyState.remove();
    }

    if (messageStream.querySelector(`[data-message-id="${message.id}"]`)) {
      return;
    }

    messageStream.insertAdjacentHTML("beforeend", renderMessage(message, state.nickname));
    scrollToBottom(forceScroll || message.sender_name === state.nickname);
  }

  function loadInitialMessages() {
    messageStream.innerHTML = "";
    if (!Array.isArray(state.messages) || state.messages.length === 0) {
      renderEmptyState();
      return;
    }
    state.messages.forEach((message) => appendMessage(message));
    scrollToBottom(true);
  }

  function connectSocket() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/ws/rooms/${encodeURIComponent(state.roomCode)}?nickname=${encodeURIComponent(state.nickname)}`;

    setStatus("正在连接", "connecting");
    socket = new WebSocket(url);

    socket.addEventListener("open", () => {
      reconnectDelay = 1200;
      setStatus("实时连接已就绪", "connected");
    });

    socket.addEventListener("message", (event) => {
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        return;
      }

      if (payload.type === "ready") {
        setStatus("实时连接已就绪", "connected");
        return;
      }

      if (payload.type === "message" && payload.message) {
        appendMessage(payload.message);
        return;
      }

      if (payload.type === "error") {
        setStatus("连接异常，正在恢复", "disconnected");
      }
    });

    socket.addEventListener("close", () => {
      setStatus("连接已断开，正在重连", "disconnected");
      clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(connectSocket, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, 8000);
    });

    socket.addEventListener("error", () => {
      setStatus("连接异常，正在恢复", "disconnected");
    });
  }

  function sendMessage() {
    const content = textarea.value.trim();
    if (!content) {
      return;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setStatus("连接未就绪，请稍后重试", "disconnected");
      return;
    }

    socket.send(JSON.stringify({ type: "message", content }));
    textarea.value = "";
    autosizeTextarea(textarea);
  }

  async function uploadFiles(files) {
    if (!files || files.length === 0) {
      return;
    }

    uploadTrigger.disabled = true;
    const originalText = uploadTrigger.textContent;

    for (const file of files) {
      uploadTrigger.textContent = `上传中：${file.name}`;

      const formData = new FormData();
      formData.append("nickname", state.nickname);
      formData.append("file", file);

      try {
        const response = await fetch(`/api/rooms/${encodeURIComponent(state.roomCode)}/files`, {
          method: "POST",
          body: formData,
        });

        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "上传失败");
        }
      } catch (error) {
        setStatus(`上传失败：${error.message}`, "disconnected");
      }
    }

    uploadTrigger.disabled = false;
    uploadTrigger.textContent = originalText;
  }

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  textarea.addEventListener("input", () => autosizeTextarea(textarea));
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  uploadTrigger.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => uploadFiles(Array.from(fileInput.files || [])));
  fileInput.addEventListener("change", () => {
    window.setTimeout(() => {
      fileInput.value = "";
    }, 0);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
      dropHint.setAttribute("aria-hidden", "false");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
      dropHint.setAttribute("aria-hidden", "true");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length > 0) {
      uploadFiles(files);
    }
  });

  loadInitialMessages();
  autosizeTextarea(textarea);
  connectSocket();

  window.addEventListener("beforeunload", () => {
    clearTimeout(reconnectTimer);
    if (socket) {
      socket.close();
    }
  });

  sendTrigger.disabled = false;
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.dataset.page === "room") {
    initRoomPage();
  }
});
