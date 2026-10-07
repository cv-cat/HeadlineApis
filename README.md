# HeadlineApis

今日头条读取 API 与头条官方开放平台视频 Creator API。Python 3.10+，Node.js 20+。`TouTiaoApi` 旧入口保留。

## 能力状态

| 能力 | 入口 | 状态 |
| --- | --- | --- |
| Cookie 会话 | `TouTiaoAuth.from_cookie`、`prepare_auth` | 本地解析与契约测试通过；Cookie 是否有效需用户账号验证 |
| 头条 OAuth 授权与续期 | `TouTiaoOAuth.authorize_url`、`exchange_code`、`refresh_access_token` | 官方端点有文档，mock 请求测试通过；真实授权未验证 |
| 搜索 | `TouTiaoApi.search`、旧 `getSearchInfo` | 匿名首页实测返回 6 个头条作品链接；当前只支持第 0 页，后续页明确报错，避免把重复首页当成翻页结果 |
| Item 详情 | `TouTiaoApi.item`、旧 `get_work_info` | 接受 `https://www.toutiao.com/article/{id}/`、`video/{id}/`、`item/{id}/`、`group/{id}/`；解析契约测试通过，线上详情页面待验证 |
| 用户页、作品列表、视频直链 | `TouTiaoApi` 原有方法 | 保留兼容入口；本轮没有账号级线上测试 |
| 视频上传 | `TouTiaoCreatorApi.upload_video` | 官方端点和表单字段有文档，mock 请求测试通过；真实上传未验证 |
| 视频发布 | `TouTiaoCreatorApi.publish_video` | 官方端点已确认；官方页面目前未给完整请求体字段，`video_id`/`text` 约定待真实授权账号验证 |
| 已发布视频列表 | `TouTiaoCreatorApi.list_videos` | 官方端点已确认；分页参数参考公开 SDK，真实账号待验证 |
| 特定视频数据 | `TouTiaoCreatorApi.get_video_data` | 官方端点已确认；请求体由调用方提供，字段待验证 |
| 图文、微头条发布 | `publish_article` 明确抛出 `NotImplementedError` | 官方头条发布接入方案明确目前只支持小视频；创作者网页私有接口待单独验证 |

**上传不会自动发布。** 只有调用 `publish_video` 才会提交作品；成功提交后仍有平台审核过程。Creator API 需要已审核应用及用户授权的 `toutiao.video.create` 或 `toutiao.video.data` 权限。Cookie 不能代替开放平台的 access token。

## 安装与只读示例

```bash
pip install -r requirements.txt
cd static && npm install
cd ..
python main.py --help
```

`main.py` 是只读 CLI，并非 HTTP 服务。搜索和详情用环境变量传 Cookie，避免写进命令历史；公开页面在无需 Cookie 的条件下也可以尝试，但平台可能限制。

```powershell
$env:TOUTIAO_COOKIE = '从本人浏览器获取的 Cookie'
python main.py search '人工智能' --page 0
python main.py item 'https://www.toutiao.com/article/1234567890/'
```

Python 调用：

```python
from builder.auth import TouTiaoAuth
from tou_tiao_api import TouTiaoApi

with TouTiaoAuth.from_cookie(cookie_str) as auth:
    api = TouTiaoApi()
    results = api.search("人工智能", 0, auth)  # {raw_html, items, search_id}
    article = api.item("https://www.toutiao.com/article/1234567890/", auth)
    legacy = api.getSearchInfo("人工智能", 0, auth)  # (success, msg, HTML)
```

## OAuth 与视频 Creator

开发者先在头条开放平台申请权限，把授权 URL 展示给本人确认，再从回调取得 `code`。`state` 应由应用生成随机值，并在回调验证。`client_secret` 只应保存在服务端。

