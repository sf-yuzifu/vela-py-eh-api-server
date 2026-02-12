# Copyright (C) 2025 OrPudding
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import re
import io
import sys
import requests
import logging
from flask import Flask, request, jsonify, Response, render_template_string
from urllib.parse import urlencode, quote, unquote
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup
from PIL import Image
from cachetools import cached, TTLCache
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==============================================================================
# 缓存配置
# ==============================================================================
list_cache = TTLCache(maxsize=100, ttl=300 )
gallery_cache = TTLCache(maxsize=500, ttl=3600)
image_proxy_cache = TTLCache(maxsize=1000, ttl=86400)
pagination_cache = TTLCache(maxsize=200, ttl=600)

import sys
sys.stdout.reconfigure(encoding="utf-8")

def decode_search_value(value: str) -> str:
    """
    判断并解码搜索值
    如果值是URL编码，则解码为中文，否则直接返回
    """
    # URL编码的特征：包含%后跟两个十六进制字符
    url_encoded_pattern = r"%[0-9A-Fa-f]{2}"

    # 如果包含URL编码特征，尝试解码
    if re.search(url_encoded_pattern, value):
        try:
            decoded = unquote(value)
            # 解码后如果还包含URL编码特征，说明可能有多重编码，继续解码
            while re.search(url_encoded_pattern, decoded):
                temp = unquote(decoded)
                if temp == decoded:  # 如果没有变化，停止解码
                    break
                decoded = temp
            return decoded
        except Exception:
            # 如果解码失败，返回原值
            return value
    else:
        # 没有URL编码特征，直接返回
        return value

