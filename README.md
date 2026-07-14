<div align="center">
    <a href="https://www.python.org/">
        <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
    </a>
    <a href="https://nodejs.org/zh-cn/">
        <img src="https://img.shields.io/badge/nodejs-20%2B-green" alt="NodeJS 20+">
    </a>
    <a href="https://fastapi.tiangolo.com/">
        <img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688" alt="FastAPI">
    </a>
</div>

# 📰 Toutiao Platform

**✨ 专业的今日头条数据采集解决方案，支持搜索、用户主页、图文与视频内容全量抓取**

当你需要让 AI Agent 感知今日头条内容生态——自动采集用户作品、分析内容数据、驱动内容运营策略——第一道墙往往不是模型能力，而是**平台数据获取能力的缺失**。

本项目做的事很简单：把这道墙拆掉。

**⚠️ 严禁用于爬取用户隐私、违规商业用途！本项目仅供学习与技术研究使用，后果自负。**

## 🌟 功能特性

- ✅ **搜索采集**
  - 支持关键词搜索，返回综合搜索结果原始数据
- ✅ **用户信息采集**
  - 获取用户主页信息：昵称、头像、粉丝数、点赞数等
- ✅ **用户作品采集**
  - 支持单页获取与自动翻页全量获取
  - 同时覆盖视频、图文、微头条三种内容类型
- ✅ **作品详情采集**
  - 获取正文内容、图片列表、视频列表
- ✅ **视频直链解析**
  - 自动 Base64 解码，返回可直接访问的视频 MP4 地址
- 🔐 **a_bogus / msToken / _signature 自动计算**
  - 内嵌 Node.js 运行时，自动生成今日头条接口鉴权参数
- 🚀 **高性能服务**
  - 基于 FastAPI + Uvicorn 异步服务
  - 支持 Docker 一键部署

## 🛠️ 快速开始

### ⛳ 运行环境

- Python 3.10+
- Node.js 20+

### 🎯 本地安装

```bash
pip install -r requirements.txt
cd static && npm install
```

### 🚀 运行项目

```bash
python main.py
```

服务启动后访问 http://localhost:5000/docs 查看交互式 API 文档。

### 🎨 Cookie 配置

