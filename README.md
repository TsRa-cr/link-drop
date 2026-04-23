# LinkDrop

LinkDrop 是一个可部署到单台 Linux VPS 的极简实时互传网站，支持：

- 同一房间内多人实时文字聊天
- 手机和电脑之间互传文件
- 图片缩略图预览
- 文本和代码文件基础预览
- 音视频基础播放器预览
- SQLite 持久化消息和文件记录

技术栈：

- FastAPI
- Jinja2 模板
- 原生 HTML / CSS / JavaScript
- WebSocket
- SQLite
- 本地 `uploads/` 存储

## 项目结构

```text
LinkDrop/
├── app/
│   ├── db.py
│   ├── main.py
│   ├── models.py
│   ├── schemas.py
│   ├── static/
│   │   ├── app.js
│   │   └── style.css
│   └── templates/
│       ├── base.html
│       ├── index.html
│       └── room.html
├── deploy/
│   └── Caddyfile
├── data/
├── uploads/
├── .env.example
├── README.md
└── requirements.txt
```

## 本地运行

1. 创建虚拟环境

```bash
cd /home/linuxuser/LinkDrop
python3 -m venv .venv
source .venv/bin/activate
```

2. 安装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

3. 可选：导入环境变量

```bash
cp .env.example .env
set -a
source .env
set +a
```

如果你只是本地试跑，也可以不加载 `.env`，应用会默认使用：

- SQLite 数据库：`./data/linkdrop.db`
- 上传目录：`./uploads`
- 单文件大小限制：`128 MB`

4. 使用 uvicorn 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

5. 打开浏览器

```text
http://127.0.0.1:8000
```

## VPS 部署步骤

下面以 `/opt/linkdrop` 为例。

1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip caddy
```

2. 上传项目并放到目标目录

```bash
sudo mkdir -p /opt/linkdrop
sudo chown -R $USER:$USER /opt/linkdrop
cd /opt/linkdrop
```

3. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

4. 准备运行目录和环境变量

```bash
mkdir -p /opt/linkdrop/data /opt/linkdrop/uploads
cp .env.example .env
```

按需修改 `.env`，至少确认以下两项：

- `LINKDROP_DATABASE_URL`
- `LINKDROP_UPLOAD_DIR`

5. 手动启动验证

```bash
set -a
source /opt/linkdrop/.env
set +a
/opt/linkdrop/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

确认本机访问 `http://127.0.0.1:8000/healthz` 返回 `{"ok": true, ...}` 后，再接反向代理。

## 如何用 uvicorn 启动

开发环境：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

生产环境：

```bash
set -a
source /opt/linkdrop/.env
set +a
/opt/linkdrop/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 如何配合 Caddy 反代

1. 修改 [deploy/Caddyfile](/home/linuxuser/LinkDrop/deploy/Caddyfile) 中的域名，把 `your-domain.com` 改成你的真实域名。

2. 复制到 Caddy 配置目录

```bash
sudo cp /opt/linkdrop/deploy/Caddyfile /etc/caddy/Caddyfile
```

3. 检查配置并重载

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 会自动处理 HTTPS 证书，WebSocket 也会跟随 `reverse_proxy` 正常转发。

## 上传目录权限注意事项

- `uploads/` 必须对运行 FastAPI 的系统用户可写。
- `uploads/.thumbs/` 会自动创建，用于存图片缩略图，也需要可写。
- 不要把 `uploads/` 配成可执行目录，本项目只保存和分发文件，不执行任何上传内容。
- 如果你用独立用户运行应用，例如 `linkdrop`，可执行：

```bash
sudo useradd --system --home /opt/linkdrop --shell /usr/sbin/nologin linkdrop
sudo chown -R linkdrop:linkdrop /opt/linkdrop
sudo chmod -R 750 /opt/linkdrop
sudo chmod -R 770 /opt/linkdrop/uploads /opt/linkdrop/data
```

## 建议的 systemd 服务

创建 `/etc/systemd/system/linkdrop.service`：

```ini
[Unit]
Description=LinkDrop FastAPI App
After=network.target

[Service]
User=linkdrop
Group=linkdrop
WorkingDirectory=/opt/linkdrop
EnvironmentFile=/opt/linkdrop/.env
ExecStart=/opt/linkdrop/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now linkdrop
sudo systemctl status linkdrop
```

## 安全与文件处理说明

- 第一版不基于扩展名白名单阻止上传，重点控制大小、文件名和存储路径。
- 文件保存时使用唯一文件名，原始文件名仅作为展示和下载名称写入数据库。
- 图片会生成缩略图，原图仍通过下载或媒体地址访问。
- 文本和代码文件会做基础文本预览，预览长度有限。
- 音视频文件提供浏览器基础播放能力。
- 其他类型统一以文件卡片展示，不做自动解压、执行或高风险解析。

## 默认访问路径

- 首页：`/`
- 房间页：`/rooms/{room_code}`
- 健康检查：`/healthz`

## 后续可扩展方向

- 限制房间历史消息数量
- 增加上传进度条
- 增加过期清理脚本
- 增加可选的房间密码