# ==============================================================================
# 模块 1: E-Hentai HTML 解析器 (EhParser)
# ==============================================================================
class EhParser:
    PATTERN_GALLERY_URL = re.compile(r'/g/(\d+)/([a-f0-9]+)/?')
    PATTERN_RATING = re.compile(r'background-position:\s*(-?\d+)px')
    PATTERN_PAGES = re.compile(r'(\d+)\s+pages?', re.IGNORECASE)
    PATTERN_NEXT_ID = re.compile(r'[?&]next=(\d+)')
    PATTERN_STYLE_DETAILS = re.compile(r'width:(\d+)px;height:(\d+)px;.*background:.*?url\(([^)]+)\) (-?\d+)px (-?\d+)')

    @staticmethod
    def parse_gallery_list(html: str) -> Dict:
        soup = BeautifulSoup(html, 'html.parser')
        galleries = []
        main_table = soup.find('table', class_='itg gltc')
        # 如果找不到主表格，记录日志并返回空结果
        if not main_table:
            logging.warning("未能解析到画廊列表 (找不到 'itg gltc' 表格)。页面原始内容如下：")
            logging.debug(html)
            return {'galleries': [], 'pagination': {}}
        
        rows = main_table.find_all('tr')
        for row in rows:
            try:
                name_cell = row.find('td', class_='glname')
                if not name_cell: continue
                gallery = {}
                link_tag = name_cell.find('a')
                if not link_tag or 'href' not in link_tag.attrs: continue
                url = link_tag['href']
                match = EhParser.PATTERN_GALLERY_URL.search(url)
                if not match: continue
                gallery['gid'] = int(match.group(1)); gallery['token'] = match.group(2); gallery['url'] = url
                title_div = link_tag.find('div', class_='glink')
                gallery['title'] = title_div.text.strip() if title_div else 'N/A'
                thumb_cell = row.find('td', class_='gl2c')
                if thumb_cell:
                    img_tag = thumb_cell.find('img')
                    if img_tag: gallery['thumbnail'] = img_tag.get('data-src') or img_tag.get('src')
                    posted_div = thumb_cell.find('div', id=lambda x: x and x.startswith('posted_'))
                    if posted_div: gallery['posted'] = posted_div.text.strip()
                category_cell = row.find('td', class_='glcat')
                if category_cell: gallery['category'] = category_cell.text.strip()
                rating_elem = name_cell.find('div', class_='ir')
                if rating_elem:
                    style = rating_elem.get('style', ''); rating_match = EhParser.PATTERN_RATING.search(style)
                    if rating_match: gallery['rating'] = round(5 - (abs(int(rating_match.group(1))) / 16.0), 2)
                uploader_cell = row.find('td', class_='glhide')
                if uploader_cell:
                    uploader_elem = uploader_cell.find('a')
                    if uploader_elem: gallery['uploader'] = uploader_elem.get_text(strip=True)
                    pages_text_node = uploader_cell.find(string=re.compile(r'\d+\s+pages?'))
                    if pages_text_node:
                        pages_match = EhParser.PATTERN_PAGES.search(pages_text_node)
                        if pages_match: gallery['pages'] = int(pages_match.group(1))
                galleries.append(gallery)
            except Exception as e: logging.error(f"解析画廊项时发生错误: {e}"); continue
        
        # 如果循环后列表仍为空，可能页面有内容但所有行都解析失败
        if not galleries and len(rows) > 1: # len(rows) > 1 是为了排除只有表头的情况
            logging.warning("画廊列表解析结果为空，可能所有行都解析失败。页面原始内容如下：")
            logging.debug(html)

        pagination = {'has_next': False, 'next_id': None}
        try:
            pager = soup.find('div', class_='searchnav') or soup.find('table', class_='ptt')
            if pager:
                next_link = pager.find('a', id='unext') or pager.find('a', text='>')
                if next_link and next_link.has_attr('href'):
                    pagination['has_next'] = True
                    href = next_link['href']
                    next_id_match = EhParser.PATTERN_NEXT_ID.search(href)
                    if next_id_match: pagination['next_id'] = next_id_match.group(1)
        except Exception as e: logging.error(f"解析分页信息时出错: {e}")
        return {'galleries': galleries, 'pagination': pagination}

    @staticmethod
    def parse_gallery_detail(html: str) -> Dict:
        soup = BeautifulSoup(html, 'html.parser'); detail = {}
        try:
            # 检查核心元素是否存在
            if not soup.select_one('#gn') and not soup.select_one('#gj'):
                logging.warning("未能解析到画廊详情 (找不到标题元素 #gn 或 #gj)。页面原始内容如下：")
                logging.debug(html)
                return {}

            title_elem = soup.select_one('#gn');
            if title_elem: detail['title'] = title_elem.get_text(strip=True)
            title_jp_elem = soup.select_one('#gj');
            if title_jp_elem: detail['title_jp'] = title_jp_elem.get_text(strip=True)
            category_elem = soup.select_one('#gdc a');
            if category_elem: detail['category'] = category_elem.get_text(strip=True)
            thumb_elem = soup.select_one('#gd1 div')
            if thumb_elem:
                style = thumb_elem.get('style', ''); url_match = re.search(r'url\((.+?)\)', style)
                if url_match: detail['thumbnail'] = url_match.group(1)
            tags = {}
            tag_list = soup.select('#taglist tr')
            for tag_row in tag_list:
                tag_type = tag_row.select_one('td.tc')
                if tag_type:
                    tag_name = tag_type.get_text(strip=True).rstrip(':'); tag_values = [tag_elem.get_text(strip=True) for tag_elem in tag_row.select('td div a')]
                    if tag_values: tags[tag_name] = tag_values
            if tags: detail['tags'] = tags
            rating_elem = soup.select_one('#rating_label')
            if rating_elem:
                rating_text = rating_elem.get_text(strip=True); rating_match = re.search(r'([\d.]+)', rating_text)
                if rating_match: detail['rating'] = float(rating_match.group(1))
            
            gdd_rows = soup.select('#gdd tr')
            for row in gdd_rows:
                label_elem = row.select_one('td.gdt1')
                value_elem = row.select_one('td.gdt2')
                if label_elem and value_elem:
                    label_text = label_elem.get_text(strip=True)
                    if 'Length:' in label_text or 'length:' in label_text.lower():
                        pages_text = value_elem.get_text(strip=True)
                        pages_match = re.search(r'(\d+)', pages_text)
                        if pages_match:
                            detail['pages'] = int(pages_match.group(1))
                            break
        except Exception as e: logging.error(f"解析画廊详情时出错: {e}")
        
        # 如果最终字典为空，记录日志
        if not detail:
            logging.warning("画廊详情解析结果为空。页面原始内容如下：")
            logging.debug(html)

        return detail

    @staticmethod
    def parse_preview_images(html: str) -> List[Dict]:
        previews = []
        soup = BeautifulSoup(html, 'html.parser')
        container = soup.find('div', id='gdt')
        if not container:
            logging.warning("未能解析到预览图列表 (找不到容器 #gdt)。页面原始内容如下：")
            logging.debug(html)
            return previews
        
        image_links = container.find_all('a')
        for index, a_tag in enumerate(image_links):
            try:
                div_tag = a_tag.find('div')
                if not div_tag or 'style' not in div_tag.attrs: continue
                style = div_tag['style']
                details_match = EhParser.PATTERN_STYLE_DETAILS.search(style)
                if details_match:
                    width = int(details_match.group(1)); height = int(details_match.group(2))
                    thumbnail_url = details_match.group(3); x_offset = abs(int(details_match.group(4))); y_offset = abs(int(details_match.group(5)))
                    previews.append({'index': index, 'page_url': a_tag['href'], 'thumbnail_url': thumbnail_url, 'crop_x': x_offset, 'crop_y': y_offset, 'crop_w': width, 'crop_h': height})
            except Exception as e: logging.error(f"解析单个预览图时出错: {e}"); continue
        
        if not previews and image_links:
            logging.warning("预览图列表解析结果为空，但找到了 a 标签。页面原始内容如下：")
            logging.debug(html)

        return previews

    @staticmethod
    def parse_image_page(html: str) -> Optional[str]:
        soup = BeautifulSoup(html, 'html.parser')
        img_container = soup.find('div', id='i3')
        if not img_container:
            logging.warning("未能解析到大图页面 (找不到容器 #i3)。页面原始内容如下：")
            logging.debug(html)
            return None
        img_tag = img_container.find('img')
        if not img_tag or 'src' not in img_tag.attrs:
            logging.warning("未能解析到大图 URL (在 #i3 中找不到带 src 的 img 标签)。页面原始内容如下：")
            logging.debug(html)
            return None
        return img_tag['src']


