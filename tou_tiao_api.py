import base64
import json
import re
import time
import urllib
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from builder.header import HeaderBuilder
from builder.params import Params
from utils.tou_tiao_utils import get_headers, generate_sign, trans_time, timestamp_to_str

ILLEGAL_CHARACTERS_RE = re.compile(r'[\000-\010]|[\013-\014]|[\016-\037]')


class TouTiaoApi:
    base_url = "https://so.toutiao.com"

    def __init__(self, session=None):
        # 注入 requests 兼容对象，便于做不触网的请求契约测试。
        self.http = session or requests

    def search(self, keyword, page_num, auth, *, search_id=None):
        """搜索首页作品，返回原始 HTML 与尽力提取的作品链接。

        搜索页可能改变 HTML 结构；翻页请求在线返回了重复首页，暂不开放。
        """
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("keyword is required")
        if not isinstance(page_num, int) or page_num < 0:
            raise ValueError("page_num must be a non-negative integer")
        if page_num:
            raise NotImplementedError("search pagination is unverified; only page_num=0 is supported")
        if search_id is not None:
            raise ValueError("search_id is only for pagination, which is unverified")
        params = {"keyword": keyword, "pd": "synthesis", "page_num": page_num}
        response = self.http.get(
            f"{self.base_url}/search",
            params=params,
            headers=HeaderBuilder.build_common_header().get(),
            cookies=auth.cookie,
            timeout=30,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items = []
        seen = set()
        for link in soup.select("a[href]"):
            href = link.get("href", "")
            parsed = urlparse(urljoin(self.base_url, href))
            if parsed.hostname == "so.toutiao.com" and parsed.path == "/search/jump":
                target = parse_qs(parsed.query).get("url", [""])[0]
                nested = parse_qs(urlparse(target).query).get("h5_url", [""])[0]
                parsed = urlparse(unquote(nested or target))
            if parsed.hostname not in {"toutiao.com", "www.toutiao.com"}:
                continue
            if not re.fullmatch(r"/(?:article|video|item|group)/\d+/?", parsed.path):
                continue
            url = f"https://www.toutiao.com{parsed.path}"
            if url not in seen:
                seen.add(url)
                items.append({"url": url, "title": link.get_text(" ", strip=True)})
        found_id = re.search(r'search_id\s*:\s*"([A-Z0-9]{20,})"', response.text)
        return {
            "raw_html": response.text,
            "items": items,
            "search_id": found_id.group(1) if found_id else None,
        }

    def item(self, work_url, auth):
        """按作品 URL 读取图文或视频详情。"""
        parsed = urlparse(work_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "toutiao.com", "www.toutiao.com"
        }:
            raise ValueError("work_url must be an HTTPS toutiao.com URL")
        if not re.fullmatch(r"/(?:article|video|item|group)/\d+/?", parsed.path):
            raise ValueError("work_url must point to an article, video, item or group")
        return self.get_work_info(work_url, auth)

    def getSearchInfo(self, keyword, page_num, auth):
        res = None
        success = True
        msg = '成功'
        try:
            res = self.search(keyword, page_num, auth)["raw_html"]
        except Exception as e:
            success = False
            msg = str(e)
        return success, msg, res

    def get_user_work_info(self, user_url, max_behot_time, auth):
        api = "/api/pc/list/user/feed"
        headers = get_headers(user_url)
        token = user_url.split("/")[-2]
        params = Params()
        params.add_param("category", 'profile_all')
        params.add_param("token", token)
        params.add_param("max_behot_time", max_behot_time)
        params.add_param("entrance_gid", "")
        params.add_param("aid", '24')
        params.add_param("app_name", 'toutiao_web')
        params.with_ms_token()
        params.with_a_bogus()
        resp = self.http.get(f'https://www.toutiao.com{api}', headers=headers, cookies=auth.cookie, params=params.get(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_user_all_work(self, user_url, auth):
        max_behot_time = ''
        res = []
        while True:
            res_json = self.get_user_work_info(user_url, max_behot_time, auth)
            res.extend(res_json['data'])
            if not res_json['has_more']:
                break
            max_behot_time = res_json['next']['max_behot_time']
        return res

    def user_info(self, user_url, auth):
        api = "/api/pc/user/fans_stat"
        url = f'https://www.toutiao.com{api}'
        headers = get_headers(user_url)
        token = user_url.split("/")[-2]
        params = Params()
        params.add_param("_signature", generate_sign(url))
        data = {
            "token": token
        }
        params.with_a_bogus(data)
        response_stat = self.http.post(url, headers=headers, cookies=auth.cookie, params=params.get(), data=data, timeout=30)
        response_stat.raise_for_status()
        response_stat_json = response_stat.json()
        page_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": "https://www.toutiao.com/",
            "sec-ch-ua": "\"Not(A:Brand\";v=\"99\", \"Microsoft Edge\";v=\"133\", \"Chromium\";v=\"133\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0"
        }
        page_params = {
            "log_from": f"b24b98c824b3f_{time.time() * 1000}"
        }
        response = self.http.get(user_url, headers=page_headers, params=page_params, cookies=auth.cookie, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        script_text = soup.find_all('script', attrs={"id": "RENDER_DATA"})[0].string
        script_text = urllib.parse.unquote(script_text)
        script_json = json.loads(script_text)
        user_id = script_json['data']['profileUserInfo']['userId']
        user_profile = soup.find('div', attrs={"class": "profile-info-l"})
        avatar = user_profile.find('a', attrs={"class": "avatar"}).find('img')['src']
        detail = user_profile.find('div', attrs={"class": "detail"})
        nickname = detail.find('span', attrs={"class": "name"}).text
        user_digg_count = response_stat_json['data']['digg_count']
        user_fans = response_stat_json['data']['fans']
        return {
            'nickname': nickname,
            'avatar': avatar,
            'user_id': user_id,
            'fans': user_fans,
            'digg_count': user_digg_count,
            'collect_time': timestamp_to_str(int(time.time() * 1000)),
        }

    def get_work_info(self, work_url, auth):
        headers = get_headers(work_url)
        response = self.http.get(work_url, headers=headers, cookies=auth.cookie, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find_all('script', type="application/ld+json")
        if not script or not script[0].string:
            raise ValueError("work page has no JSON-LD item data")
        info = json.loads(script[0].string)
        if isinstance(info, list):
            info = info[0]

        images = []
        videos = []
        if '/article/' in work_url or '/item/' in work_url or '/group/' in work_url:
            title = info.get('headline') or info.get('name') or ''
            article = soup.find('article')
            content = article.get_text("\n", strip=True) if article else info.get('description', '')
            image = info.get('image', [])
            images = image if isinstance(image, list) else ([image] if image else [])
            for video in soup.find_all('div', attrs={'class': 'tt-video-box'}):
                video_id = video.get('tt-videoid')
                if video_id:
                    videos.append(self.get_video_url(video_id, auth))
        else:
            title = info.get('name') or info.get('headline') or ''
            content = info.get('description', '')
            video_url = info.get('contentUrl')
            if video_url:
                videos.append(video_url)
        return {
            "title": ILLEGAL_CHARACTERS_RE.sub(r'', str(title or '')),
            "content": ILLEGAL_CHARACTERS_RE.sub(r'', str(content or '')),
            "images": images,
            "videos": videos,
        }

    def get_video_url(self, video_id, auth):
        url = f"https://i.snssdk.com/video/urls/1/toutiao/mp4/{video_id}"
        headers = get_headers(url)
        params = {
            "callback": "tt__video__9n4f3t"
        }
        response = self.http.get(url, headers=headers, cookies=auth.cookie, params=params, timeout=30)
        response.raise_for_status()
        res_text = response.text.replace("tt__video__9n4f3t(", "")[:-1]
        res_json = json.loads(res_text)
        video_url = res_json['data']['video_list']['video_1']['main_url']
        video_url = base64.b64decode(video_url.encode('utf-8')).decode('utf-8')
        return video_url