```python
from builder.auth import TouTiaoOAuth
from tou_tiao_creator_api import TouTiaoCreatorApi

url = TouTiaoOAuth.authorize_url(
    client_key, redirect_uri, ["toutiao.video.create", "toutiao.video.data"], state
)
# 用户打开 url 授权；回调后校验 state，再将 code 交给服务端：
auth = TouTiaoOAuth.exchange_code(client_key, client_secret, code)
# 在服务端安全保存 auth.refresh_token、auth.expires_at、auth.refresh_expires_at 和 auth.scope。
# access_token 到期或平台返回 token 失效时，可刷新原会话：
TouTiaoOAuth.refresh_access_token(client_key, auth)
creator = TouTiaoCreatorApi(auth)
uploaded = creator.upload_video("clip.mp4")
video_id = uploaded["video"]["video_id"]
# 用户确认发布后再单独调用；请求体仍需真实授权账号验证：
published = creator.publish_video(video_id, "视频标题")
videos = creator.list_videos(cursor=0, count=10)
auth.close()
```

头条刷新只针对 `access_token`：官方资料指出 `refresh_token` 不能续期，过期后须重新取得用户授权。刷新接口当前文档的参数表为空，仓库依照[官方 SDK 示例](https://open.douyin.com/platform/resource/docs/develop/guide/douyin-live-sdk/android)采用 `client_key`、`grant_type=refresh_token`、`refresh_token` 字段，以该接口文档指定的 multipart 表单发送；字段来自同平台示例，真实头条授权账号仍待验证。搜索页码大于 0 时 `search()` 抛出 `NotImplementedError`，旧 `getSearchInfo()` 返回 `(False, 错误说明, None)`。

单文件上传限制为 128 MiB；更大的视频需要官方分片接口，本仓库暂未封装。`get_video_data(payload)` 会原样发送调用方给出的请求体；不要把未知字段当作已验证的 API 契约。

## 端点依据

| 端点 | 证据 |
| --- | --- |
| `GET https://so.toutiao.com/search`、`GET https://www.toutiao.com/article/{id}/` | 本仓库原有 `tou_tiao_api.py`，本轮保持兼容并加强输入与错误处理 |
| `GET https://open.snssdk.com/oauth/authorize/`、`POST https://open.snssdk.com/oauth/access_token/` | [头条获取授权码](https://open.douyin.com/platform/resource/docs/openapi/account-permission/toutiao-get-permission-code)、[获取 access token](https://open.douyin.com/platform/resource/docs/openapi/account-permission/get-access-token) |
| `POST https://open.snssdk.com/oauth/refresh_token/` | [官方刷新 access token 文档](https://open.douyin.com/platform/resource/docs/openapi/account-permission/refresh-access-token)、[头条帐号 OAuth 说明](https://open.douyin.com/platform/resource/docs/develop/permission/toutiao-or-xigua/OAuth2.0/) |
| `POST https://open.douyin.com/toutiao/video/upload/` | [官方上传视频文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/create-video/upload-video) |
| `POST https://open.douyin.com/toutiao/video/create/` | [官方发布视频文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/create-video/publish-video)；当前页面请求字段显示“暂无数据” |
| `GET https://open.douyin.com/toutiao/video/list/` | [官方视频列表文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/search-video/account-video-list/)，分页字段参照[公开 SDK](https://github.com/leafisme/douyin_open/blob/master/docs/Api/ToutiaoVideoListApi.md) |
| `POST https://open.douyin.com/toutiao/video/data/` | [官方特定视频数据文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/search-video/video-data/)；请求字段显示“暂无数据” |
| 图文发布范围 | [官方头条内容发布接入方案](https://open.douyin.com/platform/resource/docs/ability/content-management/toutiao-publish-solution/)说明开放接口暂不支持头条文章、微头条 |

创作者网页的 `/mp/agw/article/publish` 见[公开项目的协议记录](https://github.com/xc-2000/toutiao-auto-publisher/blob/main/README.md)，但本仓库没有当前账号的脱敏请求样本、安全参数和发布结果，因此没有封装或宣称可用。

## 验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q builder tou_tiao_api.py tou_tiao_creator_api.py main.py
```

测试使用假会话，不发送真实作品。匿名搜索首页已做只读实测；真实 OAuth、详情、上传与发布需要对应账号在受控环境逐项实测。搜索分页在线返回重复作品，当前已禁止后续页请求。`.env` 已从版本追踪移除并忽略；使用 `.env.example` 查看变量名，不要提交 Cookie、Token 或 `client_secret`。