# ==============================================================================
# 模块 2: E-Hentai URL 构建器 (EhUrlBuilder)
# ==============================================================================
class EhUrlBuilder:
    SITE_E = 'e-hentai.org'; SITE_EX = 'exhentai.org'
    def __init__(self, use_exhentai: bool = False): self.domain = self.SITE_EX if use_exhentai else self.SITE_E; self.base_url = f'https://{self.domain}'
    def build_home_url(self, next_id: Optional[str] = None ) -> str: return f'{self.base_url}/' if not next_id else f'{self.base_url}/?next={next_id}'
    def build_search_url(self, keyword: Optional[str] = None, next_id: Optional[str] = None, **kwargs) -> str:
        params = {}
        if not next_id:
            if keyword: params['f_search'] = keyword.strip()
        else:
            params = {'f_search': keyword.strip(), 'next': next_id} if keyword else {'next': next_id}
        query_string = urlencode(params)
        return f'{self.base_url}/?{query_string}'
    def build_tag_url(self, tag: str, page: int = 0) -> str: encoded_tag = quote(tag); return f'{self.base_url}/tag/{encoded_tag}' if page == 0 else f'{self.base_url}/tag/{encoded_tag}/{page}'
    def build_gallery_url(self, gid: int, token: str) -> str: return f'{self.base_url}/g/{gid}/{token}/'
    def build_popular_url(self) -> str: return f'{self.base_url}/popular'
    def get_referer(self) -> str: return self.base_url

