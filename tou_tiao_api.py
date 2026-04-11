import base64
import json
import re
import time
import urllib

import requests
from bs4 import BeautifulSoup

from builder.header import HeaderBuilder
from builder.params import Params
from utils.tou_tiao_utils import get_headers, generate_sign, trans_time, timestamp_to_str

ILLEGAL_CHARACTERS_RE = re.compile(r'[\000-\010]|[\013-\014]|[\016-\037]')


class TouTiaoApi:
    base_url = "https://so.toutiao.com"

    def getSearchInfo(self, keyword, page_num, auth):
        res = None
        success = True
        msg = '成功'
        try:
            api = "/search"
            headers = HeaderBuilder.build_common_header().get()
            cookies = auth.cookie
            params = {
                "dvpf": "pc",
                "source": "pagination",
                "keyword": keyword,
                "pd": "synthesis",
                "action_type": "pagination",
                "page_num": str(page_num),
                "search_id": "202409031511249321F5AA49F1400A0BCC",
                "from": "search_tab",
                "cur_tab_title": "search_tab"
            }
            response = requests.get(self.base_url + api, headers=headers, cookies=cookies, params=params)
            res = response.text
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
        resp = requests.get(f'https://www.toutiao.com{api}', headers=headers, cookies=auth.cookie, params=params.get())
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
        response_stat = requests.post(url, headers=headers, cookies=auth.cookie, params=params.get(), data=data)
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
        response = requests.get(user_url, headers=page_headers, params=page_params, cookies=auth.cookie)
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
        response = requests.get(work_url, headers=headers, cookies=auth.cookie)
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find_all('script', type="application/ld+json")
        info = json.loads(script[0].string)

        images = []
        videos = []
        if 'article' in work_url:
            title = info['headline']
            content = soup.find('article').text
            images = info['image']
            for video in soup.find_all('div', attrs={'class': 'tt-video-box'}):
                video_id = video['tt-videoid']
                videos.append(self.get_video_url(video_id, auth))
        else:
            title = info['name']
            content = info['description']
        return {
            "title": ILLEGAL_CHARACTERS_RE.sub(r'', title),
            "content": ILLEGAL_CHARACTERS_RE.sub(r'', content),
            "images": images,
            "videos": videos,
        }

    def get_video_url(self, video_id, auth):
        url = f"https://i.snssdk.com/video/urls/1/toutiao/mp4/{video_id}"
        headers = get_headers(url)
        params = {
            "callback": "tt__video__9n4f3t"
        }
        response = requests.get(url, headers=headers, cookies=auth.cookie, params=params)
        res_text = response.text.replace("tt__video__9n4f3t(", "")[:-1]
        res_json = json.loads(res_text)
        video_url = res_json['data']['video_list']['video_1']['main_url']
        video_url = base64.b64decode(video_url.encode('utf-8')).decode('utf-8')
        return video_url
