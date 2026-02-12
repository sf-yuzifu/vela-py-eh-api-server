# Public E-Hentai API Service

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

一个高性能、带缓存、专为低功耗设备（如 RTOS 快应用）优化的 E-Hentai 公共 API 服务。符合自定义漫画源规范，可直接用于漫画阅读应用。

## ✨ 功能特性

- **高性能**: 使用 `Gunicorn` 作为 WSGI 服务器和 `PM2` 多进程管理，充分利用多核 CPU 性能。
- **全方位缓存**: 
  - API 响应缓存 (5分钟)
  - 画廊详情缓存 (1小时)
  - 图片代理缓存 (24小时)
  - 分页游标缓存 (10分钟)
- **专为嵌入式优化**:
    - 一次性返回所有图片链接，杜绝客户端二次请求。
    - 所有图片（包括缩略图）均在服务器端处理为 **JPEG** 格式。
    - 服务器端实现雪碧图（Sprite Sheet）的精确切割。
- **强大的图片处理**:
    - 支持动态调整图片宽度和压缩质量。
    - 强制将 WebP 等格式转换为兼容性更强的 JPEG。
    - 根据设备 User-Agent 自动调整图片参数。
- **智能分页**: 服务器端缓存游标，客户端只需传递页数即可翻页。
- **符合漫画源规范**: 完全符合自定义漫画源标准，可直接集成到漫画阅读应用。
- **易于部署**: 提供详细的手动部署指南和一键安装脚本。

---

## 🚀 部署指南

我们提供两种部署方式：**一键安装脚本 (推荐)** 和 **手动部署**。

### 方式一：一键安装脚本 (推荐)

此脚本适用于一个全新的、基于 Debian 的系统 (如 Ubuntu)。它将自动完成所有环境配置和部署。

**1. 下载脚本**:
```bash
wget https://raw.githubusercontent.com/OrPudding/vela-py-eh-api-server/main/install.sh
```

**2. 运行脚本**:
```bash
chmod +x install.sh
sudo ./install.sh
```

### 方式二：手动部署

#### a. 环境要求
- Linux 服务器
- Python 3.10+
- PM2 (Node.js 进程管理器)
- Nginx 或 OpenResty

#### b. 安装依赖
```bash
git clone https://github.com/OrPudding/vela-py-eh-api-server.git /opt/eh-api-service
cd /opt/eh-api-service
pip3 install --break-system-packages -r requirements.txt
npm install pm2 -g
```

#### c. 使用 PM2 启动服务
```bash
pm2 start ecosystem.config.js
pm2 save
```

