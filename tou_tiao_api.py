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
WORK_PATH_RE = re.compile(r"/(?:article|video|item|group)/\d+/?")


class TouTiaoApi:
    base_url = "https://so.toutiao.com"

    def __init__(self, session=None):
        # 使用持久 requests.Session 保存 ttwid/tt_webid；测试可注入兼容对象。
        self.http = session or requests.Session()
        self._ttwid_ready = False

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
        return self.get_work_info(work_url, auth)

    @staticmethod
    def _work_url(url):
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in {"toutiao.com", "www.toutiao.com"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("work_url must be an HTTPS toutiao.com URL")
        if not WORK_PATH_RE.fullmatch(parsed.path):
            raise ValueError("work_url must point to an article, video, item or group")
        return f"https://www.toutiao.com{parsed.path}"

    def _request_work_page(self, url, auth):
        """只跟随同站作品重定向，避免 Cookie 被带到站外。"""
        for _ in range(6):
            response = self.http.get(
                url, headers=get_headers(url), cookies=auth.cookie,
                timeout=30, allow_redirects=False,
            )
            status = getattr(response, "status_code", 200)
            if 300 <= status < 400:
                location = getattr(response, "headers", {}).get("Location")
                if not location:
                    raise ValueError("work page redirect has no Location")
                url = self._work_url(urljoin(url, location))
                continue
            response.raise_for_status()
            return response.text, url
        raise ValueError("work page redirected too many times")

    def _ensure_ttwid(self, auth):
        """用头条公开 ttwid 注册接口准备纯 HTTP 作品页会话。

        作品页未带有效 ``ttwid`` 时会返回 JSVM 空壳。网页首屏脚本实际
        先 POST ``ttwid.bytedance.com/ttwid/union/register/``，再 GET
        回调地址；这里复现相同的两步请求，不执行脚本或启动浏览器。
        """
        if self._ttwid_ready or not isinstance(self.http, requests.Session):
            return
        if any(str(name).lower() == "ttwid" for name in auth.cookie):
            self._ttwid_ready = True
            return
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0 Safari/537.36"
            ),
            "Referer": "https://www.toutiao.com/",
            "Origin": "https://www.toutiao.com",
            "Accept": "application/json, text/plain, */*",
        }
        response = self.http.post(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={"aid": 24, "service": "www.toutiao.com", "region": "cn",
                  "union": True, "needFid": False},
            headers={**headers, "Content-Type": "application/json"},
            cookies=auth.cookie,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        redirect_url = payload.get("redirect_url") if isinstance(payload, dict) else None
        if not isinstance(redirect_url, str):
            raise ValueError("ttwid registration returned no redirect URL")
        redirect = urlparse(redirect_url)
        if redirect.scheme != "https" or redirect.hostname != "www.toutiao.com" \
                or not redirect.path.startswith("/ttwid/union/register/callback/"):
            raise ValueError("ttwid registration redirect left the Toutiao callback")
        callback = self.http.get(
            redirect_url, headers=headers, cookies=auth.cookie,
            timeout=30, allow_redirects=False,
        )
        callback.raise_for_status()
        callback_payload = callback.json()
        if not isinstance(callback_payload, dict) or str(callback_payload.get("status_code")) != "0":
            raise ValueError("ttwid registration callback was not accepted")
        self._ttwid_ready = True

    @staticmethod
    def _video_streams(soup):
        """读取页面公开的分离音视频流；这些地址通常有有效期。"""
        streams = {"video": [], "audio": []}
        node = soup.find("script", id="RENDER_DATA")
        if not node or not node.string:
            return streams
        try:
            state = json.loads(unquote(node.string))
            play = state["data"]["initialVideo"]["videoPlayInfo"]
            dynamic = play["dynamic_video"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return streams
        if not isinstance(dynamic, dict):
            return streams
        for kind in ("video", "audio"):
            entries = dynamic.get(f"dynamic_{kind}_list", [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                url = entry.get("main_url")
                if not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).hostname:
                    continue
                meta = entry.get(f"{kind}_meta") or {}
                stream = {"url": url}
                if isinstance(meta, dict):
                    for key in ("definition", "vwidth", "vheight", "codec_type"):
                        if key in meta:
                            stream[key] = meta[key]
                streams[kind].append(stream)
        return streams

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
        work_url = self._work_url(work_url)
        parsed = urlparse(work_url)
        if parsed.path.startswith(("/group/", "/item/")):
            # 搜索结果目前是 /group/{id}/，该 URL 只返回空壳；/article/{id}/
            # 会重定向到实际的图文或视频页。
            work_url = f"https://www.toutiao.com/article/{parsed.path.strip('/').split('/')[1]}/"
        self._ensure_ttwid(auth)
        html, final_url = self._request_work_page(work_url, auth)
        soup = BeautifulSoup(html, 'html.parser')
        script = soup.find_all('script', type="application/ld+json")
        if not script:
            raise ValueError(
                "work page did not expose JSON-LD over HTTP; "
                "ttwid registration or the public page contract changed"
            )
        info = None
        for node in script:
            if not node.string:
                continue
            try:
                payload = json.loads(node.string)
            except json.JSONDecodeError:
                continue
            candidates = payload if isinstance(payload, list) else [payload]
            for candidate in candidates:
                if isinstance(candidate, dict) and isinstance(candidate.get("@graph"), list):
                    candidates.extend(candidate["@graph"])
                elif isinstance(candidate, dict) and (
                    candidate.get('headline') or candidate.get('name')
                ):
                    info = candidate
                    break
            if info:
                break
        if not info:
            raise ValueError("work page has no JSON-LD item data")

        images = []
        videos = []
        item_type = info.get('@type')
        is_video = item_type == "VideoObject" or urlparse(final_url).path.startswith('/video/')
        if not is_video:
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
            thumbnail = info.get('thumbnailUrl') or info.get('image') or []
            images = thumbnail if isinstance(thumbnail, list) else ([thumbnail] if thumbnail else [])
            video_url = info.get('contentUrl')
            if video_url:
                videos.append(video_url)
        result = {
            "url": final_url,
            "type": "video" if is_video else "article",
            "title": ILLEGAL_CHARACTERS_RE.sub(r'', str(title or '')),
            "content": ILLEGAL_CHARACTERS_RE.sub(r'', str(content or '')),
            "images": images,
            "videos": videos,
        }
        if is_video:
            result["media_streams"] = self._video_streams(soup)
        return result

    def get_video_url(self, video_id, auth):
        url = f"https://i.snssdk.com/video/urls/1/toutiao/mp4/{video_id}"
        headers = get_headers(url)
        params = {
            "callback": "tt__video__9n4f3t"
        }
        response = self.http.get(url, headers=headers, cookies=auth.cookie, params=params, timeout=30)
        response.raise_for_status()
        prefix = "tt__video__9n4f3t("
        body = response.text.strip().removesuffix(";")
        if not body.startswith(prefix) or not body.endswith(")"):
            raise ValueError("legacy video endpoint returned invalid JSONP")
        res_json = json.loads(body[len(prefix):-1])
        video_list = res_json.get('data', {}).get('video_list') or {}
        video = next(
            (video_list[key] for key in ('video_1', 'video_2', 'video_3')
             if isinstance(video_list.get(key), dict) and video_list[key].get('main_url')),
            None,
        )
        if video is None:
            raise ValueError("legacy video endpoint has no single-file URL")
        video_url = video['main_url']
        video_url = base64.b64decode(video_url.encode('utf-8')).decode('utf-8')
        return video_url
