# Public E-Hentai API Service

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg )](https://www.gnu.org/licenses/agpl-3.0 )

一个高性能、带缓存、专为低功耗设备（如 RTOS 快应用）优化的 E-Hentai 公共 API 服务。

## ✨ 功能特性

- **高性能**: 使用 `Gunicorn` 作为 WSGI 服务器和 `PM2` 多进程管理，充分利用多核 CPU 性能。
- **全方位缓存**: 对 API 响应 (JSON) 和图片代理 (二进制) 进行双重内存缓存，大幅提升响应速度，降低对源站的请求压力。
- **专为嵌入式优化**:
    - 一次性返回所有图片链接，杜绝客户端二次请求。
    - 所有图片（包括缩略图）均在服务器端处理为 **JPEG** 格式。
    - 服务器端实现雪碧图（Sprite Sheet）的精确切割。
- **强大的图片处理**:
    - 支持动态调整图片宽度和压缩质量。
    - 强制将 WebP 等格式转换为兼容性更强的 JPEG。
- **健壮的翻页支持**: 完美兼容 E-Hentai 的游标翻页 (`next=gid`) 机制。
- **易于部署**: 提供详细的手动部署指南和一键安装脚本。

---

## 🚀 部署指南

我们提供两种部署方式：**一键安装脚本 (推荐)** 和 **手动部署**。

### 方式一：一键安装脚本 (推荐)

此脚本适用于一个全新的、基于 Debian 的系统 (如 Ubuntu)。它将自动完成所有环境配置和部署。

**1. 下载脚本**:
在一个全新的服务器上，下载 `install.sh` 脚本。
```bash
wget https://raw.githubusercontent.com/OrPudding/vela-py-eh-api-server/main/install.sh
```

**2. 运行脚本**:
赋予脚本执行权限并以 `sudo` 运行 。
```bash
chmod +x install.sh
sudo ./install.sh
```
脚本会引导您完成所有配置和安装步骤。它会自动检测并从 GitHub 克隆最新的项目代码，然后交互式地询问您需要配置的域名和目录，最后指导您完成 Nginx 和 SSL 的配置。

### 方式二：手动部署

如果您想在现有环境中手动部署，请遵循以下步骤。

#### a. 环境要求
- Linux 服务器
- Python 3.10+
- PM2 (Node.js 进程管理器)
- Nginx 或 OpenResty

#### b. 安装依赖
```bash
# 克隆项目到服务器
git clone https://github.com/OrPudding/vela-py-eh-api-server.git /opt/eh-api-service
cd /opt/eh-api-service

# 安装 Python 依赖
pip3 install --break-system-packages -r requirements.txt

# 全局安装 PM2
npm install pm2 -g
```

#### c. 使用 PM2 启动服务
项目内置了 `ecosystem.config.js` 配置文件 ，用于 `PM2`。
```bash
# 启动服务
pm2 start ecosystem.config.js

# 保存当前进程列表，以便服务器重启后自动恢复
pm2 save
```

#### d. 配置 Nginx 反向代理
配置您的 Nginx，将来自您域名的请求反向代理到本地的 `8000` 端口。一个简单的配置示例如下：
```nginx
server {
    listen 443 ssl http2;
    server_name your-api-domain.com; # 替换为您的域名

    # SSL 证书配置
    ssl_certificate /path/to/your/fullchain.pem;
    ssl_certificate_key /path/to/your/privkey.pem;
    
    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://127.0.0.1:8000;
    }
}
```

---

## 📖 API 接口文档

### 调用前须知

为了访问 ExHentai 或个性化内容 ，您需要提供 E-Hentai/ExHentai 的 Cookie。本项目支持通过 **HTTP 请求头** `X-EH-Cookie` 来手动传入 Cookie。

**请求示例**:
```
GET https://your-api-domain.com/search?q=language:chinese
Headers: {
  "X-EH-Cookie": "igneous=xxx; ipb_member_id=12345; ..."
}
```
> **注意**: 如果不提供此请求头 ，API 将以游客身份访问公开的 E-Hentai 内容。

### 1. 获取画廊列表 (首页/搜索)

#### 1.1 首页

**必选参数**: 无

**可选参数**:
`next`: 翻页游标。值为上一页返回的 `pagination.next_id`。

**接口地址**: `/`

**调用例子**:
- 获取首页第一页: `/`
- 翻页: `/?next=3645194`

**返回示例**:
```json
{
  "success": true,
  "keyword": "language:chinese",
  "galleries": [
    {
      "gid": 3645215,
      "token": "4db836130d",
      "url": "https://e-hentai.org/g/3645215/4db836130d/",
      "title": "[Chinese] 画廊标题",
      "thumbnail": "https://ehgt.org/xx/xxxxxxxx.jpg",
      "thumbnail_proxy": "/image/proxy?url=https://ehgt.org/xx/xxxxxxxx.jpg&w=200&q=40",
      "posted": "2025-11-19 12:34",
      "category": "Doujinshi",
      "rating": 4.5,
      "uploader": "some_user",
      "pages": 25
    }
  ],
  "pagination": {
    "has_next": true,
    "next_id": "3644809"
  }
}
```

#### 1.2 搜索

**必选参数**:
`q`: 搜索关键词，例如 `language:chinese`。

**可选参数**:
`next`: 翻页游标。值为上一页返回的 `pagination.next_id`。

**接口地址**: `/search`

**调用例子**:
- 搜索第一页: `/search?q=language:chinese`
- 搜索翻页: `/search?q=language:chinese&next=3644809`


**返回示例**:
```json
{
  "success": true,
  "keyword": "language:chinese",
  "galleries": [
    {
      "gid": 3645215,
      "token": "4db836130d",
      "url": "https://e-hentai.org/g/3645215/4db836130d/",
      "title": "[Chinese] 画廊标题",
      "thumbnail": "https://ehgt.org/xx/xxxxxxxx.jpg",
      "thumbnail_proxy": "/image/proxy?url=https://ehgt.org/xx/xxxxxxxx.jpg&w=200&q=40",
      "posted": "2025-11-19 12:34",
      "category": "Doujinshi",
      "rating": 4.5,
      "uploader": "some_user",
      "pages": 25
    }
  ],
  "pagination": {
    "has_next": true,
    "next_id": "3644809"
  }
}
```

### 2. 获取画廊详情

**必选参数**:
`gid`: 画廊 ID。
`token`: 画廊 Token。

**接口地址**: `/gallery/<gid>/<token>`

**调用例子**: `/gallery/3645215/4db836130d`

**返回示例**:
```json
{
  "success": true,
  "gallery": {
    "gid": 3645215,
    "token": "4db836130d",
    "title": "[Chinese] 画廊标题",
    "title_jp": "[日本語] 画廊日文标题",
    "category": "Doujinshi",
    "thumbnail": "https://ehgt.org/xx/xxxxxxxx.jpg",
    "thumbnail_proxy": "/image/proxy?url=https://ehgt.org/xx/xxxxxxxx.jpg&w=200&q=40",
    "rating": 4.5,
    "pages": 25,
    "tags": {
      "language": ["chinese", "translated"],
      "artist": ["artist_name"],
      "female": ["big breasts", "sole female"],
      "male": ["sole male"]
    }
  }
}
```

### 3. 获取画廊图片列表

**说明**: 这是本 API 的核心功能。它会一次性返回该画廊指定页码下所有图片的代理链接，包括处理好的大图和缩略图。**此过程在服务器端是并发处理的，可能会有一定耗时，但结果会被缓存 1 小时。**

**必选参数**:
`gid`: 画廊 ID。
`token`: 画廊 Token。

**可选参数**:
`page`: 图片列表的页码，从 0 开始。默认为 0。

**接口地址**: `/gallery/<gid>/<token>/images`

**调用例子**:
- 获取第一页图片: `/gallery/3645215/4db836130d/images`
- 获取第二页图片: `/gallery/3645215/4db836130d/images?page=1`

**返回示例**:
```json
{
  "success": true,
  "gid": 3645215,
  "token": "4db836130d",
  "page": 1,
  "count": 5,
  "images": [
    {
      "index": 0,
      "absolute_index": 20,
      "thumbnail_jpg": "/image/proxy?url=...&crop_x=...",
      "image_jpg": "/image/proxy?url=..."
    },
    {
      "index": 1,
      "absolute_index": 21,
      "thumbnail_jpg": "/image/proxy?url=...&crop_x=...",
      "image_jpg": "/image/proxy?url=..."
    }
  ]
}
```

### 4. 图片代理服务

**说明**: 此接口用于获取经过服务器处理（切割、压缩、转码）后的 JPEG 图片。它由其他接口返回的 `thumbnail_jpg` 和 `image_jpg` 链接自动调用，通常不需要手动拼接。

**必选参数**:
`url`: 原始图片 URL。

**可选参数**:
- `w`: 图片最大宽度。默认为 `400`。
- `q`: JPEG 压缩质量 (1-100)。默认为 `50`。
- `crop_x`, `crop_y`, `crop_w`, `crop_h`: 用于切割雪碧图的参数，由系统自动生成。

**接口地址**: `/image/proxy`

**调用例子**:
- 代理大图: `/image/proxy?url=https://.../image.webp`
- 代理并切割缩略图: `/image/proxy?url=https://.../sprite.webp&crop_x=200&crop_y=0&crop_w=200&crop_h=282&w=200&q=40`

**返回内容**:
- **成功**: 返回 `Content-Type: image/jpeg` 的图片二进制数据。
- **失败**: 返回 `Content-Type: application/json` 的错误信息，例如 `{"error": "无法下载或处理图片"}`。


### 5. 健康检查

**说明**: 用于检查 API 服务是否在线 。

**必选参数**: 无

**接口地址**: `/health`

**调用例子**: `/health`

**返回示例**:
```json
{
  "status": "ok",
  "client_cookie_provided": true
}
```

---

## ⚖️ 许可

本软件根据 **GNU Affero General Public License v3.0** 许可。详情请参阅 `LICENSE` 文件。