#### d. 配置 Nginx 反向代理
```nginx
server {
    listen 443 ssl http2;
    server_name your-api-domain.com;

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

为了访问 ExHentai 或个性化内容，您需要提供 E-Hentai/ExHentai 的 Cookie。本项目支持通过 **HTTP 请求头** `Cookie` 来手动传入 Cookie。

**请求示例**:
```
GET https://your-api-domain.com/search?q=language:chinese
Headers: {
  "Cookie": "igneous=xxx; ipb_member_id=12345; ..."
}
```
> **注意**: 如果不提供此请求头，API 将以游客身份访问公开的 E-Hentai 内容。

### User-Agent 解析 (1.8版本)

所有 API 请求都会携带以下格式的 User-Agent：
```
packageName(versionName(versionCode))/product/brand/osType/osVersionName/osVersionCode/language/region
```

服务器会根据 User-Agent 自动调整图片参数：
- **手环/手表设备**: 宽度 300px，质量 40
- **手机设备**: 宽度 400px，质量 50
- **其他设备**: 默认宽度 400px，质量 50

---

### 1. 获取漫画源配置

**接口地址**: `/config`

**调用例子**: `/config`

**返回示例**:
```json
{
  "E-Hentai": {
    "name": "E-Hentai",
    "apiUrl": "https://your-api-domain.com",
    "searchPath": "/search?q=<text>&page=<page>",
    "photoPath": "/photo/<id>/<chapter>",
    "detailPath": "/comic/<id>",
    "type": "ehentai"
  }
}
```

---

### 2. 搜索漫画

**必选参数**:
`q`: 搜索关键词，例如 `language:chinese`。

**可选参数**:
`page`: 页数，从 1 开始。默认为 1。

**接口地址**: `/search`

**调用例子**:
- 搜索第一页: `/search?q=language:chinese`
- 搜索第二页: `/search?q=language:chinese&page=2`

**返回示例**:
```json
{
  "page": 1,
  "has_more": true,
  "results": [
    {
      "comic_id": "3645215_4db836130d",
      "title": "[Chinese] 画廊标题",
      "cover_url": "https://your-api-domain.com/image/proxy?url=...&w=150&q=40",
      "pages": 25
    }
  ]
}
```

> **说明**: `comic_id` 格式为 `gid_token`，用于后续获取详情和图片。

---

### 3. 获取漫画详情

**必选参数**:
`id`: 漫画 ID，格式为 `gid_token`。

**接口地址**: `/comic/<id>`

**调用例子**: `/comic/3645215_4db836130d`

**返回示例**:
```json
{
  "item_id": "3645215_4db836130d",
  "name": "[Chinese] 画廊标题",
  "page_count": 25,
  "rate": 4.5,
  "cover": "https://your-api-domain.com/image/proxy?url=...&w=150&q=40",
  "tags": ["chinese", "translated", "artist_name", "big breasts", "sole female", "sole male"]
}
```

---

### 4. 获取漫画图片列表

**必选参数**:
`id`: 漫画 ID，格式为 `gid_token`。
`chapter`: 章节（页码），从 0 开始。

**接口地址**: `/photo/<id>/<chapter>`

**调用例子**:
- 获取第一页图片: `/photo/3645215_4db836130d/0`
- 获取第二页图片: `/photo/3645215_4db836130d/1`

**返回示例**:
```json
{
  "title": "[Chinese] 画廊标题",
  "images": [
    {
      "url": "https://your-api-domain.com/image/proxy?url=...&width=400&quality=50"
    },
    {
      "url": "https://your-api-domain.com/image/proxy?url=...&width=400&quality=50"
    }
  ]
}
```

> **说明**: 图片 URL 中的 `width` 和 `quality` 参数会根据设备 User-Agent 自动调整。

---

### 5. 图片代理服务

**说明**: 此接口用于获取经过服务器处理（切割、压缩、转码）后的 JPEG 图片。

**必选参数**:
`url`: 原始图片 URL。

**可选参数**:
- `w` 或 `width`: 图片最大宽度。默认根据设备自动调整。
- `q` 或 `quality`: JPEG 压缩质量 (1-100)。默认根据设备自动调整。
- `crop_x`, `crop_y`, `crop_w`, `crop_h`: 用于切割雪碧图的参数。

**接口地址**: `/image/proxy`

**调用例子**:
- 代理大图: `/image/proxy?url=https://.../image.webp`
- 指定宽度和质量: `/image/proxy?url=https://.../image.webp&width=600&quality=60`

**返回内容**:
- **成功**: 返回 `Content-Type: image/jpeg` 的图片二进制数据。
- **失败**: 返回 `Content-Type: application/json` 的错误信息。

---

### 6. 健康检查

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

### 7. 测试页面

**接口地址**: `/test`

**说明**: 提供一个 Web 界面用于测试 API 功能。

---

## 🔄 缓存策略

| 缓存类型 | 缓存时间 | 最大条目数 |
|---------|---------|-----------|
| 列表缓存 | 5 分钟 | 100 |
| 画廊详情缓存 | 1 小时 | 500 |
| 图片代理缓存 | 24 小时 | 1000 |
| 分页游标缓存 | 10 分钟 | 200 |

---

## 📱 自定义漫画源集成

本 API 完全符合自定义漫画源规范，可直接集成到支持的漫画阅读应用中。

**集成步骤**:
1. 在应用中添加自定义漫画源
2. 输入 API 地址（如 `https://your-api-domain.com`）
3. 应用会自动获取 `/config` 配置
4. 开始浏览和阅读

**ID 格式说明**:
- 漫画 ID 格式为 `gid_token`（如 `3645215_4db836130d`）
- 这是为了兼容 E-Hentai 的 gid 和 token 机制

---

## ⚖️ 许可

本软件根据 **GNU Affero General Public License v3.0** 许可。详情请参阅 `LICENSE` 文件。