在浏览器中打开 [www.toutiao.com](https://www.toutiao.com)，**登录账号**后按 `F12` 打开开发者工具，点击「网络」→ 找任意一个 API 请求 → 复制请求头中的 `Cookie` 字段值。

> ⚠️ 注意：必须登录后获取的 Cookie 才有效，`msToken`、`ttwid` 等字段缺失将导致接口鉴权失败。

将获取到的 Cookie 字符串作为 `cookies_str` 参数传入接口，格式如下：

```
msToken=xxx; ttwid=xxx; tt_chain_token=xxx; ...
```

## 📡 接口说明

### POST `/get_search_info`

按关键词搜索今日头条内容，返回指定页的原始搜索结果。

**请求参数**

| 字段          | 类型  | 必填 | 说明                  |
|-------------|-----|----|---------------------|
| keyword     | str | 是  | 搜索关键词               |
| page_num    | int | 是  | 页码，从 0 开始           |
| cookies_str | str | 是  | 今日头条登录 Cookie 字符串   |

**请求示例**

```bash
curl -X POST http://localhost:5000/get_search_info \
  -H "Content-Type: application/json" \
  -d '{
    "keyword": "人工智能",
    "page_num": 0,
    "cookies_str": "msToken=xxx; ttwid=xxx"
  }'
```

**响应示例**

```json
{
  "code": 200,
  "message": "成功",
  "data": "<原始 HTML 字符串>"
}
```

---

### POST `/get_user_info`

获取今日头条用户主页信息。

**请求参数**

| 字段          | 类型  | 必填 | 说明                                          |
|-------------|-----|----|---------------------------------------------|
| user_url    | str | 是  | 用户主页 URL，如 `https://www.toutiao.com/c/user/token/MS4w.../` |
| cookies_str | str | 是  | 今日头条登录 Cookie 字符串                           |

**请求示例**

```bash
curl -X POST http://localhost:5000/get_user_info \
  -H "Content-Type: application/json" \
  -d '{
    "user_url": "https://www.toutiao.com/c/user/token/MS4wLjABAAAA.../",
    "cookies_str": "msToken=xxx; ttwid=xxx"
  }'
```

**响应示例**

```json
{
  "code": 200,
  "message": "成功",
  "data": {
    "nickname": "用户昵称",
    "avatar": "https://p3.toutiaoimg.com/...",
    "user_id": "123456789",
    "fans": 10000,
    "digg_count": 5000,
    "collect_time": "2026-04-11 12:00:00"
  }
}
```

---

### POST `/get_user_all_work`

获取用户发布的全部作品（自动翻页），返回原始数据列表。

**请求参数**

| 字段          | 类型  | 必填 | 说明                        |
|-------------|-----|----|---------------------------|
| user_url    | str | 是  | 用户主页 URL                  |
| cookies_str | str | 是  | 今日头条登录 Cookie 字符串         |

**请求示例**

```bash
curl -X POST http://localhost:5000/get_user_all_work \
  -H "Content-Type: application/json" \
  -d '{
    "user_url": "https://www.toutiao.com/c/user/token/MS4wLjABAAAA.../",
    "cookies_str": "msToken=xxx; ttwid=xxx"
  }'
```

**响应示例**

```json
{
  "code": 200,
  "message": "成功",
  "data": [
    {
      "id": "7300000000000000000",
      "title": "文章标题",
      "publish_time": 1731643083,
      "like_count": 100,
      "comment_count": 20
    }
  ]
}
```

---

### POST `/get_work_info`

获取单篇作品的详细内容（支持图文与视频）。

**请求参数**

| 字段          | 类型  | 必填 | 说明                                                      |
|-------------|-----|----|-----------------------------------------------------------|
| work_url    | str | 是  | 作品 URL，如 `https://www.toutiao.com/article/xxx/` 或 `https://www.toutiao.com/video/xxx/` |
| cookies_str | str | 是  | 今日头条登录 Cookie 字符串                                       |

**请求示例**

```bash
curl -X POST http://localhost:5000/get_work_info \
  -H "Content-Type: application/json" \
  -d '{
    "work_url": "https://www.toutiao.com/article/7300000000000000000/",
    "cookies_str": "msToken=xxx; ttwid=xxx"
  }'
```

**响应示例**

```json
{
  "code": 200,
  "message": "成功",
  "data": {
    "title": "文章标题",
    "content": "正文内容...",
    "images": ["https://p3.toutiaoimg.com/img1.jpg"],
    "videos": ["https://v3.toutiaoimg.com/video1.mp4"]
  }
}
```

---

### POST `/get_video_url`

解析视频 ID，返回可直接访问的 MP4 直链地址。

**请求参数**

| 字段          | 类型  | 必填 | 说明                    |
|-------------|-----|----|------------------------|
| video_id    | str | 是  | 视频 URI，从作品数据中的 `play_addr.uri` 字段获取 |
| cookies_str | str | 是  | 今日头条登录 Cookie 字符串     |

**请求示例**

```bash
curl -X POST http://localhost:5000/get_video_url \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "v0d00fg10000cu9gobbc77u0s7d8i2b0",
    "cookies_str": "msToken=xxx; ttwid=xxx"
  }'
```

**响应示例**

```json
{
  "code": 200,
  "message": "成功",
  "data": "https://v3.toutiaoimg.com/obj/tos-cn-ve-15/xxx.mp4"
}
```

## 🐳 Docker 部署

```bash
docker build -t toutiao-platform .
docker run -d -p 5000:5000 toutiao-platform
```

## 🍥 日志

| 日期       | 说明                                          |
|----------|---------------------------------------------|
| 26/04/11 | 项目初始化，完成搜索、用户信息、用户作品、内容详情、视频直链 API 封装 |

## 🤝 欢迎贡献 PR

本项目欢迎任何形式的贡献！如果你有新功能想法、Bug 修复或文档改进，欢迎提交 PR。

- Fork 本仓库并在新分支上开发
- 保持代码风格与现有代码一致
- PR 描述中请简要说明改动内容和目的
- 也欢迎通过 Issue 提出建议或报告问题

## 🧸 额外说明
1. 感谢 star⭐ 和 follow📰！不时更新
2. 作者的联系方式在主页里，有问题可以随时联系我
3. 可以关注下作者的其他项目，欢迎 PR 和 issue
4. 感谢赞助！如果此项目对您有帮助，请作者喝一杯奶茶~~ （开心一整天😊😊）
5. thank you~~~

## 🍔 交流群

如果你对爬虫和 AI Agent 感兴趣，请加作者主页 wx 通过邀请加入群聊

ps: 请加群22、23、24，人满或者过期 issue | wx 提醒

| group22 | group23 | group24 |
|:--:|:--:|:--:|
| <img width="280" alt="group22" src="https://github.com/user-attachments/assets/d2821fad-1be7-4712-8399-1a9f4710483a" /> | <img width="280" alt="group23" src="https://github.com/user-attachments/assets/ea27f64b-2f0c-46eb-9728-10691fef9756" /> | <img width="280" alt="group24" src="https://github.com/user-attachments/assets/4d153b10-598a-4f3b-9508-2c51832b89f0" /> |
