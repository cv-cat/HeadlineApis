import subprocess
import time
from functools import partial
subprocess.Popen = partial(subprocess.Popen, encoding="utf-8")
import execjs
import sys
import random
import urllib
from os import path


def trans_cookies(cookies_str):
    cookies = dict()
    for i in cookies_str.split("; "):
        try:
            cookies[i.split('=')[0]] = '='.join(i.split('=')[1:])
        except:
            continue
    return cookies


if getattr(sys, 'frozen', None):
    basedir = sys._MEIPASS
else:
    basedir = path.dirname(__file__)

try:
    node_modules = path.join(basedir, 'static', 'node_modules')
    dy_path = path.join(basedir, 'static', 'dy.js')
    dy_js = execjs.compile(open(dy_path, 'r', encoding='utf-8').read(), cwd=node_modules)
    sign_path = path.join(basedir, 'static', 'sign.js')
    sign_js = execjs.compile(open(sign_path, 'r', encoding='utf-8').read(), cwd=node_modules)
except:
    node_modules = path.join(basedir, '..', 'static', 'node_modules')
    dy_path = path.join(basedir, '..', 'static', 'dy.js')
    dy_js = execjs.compile(open(dy_path, 'r', encoding='utf-8').read(), cwd=node_modules)
    sign_path = path.join(basedir, '..', 'static', 'sign.js')
    sign_js = execjs.compile(open(sign_path, 'r', encoding='utf-8').read(), cwd=node_modules)


def generate_a_bogus(query, data=""):
    a_bogus = dy_js.call('get_ab', query, data)
    return a_bogus


def generate_sign(url):
    sign = sign_js.call('genserate_sign', url)
    return sign


def generate_msToken(randomlength=107):
    random_str = ''
    base_str = 'ABCDEFGHIGKLMNOPQRSTUVWXYZabcdefghigklmnopqrstuvwxyz0123456789='
    length = len(base_str) - 1
    for _ in range(randomlength):
        random_str += base_str[random.randint(0, length)]
    return random_str


def splice_url(params):
    splice_url_str = ''
    for key, value in params.items():
        if value is None:
            value = ''
        splice_url_str += key + '=' + urllib.parse.quote(str(value)) + '&'
    return splice_url_str[:-1]


def get_headers(referer="https://www.toutiao.com/"):
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": referer,
        "sec-ch-ua": "\"Microsoft Edge\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0"
    }


def trans_time(timestamp):
    timeArray = time.localtime(timestamp)
    otherStyleTime = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
    return otherStyleTime


def timestamp_to_str(timestamp):
    time_local = time.localtime(timestamp / 1000)
    dt = time.strftime("%Y-%m-%d %H:%M:%S", time_local)
    return dt


def str_to_timestamp(str):
    time_array = time.strptime(str, "%Y-%m-%d %H:%M:%S")
    timestamp = int(time.mktime(time_array))
    return timestamp


def is_n_days_ago(date, n):
    now = time.time()
    date = str_to_timestamp(date)
    if now - date > n * 24 * 60 * 60:
        return False
    return True