# ==============================================================================
# 模块 3: 图片处理器 (ImageProcessor)
# ==============================================================================
class ImageProcessor:
    @staticmethod
    def process_and_compress(image_bytes: bytes, max_width: int, quality: int, crop_params: Optional[Dict] = None) -> Optional[Tuple[bytes, dict]]:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            if crop_params:
                x, y, w, h = crop_params['x'], crop_params['y'], crop_params['w'], crop_params['h']
                box = (x, y, x + w, y + h)
                img = img.crop(box)
            original_width, original_height = img.size
            if original_width > max_width:
                ratio = max_width / original_width; new_height = int(original_height * ratio)
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            if img.mode in ("RGBA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA": background.paste(img, mask=img.split()[-1])
                else: background.paste(img)
                img = background
            elif img.mode != "RGB": img = img.convert("RGB")
            output = io.BytesIO()
            optimize_options = {"quality": quality, "optimize": True, "progressive": True}
            img.save(output, "JPEG", **optimize_options)
            compressed_bytes = output.getvalue()
            info = {"original_size": f"{original_width}x{original_height}", "compressed_size": f"{img.width}x{img.height}", "file_size": len(compressed_bytes)}
            return compressed_bytes, info
        except Exception as e: logging.error(f"图片处理失败: {e}"); return None

# ==============================================================================
# 核心业务逻辑 (独立的、可缓存的函数)
# ==============================================================================
@cached(cache=list_cache)
def get_gallery_list_data(url: str, headers: tuple):
    logging.info(f"缓存未命中或已过期，正在抓取列表页: {url}")
    html = fetch_page_for_request(url, dict(headers))
    if not html:
        # fetch_page_for_request 内部已经记录了错误，这里无需重复记录
        return None
    
    parsed_data = EhParser.parse_gallery_list(html)
    # EhParser 内部已经增加了日志，这里返回即可
    return parsed_data

@cached(cache=gallery_cache)
def get_gallery_detail_data(gid: int, token: str, headers: tuple, url_builder: 'EhUrlBuilder'):
    url = url_builder.build_gallery_url(gid=gid, token=token)
    logging.info(f"缓存未命中或已过期，正在抓取详情页: {url}")
    html = fetch_page_for_request(url, dict(headers))
    if not html:
        return None
    
    parsed_data = EhParser.parse_gallery_detail(html)
    # 如果解析结果为空字典，说明解析失败
    if not parsed_data:
        logging.warning(f"画廊详情页 {url} 解析结果为空。")
        # EhParser 内部已记录 HTML，这里只记录上下文
        return None
        
    return parsed_data

@cached(cache=gallery_cache)
def get_gallery_images_data(gid: int, token: str, page: int, headers: tuple, url_builder: 'EhUrlBuilder'):
    url = f"{url_builder.build_gallery_url(gid=gid, token=token)}?p={page}"
    logging.info(f"缓存未命中或已过期，正在抓取图片列表并并发解析所有大图: {url}")
    preview_html = fetch_page_for_request(url, dict(headers))
    if not preview_html:
        return None
        
    preview_list = EhParser.parse_preview_images(preview_html)
    if not preview_list:
        logging.warning(f"画廊图片预览页 {url} 解析结果为空列表。")
        # EhParser 内部已记录 HTML，这里返回空列表是符合预期的
        return []

    final_images = [None] * len(preview_list)
    def fetch_and_parse_image_url(preview_item):
        page_html = fetch_page_for_request(preview_item['page_url'], dict(headers))
        if page_html:
            image_url = EhParser.parse_image_page(page_html)
            if image_url:
                return preview_item['index'], image_url
        logging.warning(f"无法从 {preview_item['page_url']} 获取最终图片链接。")
        return preview_item['index'], None

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        future_to_url = {executor.submit(fetch_and_parse_image_url, item): item for item in preview_list}
        for future in as_completed(future_to_url):
            original_item = future_to_url[future]
            try:
                index, image_url = future.result()
                if image_url:
                    crop_info = f"&crop_x={original_item['crop_x']}&crop_y={original_item['crop_y']}&crop_w={original_item['crop_w']}&crop_h={original_item['crop_h']}"
                    thumbnail_proxy_url = f"/image/proxy?url={original_item['thumbnail_url']}{crop_info}&w={THUMBNAIL_PROXY_WIDTH}&q={THUMBNAIL_PROXY_QUALITY}"
                    final_images[index] = {'index': index, 'thumbnail_jpg': thumbnail_proxy_url, 'image_jpg': f"/image/proxy?url={image_url}"}
            except Exception as exc: logging.error(f"并发任务生成异常: {exc}")
    return [img for img in final_images if img is not None]


@cached(cache=image_proxy_cache)
def get_processed_image_data(url: str, headers: tuple, max_width: int, quality: int, crop_params: Optional[tuple] = None):
    logging.info(f"图片缓存未命中或已过期，正在处理图片: {url}")
    try:
        response = requests.get(url, headers=dict(headers), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        crop_dict = None
        if crop_params: crop_dict = {'x': crop_params[0], 'y': crop_params[1], 'w': crop_params[2], 'h': crop_params[3]}
        result = ImageProcessor.process_and_compress(image_bytes=response.content, max_width=max_width, quality=quality, crop_params=crop_dict)
        return result
    except requests.RequestException as e: logging.error(f"下载原始图片失败: {e}"); return None
    except Exception as e: logging.error(f"处理图片时发生未知错误: {e}"); return None

# ==============================================================================
# Flask 应用配置与路由
# ==============================================================================
app = Flask(__name__)
app.json.ensure_ascii = False
app.debug = False
REQUEST_TIMEOUT = 20; DEFAULT_PROXY_WIDTH = 400; DEFAULT_PROXY_QUALITY = 50
THUMBNAIL_PROXY_WIDTH = 150; THUMBNAIL_PROXY_QUALITY = 40
MAX_CONCURRENT_REQUESTS = 10

def parse_user_agent(user_agent: str) -> dict:
    device_info = {'product': '', 'brand': '', 'os_type': '', 'os_version': ''}
    try:
        if not user_agent:
            return device_info
        
        parts = user_agent.split('/')
        if len(parts) >= 5:
            device_info['product'] = parts[1] if len(parts) > 1 else ''
            device_info['brand'] = parts[2] if len(parts) > 2 else ''
            device_info['os_type'] = parts[3] if len(parts) > 3 else ''
            device_info['os_version'] = parts[4] if len(parts) > 4 else ''
    except Exception as e:
        logging.warning(f"解析 User-Agent 失败: {e}")
    
    return device_info

def parse_id(id_str: str) -> tuple:
    try:
        if '_' in id_str:
            parts = id_str.split('_')
            if len(parts) == 2:
                return int(parts[0]), parts[1]
        return None, None
    except Exception as e:
        logging.warning(f"解析 ID 失败: {e}")
        return None, None

def get_image_params_for_device(device_info: dict) -> tuple:
    default_width = DEFAULT_PROXY_WIDTH
    default_quality = DEFAULT_PROXY_QUALITY
    
    product = device_info.get('product', '').lower()
    brand = device_info.get('brand', '').lower()
    
    if 'band' in product or 'watch' in product or brand in ['xiaomi', 'huawei', 'oppo', 'vivo']:
        default_width = 300
        default_quality = 40
    elif 'phone' in product or 'mobile' in product:
        default_width = 400
        default_quality = 50
    
    return default_width, default_quality

def get_request_context() -> tuple:
    client_cookie = request.headers.get('Cookie')
    use_exhentai = bool(client_cookie and 'igneous=EX' in client_cookie)
    url_builder = EhUrlBuilder(use_exhentai=use_exhentai)
    
    user_agent = request.headers.get('User-Agent', '')
    device_info = parse_user_agent(user_agent)
    default_width, default_quality = get_image_params_for_device(device_info)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7', 'Accept-Language': 'en-US,en;q=0.9', 'Referer': url_builder.get_referer()}
    if client_cookie: headers['Cookie'] = client_cookie
    return headers, url_builder, default_width, default_quality

def fetch_page_for_request(url: str, headers: dict) -> Optional[str]:
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logging.error(f"请求 E-Hentai 失败: {e}, URL: {url}")
        return None

@app.route('/test')
def test_page():
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>E-Hentai API 测试器</title>
        <style>
            body{font-family:sans-serif;line-height:1.6;margin:20px;max-width:1200px;margin:0 auto;padding:20px}
            h1,h2,h3{border-bottom:1px solid #ccc;padding-bottom:5px}
            form{margin-bottom:20px;padding:15px;border:1px solid #eee;border-radius:5px;background:#f9f9f9}
            input,button,textarea{padding:8px;margin-right:10px;border:1px solid #ddd;border-radius:4px}
            button{background:#007bff;color:white;border:none;cursor:pointer}
            button:hover{background:#0056b3}
            button:disabled{background:#ccc;cursor:not-allowed}
            pre{background-color:#f4f4f4;padding:15px;border-radius:5px;white-space:pre-wrap;word-wrap:break-word;max-height:600px;overflow-y:auto}
            .container{max-width:1200px;margin:auto}
            .form-group{margin-bottom:10px}
            label{display:inline-block;margin-right:10px}
            .pagination{margin-top:10px;display:flex;gap:10px;align-items:center}
            .page-info{padding:5px 10px;background:#e9ecef;border-radius:4px}
            .config-output{background:#e7f3ff;padding:10px;border-radius:5px;margin-bottom:20px;font-family:monospace}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🧪 E-Hentai API 测试器</h1>
            
            <form id="cookieForm">
                <h3>1. 设置 Cookie (可选)</h3>
                <textarea id="cookieInput" rows="2" style="width:100%" placeholder="例如：igneous=xxx; ipb_member_id=12345; ..."></textarea>
            </form>
            
            <form id="configForm">
                <h3>2. 获取漫画源配置</h3>
                <button type="submit">获取配置</button>
            </form>
            
            <form id="searchForm">
                <h3>3. 搜索漫画</h3>
                <div class="form-group">
                    <label>关键词: <input type="text" id="searchKeyword" value="language:chinese" style="width:300px"></label>
                    <label>页数: <input type="number" id="searchPage" value="1" min="1" style="width:80px"></label>
                    <button type="submit">搜索</button>
                </div>
            </form>
            
            <div class="pagination" id="searchPagination" style="display:none;">
                <button id="prevPageBtn">上一页</button>
                <span class="page-info" id="pageInfo">第 1 页</span>
                <button id="nextPageBtn">下一页</button>
            </div>
            
            <hr>
            
            <form id="detailForm">
                <h3>4. 获取漫画详情和图片</h3>
                <div class="form-group">
                    <label>ID (gid_token): <input type="text" id="comicId" size="40" placeholder="例如: 3645215_4db836130d"></label>
                </div>
                <div class="form-group">
                    <button type="button" id="getDetailBtn">获取详情</button>
                    <button type="button" id="getImagesBtn">获取图片</button>
                    <label>章节: <input type="number" id="chapterNum" value="0" min="0" style="width:80px"></label>
                </div>
            </form>
            
            <h3>📋 结果输出:</h3>
            <pre id="output">这里将显示 API 的 JSON 响应...</pre>
        </div>
        
        <script>
            const cookieInput = document.getElementById("cookieInput");
            const output = document.getElementById("output");
            const searchPage = document.getElementById("searchPage");
            const searchPagination = document.getElementById("searchPagination");
            const pageInfo = document.getElementById("pageInfo");
            const prevPageBtn = document.getElementById("prevPageBtn");
            const nextPageBtn = document.getElementById("nextPageBtn");
            const comicIdInput = document.getElementById("comicId");
            const chapterNum = document.getElementById("chapterNum");
            
            let currentKeyword = "";
            let currentHasMore = false;
            
            cookieInput.value = localStorage.getItem("ehCookie") || "";
            cookieInput.addEventListener("input", () => {
                localStorage.setItem("ehCookie", cookieInput.value);
            });
            
            async function callApi(url, updatePagination = false) {
                const cookie = cookieInput.value;
                output.textContent = "⏳ 正在请求...";
                
                try {
                    const headers = {};
                    if (cookie) headers["Cookie"] = cookie;
                    
                    const response = await fetch(url, { headers });
                    const data = await response.json();
                    
                    output.textContent = JSON.stringify(data, null, 2);
                    
                    if (updatePagination && data.results) {
                        currentKeyword = document.getElementById("searchKeyword").value;
                        currentHasMore = data.has_more;
                        const page = data.page;
                        
                        searchPage.value = page;
                        pageInfo.textContent = `第 ${page} 页`;
                        prevPageBtn.disabled = page <= 1;
                        nextPageBtn.disabled = !currentHasMore;
                        searchPagination.style.display = "flex";
                    }
                    
                    if (data.comic_id) {
                        comicIdInput.value = data.comic_id;
                    }
                    
                    if (data.item_id) {
                        comicIdInput.value = data.item_id;
                    }
                } catch (e) {
                    output.textContent = "❌ 请求失败: " + e.message;
                }
            }
            
            document.getElementById("configForm").addEventListener("submit", (e) => {
                e.preventDefault();
                callApi("/config");
            });
            
            document.getElementById("searchForm").addEventListener("submit", (e) => {
                e.preventDefault();
                const keyword = document.getElementById("searchKeyword").value;
                const page = searchPage.value;
                callApi(`/search?q=${encodeURIComponent(keyword)}&page=${page}`, true);
            });
            
            prevPageBtn.addEventListener("click", () => {
                const page = parseInt(searchPage.value);
                if (page > 1) {
                    searchPage.value = page - 1;
                    callApi(`/search?q=${encodeURIComponent(currentKeyword)}&page=${page - 1}`, true);
                }
            });
            
            nextPageBtn.addEventListener("click", () => {
                const page = parseInt(searchPage.value);
                if (currentHasMore) {
                    searchPage.value = page + 1;
                    callApi(`/search?q=${encodeURIComponent(currentKeyword)}&page=${page + 1}`, true);
                }
            });
            
            document.getElementById("getDetailBtn").addEventListener("click", () => {
                const id = comicIdInput.value.trim();
                if (!id) {
                    alert("请输入漫画 ID (格式: gid_token)");
                    return;
                }
                callApi(`/comic/${id}`);
            });
            
            document.getElementById("getImagesBtn").addEventListener("click", () => {
                const id = comicIdInput.value.trim();
                const chapter = chapterNum.value;
                if (!id) {
                    alert("请输入漫画 ID (格式: gid_token)");
                    return;
                }
                callApi(`/photo/${id}/${chapter}`);
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)

@app.route('/')
def home():
    try:
        headers, url_builder, default_width, default_quality = get_request_context()
        next_id = request.args.get('next')
        url = url_builder.build_home_url(next_id=next_id)
        result = get_gallery_list_data(url, tuple(headers.items()))
        if not result: return jsonify({'error': '无法获取页面内容'}), 500
        for gallery in result.get('galleries', []):
            if 'thumbnail' in gallery and gallery['thumbnail']: gallery['thumbnail_proxy'] = f"/image/proxy?url={gallery['thumbnail']}&w={THUMBNAIL_PROXY_WIDTH}&q={THUMBNAIL_PROXY_QUALITY}"
        return jsonify({'success': True, **result})
    except Exception as e: logging.error(f"路由 / 出错: {e}"); return jsonify({'error': f'服务器错误: {str(e)}'}), 500

@app.route('/search')
def search():
    try:
        headers, url_builder, default_width, default_quality = get_request_context()
        search_keyword = request.args.get('q', '')
        page = int(request.args.get('page', 1) or 1)

        keyword = decode_search_value(search_keyword)
        print(f"原始值: {search_keyword}, 解码后: {keyword}")
        
        if not keyword: return jsonify({'error': '缺少搜索关键词参数 q'}), 400
        
        cache_key = f"search_{keyword}"
        next_id = None
        
        if page > 1:
            next_id = pagination_cache.get(f"{cache_key}_{page - 1}")
            if not next_id:
                for p in range(1, page):
                    prev_next_id = pagination_cache.get(f"{cache_key}_{p - 1}") if p > 1 else None
                    temp_url = url_builder.build_search_url(keyword=keyword, next_id=prev_next_id)
                    temp_result = get_gallery_list_data(temp_url, tuple(headers.items()))
                    if not temp_result:
                        return jsonify({'error': f'无法获取第 {p} 页数据'}), 500
                    
                    temp_next_id = temp_result.get('pagination', {}).get('next_id')
                    if temp_next_id:
                        pagination_cache[f"{cache_key}_{p}"] = temp_next_id
                    else:
                        return jsonify({'error': f'第 {p} 页后没有更多数据'}), 404
                
                next_id = pagination_cache.get(f"{cache_key}_{page - 1}")
        
        url = url_builder.build_search_url(keyword=keyword, next_id=next_id)
        result = get_gallery_list_data(url, tuple(headers.items()))
        if not result: return jsonify({'error': '无法获取搜索结果'}), 500
        
        current_next_id = result.get('pagination', {}).get('next_id')
        if current_next_id:
            pagination_cache[f"{cache_key}_{page}"] = current_next_id
        
        results = []
        api_url = request.host_url.rstrip("/")
        for gallery in result.get('galleries', []):
            if 'thumbnail' in gallery and gallery['thumbnail']: 
                gallery['thumbnail_proxy'] = f"{api_url}/image/proxy?url={gallery['thumbnail']}&w={THUMBNAIL_PROXY_WIDTH}&q={THUMBNAIL_PROXY_QUALITY}"
            
            results.append({
                'comic_id': f"{gallery.get('gid')}_{gallery.get('token')}",
                'title': gallery.get('title'),
                'cover_url': gallery.get('thumbnail_proxy') or gallery.get('thumbnail'),
                'pages': gallery.get('pages')
            })
        
        has_more = result.get('pagination', {}).get('has_next', False)
        
        return jsonify({
            'page': page,
            'has_more': has_more,
            'results': results
        })
    except Exception as e: logging.error(f"路由 /search 出错: {e}"); return jsonify({'error': f'服务器错误: {str(e)}'}), 500

@app.route('/comic/<id>')
def gallery_detail(id: str):
    try:
        gid, token = parse_id(id)
        if not gid or not token: return jsonify({'error': '无效的 ID 格式，应为 gid_token'}), 400

        api_url = request.host_url.rstrip("/")
        
        headers, url_builder, default_width, default_quality = get_request_context()
        detail = get_gallery_detail_data(gid, token, tuple(headers.items()), url_builder)
        if not detail: return jsonify({'error': '无法获取画廊详情'}), 500
        detail.update({'gid': gid, 'token': token})
        if 'thumbnail' in detail and detail['thumbnail']: detail['thumbnail_proxy'] = f"{api_url}/image/proxy?url={detail['thumbnail']}&w={THUMBNAIL_PROXY_WIDTH}&q={THUMBNAIL_PROXY_QUALITY}"

        print(detail)
        
        result = {
            'item_id': f"{detail.get('gid')}_{detail.get('token')}",
            'name': detail.get('title'),
            'page_count': detail.get('pages'),
            'rate': detail.get('rating'),
            'cover': detail.get('thumbnail_proxy') or detail.get('thumbnail'),
            'tags': []
        }
        
        if 'tags' in detail and isinstance(detail['tags'], dict):
            for tag_type, tag_list in detail['tags'].items():
                if isinstance(tag_list, list):
                    result['tags'].extend(tag_list)
        
        return jsonify(result)
    except Exception as e: logging.error(f"路由 /comic 出错: {e}"); return jsonify({'error': f'服务器错误: {str(e)}'}), 500

@app.route('/photo/<id>')
@app.route('/photo/<id>/')
@app.route('/photo/<id>/<chapter>')
@app.route('/photo/<id>/<chapter>/')
def gallery_images(id: str, chapter: str):
    try:
        gid, token = parse_id(id)
        if not gid or not token: return jsonify({'error': '无效的 ID 格式，应为 gid_token'}), 400
        
        headers, url_builder, default_width, default_quality = get_request_context()
        page = int(chapter) if chapter.isdigit() else 0
        processed_images = get_gallery_images_data(gid, token, page, tuple(headers.items()), url_builder)
        if processed_images is None: return jsonify({'error': '无法获取画廊图片列表'}), 500
        
        detail = get_gallery_detail_data(gid, token, tuple(headers.items()), url_builder)
        title = detail.get('title', '') if detail else ''

        api_url = request.host_url.rstrip("/")
        
        images = []
        for img in processed_images:
            image_url = img.get('image_jpg', '')
            if image_url:
                images.append({'url': f"{api_url}{image_url}"})
        
        result = {
            'title': title,
            'images': images
        }
        
        return jsonify(result)
    except Exception as e: logging.error(f"路由 /photo 出错: {e}"); return jsonify({'error': f'服务器内部错误: {str(e)}'}), 500

@app.route('/image/proxy')
def image_proxy():
    image_url = request.args.get('url')
    if not image_url: return jsonify({'error': '缺少图片 URL 参数'}), 400
    try:
        headers, _, _, _ = get_request_context()
        max_width = int(request.args.get('w', request.args.get('width', '400')))
        quality = int(request.args.get('q', request.args.get('quality', '50')))
        quality = max(1, min(100, quality))
        crop_params = None
        if 'crop_x' in request.args:
            try:
                crop_params = (int(request.args['crop_x']), int(request.args['crop_y']), int(request.args['crop_w']), int(request.args['crop_h']))
            except (ValueError, KeyError): return jsonify({'error': '无效的切割参数'}), 400
        
        result = get_processed_image_data(image_url, tuple(headers.items()), max_width, quality, crop_params)
        if not result: return jsonify({'error': '无法下载或处理图片'}), 500
        
        compressed_bytes, info = result
        response_headers = {"Content-Disposition": "inline", "Content-Length": str(info['file_size']), "X-Image-Original-Size": info['original_size'], "X-Image-Compressed-Size": info['compressed_size'], "Cache-Control": "public, max-age=86400"}
        return Response(compressed_bytes, mimetype="image/jpeg", headers=response_headers)
    except Exception as e: logging.error(f"路由 /image/proxy 出错: {e}"); return jsonify({'error': f'服务器内部错误: {str(e)}'}), 500

@app.route('/health')
def health(): return jsonify({'status': 'ok', 'client_cookie_provided': bool(request.headers.get('Cookie'))})
        
@app.get("/config")
@app.get("/config/")
def config():
    api_url = request.host_url.rstrip("/")
    return jsonify(
        {
            "E-Hentai": {
                "name": "E-Hentai",
                "apiUrl": api_url,
                "searchPath": "/search?q=<text>&page=<page>",
                "photoPath": "/photo/<id>/<chapter>",
                "detailPath": "/comic/<id>",
                "type": "ehentai",
            },
        }
    )

@app.errorhandler(404)
def not_found(error): return jsonify({'error': '未找到请求的资源'}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)