# HeadlineApis

今日头条读取 API 与头条官方开放平台视频 Creator API。Python 3.10+，Node.js 20+。`TouTiaoApi` 旧入口保留。

## 能力状态

| 能力 | 入口 | 状态 |
| --- | --- | --- |
| Cookie 会话 | `TouTiaoAuth.from_cookie`、`prepare_auth` | 复用已有网页 Cookie 进行读取，本地解析与契约测试通过；实际 Cookie 有效性需本人账号验证 |
| 可见浏览器登录 | `TouTiaoAuth.from_browser_login`；旧名 `from_qrcode_login` 保留 | 独立 Chromium 打开头条创作者登录页，在官方页面选择二维码或手机验证码；登录后只读检查创作者首页、新 Cookie 与登录状态接口。本地假浏览器契约测试通过，真实辅助流程待验证 |
| 头条号网页状态与草稿 | `TouTiaoCreatorWebApi.is_logged_in`、`has_account_auth`、`list_drafts`、`delete_draft` | 本人已登录页面观察到实际 GET，以及指定草稿删除 POST 的成功响应；Python Cookie 客户端通过契约测试，真实账号在 Python 中待验证。`delete_draft` 会永久删除指定草稿 |
| 头条 OAuth 授权与续期 | `TouTiaoOAuth.authorize_url`、`exchange_code`、`refresh_access_token` | 官方端点有文档，mock 请求测试通过；真实授权未验证 |
| 搜索 | `TouTiaoApi.search`、旧 `getSearchInfo` | 匿名首页实测返回 6 个头条作品链接；当前只支持第 0 页，后续页明确报错，避免把重复首页当成翻页结果 |
| Item 详情 | `TouTiaoApi.item`、旧 `get_work_info` | 接受 `https://www.toutiao.com/article/{id}/`、`video/{id}/`、`item/{id}/`、`group/{id}/`；解析契约测试通过，线上详情页面待验证 |
| 用户页、作品列表、视频直链 | `TouTiaoApi` 原有方法 | 保留兼容入口；本轮没有账号级线上测试 |
| 视频上传 | `TouTiaoCreatorApi.upload_video` | 官方端点和表单字段有文档，mock 请求测试通过；真实上传未验证 |
| 视频发布 | `TouTiaoCreatorApi.publish_video` | 官方端点已确认；官方页面目前未给完整请求体字段，`video_id`/`text` 约定待真实授权账号验证 |
| 已发布视频列表 | `TouTiaoCreatorApi.list_videos` | 官方端点已确认；分页参数参考公开 SDK，真实账号待验证 |
| 特定视频数据 | `TouTiaoCreatorApi.get_video_data` | 官方端点已确认；请求体由调用方提供，字段待验证 |
| 图文、微头条发布 | `publish_article` 明确抛出 `NotImplementedError` | 官方头条发布接入方案明确目前只支持小视频；创作者网页私有接口待单独验证 |

**上传不会自动发布。** 只有调用 `publish_video` 才会提交作品；成功提交后仍有平台审核过程。上传/发布需要已审核应用与账号授权的 `toutiao.video.create`，视频列表/数据需要 `toutiao.video.data`。网页 Cookie 不能代替开放平台的 access token；OAuth token 也不用于网页搜索。网页登录得到的 `auth.creator_cookie` 仅表示创作者网页会话，`auth.access_token` 仍为空。若同时需要读取网页和 Creator Open API，可在 `TouTiaoAuth.from_access_token(..., cookie_str=...)` 中分别提供两类凭据。

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

### 创作者网页登录：二维码或手机验证码

此入口使用 Playwright 启动**新的可见 Chromium 与独立临时上下文**。在弹出的官方登录页选择今日头条 App 扫码，或在该页输入本人手机号与短信验证码；若官方页面出现滑块等人机验证，按页面提示自行完成。默认等待 600 秒，可通过 `timeout_seconds` 调整。完成登录后等待网页进入 `https://mp.toutiao.com/profile_v4/`；若平台没有自动跳转，可在该窗口手动打开这个地址。程序随后再次只读访问该地址，确认未跳回登录页，检查新 Cookie，并调用页面已观察到的只读 `user_login_status_api`，要求 `data.is_login=true`。手机号和验证码只输入官方页面，仓库代码不接收这些值。该入口不连接当前 Chrome、不读取已有浏览器配置、不保存 Cookie 到磁盘，也不模拟滑块或猜测登录 HTTP 接口。隔离上下文与按 URL 读取 Cookie 的行为见 [Playwright BrowserContext 文档](https://playwright.dev/python/docs/api/class-browsercontext)。

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

```python
from builder.auth import TouTiaoAuth

with TouTiaoAuth.from_browser_login(timeout_seconds=600) as auth:
    assert auth.creator_login_verified
    # auth.creator_cookie / creator_cookie_str 只在当前进程内，适用于 mp.toutiao.com。
    # auth.cookie 只含可用于 toutiao.com 子域的共享 Cookie，可能为空。
    # 不要把 Cookie 打印到控制台或写入仓库。
    print("创作者网页会话已完成只读核验")
```

旧的 `TouTiaoAuth.from_qrcode_login()` 是兼容别名，打开同一官方页面，仍可选择手机验证码。这项核验依据创作者首页的最终 URL、登录前后的 Cookie 变化和只读登录状态接口。浏览器辅助流程尚未在本人账号上端到端实测，不能证明私有发布接口可用，也不生成 `open_id` 或 Creator Open API 的 OAuth token。浏览器关闭后，会话对象仍在当前 Python 进程内；进程结束即消失。`main.py` 保持只读环境变量入口，不会把登录结果输出成 Cookie 字符串。

### 头条号网页状态与草稿

已登录头条号页面实际发起 `user_login_status_api`、`check_user_auth`、`draft_list` 三个 GET。`draft_list` 的 `type=0&count=20` 在本人网页会话中返回 `code=0` 与数组；同源只读请求验证省略页面请求中的 `app_id` 后仍成功。下面的 Python 客户端复用 `auth.creator_cookie`，只访问 `mp.toutiao.com`，不使用 Open API 的 `access_token`。返回的草稿可能包含本人作品内容，不要打印、提交或共享原始响应。

```python
from builder.auth import TouTiaoAuth
from tou_tiao_creator_web_api import TouTiaoCreatorWebApi

with TouTiaoAuth.from_browser_login() as auth:
    web = TouTiaoCreatorWebApi(auth)
    assert web.is_logged_in()
    drafts = web.list_drafts(count=20)  # 只在内存使用，不输出作品内容
# 明确选定自己的草稿后，才调用 web.delete_draft(draft["gid"], draft_type=draft["draft_type"])
```

`has_account_auth()` 只在接口 `code=0` 时读取布尔标志，其他业务码会抛出 `CreatorWebApiError`。本次账号曾出现“请完善账号信息”提示，随后页面明确显示“账号信息已完善，已为你解锁发布文章、视频等权益功能”；但后来独立 GET `/mp/agw/media/check_user_auth` 仍返回 HTTP 200、`code=100002`、`has_auth=false`。这个非零业务码的语义尚未确认，不能以其中的 `has_auth` 推断当前发文权限。编辑器可打开，公开发布未验收。上述网页 Cookie 也不能替代 Creator Open API OAuth 授权。

在本人网页编辑器输入中性临时内容时，页面自动请求 `POST /mp/agw/article/publish`，表单含 `save=0`，响应 `code=0` 和 `data.pgc_id`；随后草稿列表出现对应 `gid`，二者相同。删除该临时草稿时，网页请求 `POST /mp/agw/creator_center/delete_draft?app_id=1231`，JSON body 为 `{"drafts":[{"draft_type":2,"gid":"所选草稿 ID"}]}`，返回 `code=0`；再次读取草稿列表，临时草稿已消失。`delete_draft(gid, draft_type=...)` 仅封装这一指定草稿删除请求，参数应来自同一条草稿记录，调用后不可恢复。自动保存请求带页面生成的签名参数及多项编辑器状态，尚未建立可靠的独立请求契约；仓库不封装网页草稿写入或图文公开发布。

## OAuth 与视频 Creator

开发者先在头条开放平台申请权限，并配置与应用登记一致的 HTTP(S) 回调地址。`.env.example` 仅列字段名，代码**不会自动加载 `.env`**；本地通过环境变量或服务端密钥管理注入 `TOUTIAO_CLIENT_KEY`、`TOUTIAO_CLIENT_SECRET`、`TOUTIAO_REDIRECT_URI`。`client_secret` 只保存在服务端；不要把回调中的一次性 `code`、Token 或 Cookie 写进仓库、终端日志或聊天。

```python
import os
import secrets

from builder.auth import TouTiaoOAuth
from tou_tiao_creator_api import TouTiaoCreatorApi

client_key = os.environ["TOUTIAO_CLIENT_KEY"]
redirect_uri = os.environ["TOUTIAO_REDIRECT_URI"]
state = secrets.token_urlsafe(24)
url = TouTiaoOAuth.authorize_url(
    client_key, redirect_uri, ["toutiao.video.create", "toutiao.video.data"], state
)
# 服务端按会话保存 state，把授权 URL 展示给本人确认开放平台权限。
# 回调处理器取得完整 callback_url 后：
code = TouTiaoOAuth.code_from_callback(
    callback_url, redirect_uri=redirect_uri, expected_state=state
)
auth = TouTiaoOAuth.exchange_code(
    client_key, os.environ["TOUTIAO_CLIENT_SECRET"], code
)
granted = {part.strip() for part in auth.scope.split(",")}
if "toutiao.video.create" not in granted:
    raise RuntimeError("授权缺少 toutiao.video.create")
# 若授权了 toutiao.video.data，可先只读调用：
if "toutiao.video.data" in granted:
    videos = TouTiaoCreatorApi(auth).list_videos(cursor=0, count=10)
# 在服务端安全保存 auth.refresh_token、auth.expires_at、auth.refresh_expires_at、auth.scope。
# access_token 临近到期时：TouTiaoOAuth.refresh_access_token(client_key, auth)
auth.close()
```

这里的 `callback_url` 来自应用自己的回调处理器，`state` 必须取回授权开始时同一会话保存的值；`code_from_callback` 检查回调地址、`state` 与单个 `code`，不会自动监听端口或打开浏览器。换码接口按[官方文档](https://open.douyin.com/platform/resource/docs/openapi/account-permission/get-access-token)使用 URL 编码表单。头条刷新只针对 `access_token`：官方资料指出 `refresh_token` 不能续期，过期后须重新取得用户授权。刷新接口当前文档的参数表为空，仓库依照[官方 SDK 示例](https://open.douyin.com/platform/resource/docs/develop/guide/douyin-live-sdk/android)采用 `client_key`、`grant_type=refresh_token`、`refresh_token` 字段，以该接口文档指定的 multipart 表单发送；字段来自同平台示例，真实头条授权账号仍待验证。搜索页码大于 0 时 `search()` 抛出 `NotImplementedError`，旧 `getSearchInfo()` 返回 `(False, 错误说明, None)`。

单文件上传限制为 128 MiB；更大的视频需要官方分片接口，本仓库暂未封装。`get_video_data(payload)` 会原样发送调用方给出的请求体；不要把未知字段当作已验证的 API 契约。

### 真实账号验收所需材料与步骤

| 验收项 | 最小材料 | 操作与通过条件 |
| --- | --- | --- |
| 创作者网页登录 | 本人今日头条 App 或手机号与短信验证码、本机可见桌面；无需开放平台应用凭据 | 运行 `TouTiaoAuth.from_browser_login()`，在官方页面选择登录方式，之后核对 `creator_login_verified=True`；只读访问 `profile_v4/` 并检查新 Cookie。不输出或保存 Cookie；这一步不授予 Open API 权限。 |
| 网页读取 | 本人今日头条网页 Cookie，仅用于本机 | `TOUTIAO_COOKIE` 注入环境后运行 `python main.py search 人工智能 --page 0`；确认返回作品链接。浏览器登录会话的共享 `auth.cookie` 也可用于同一进程内尝试读取，但可能为空。Cookie 不证明 Open API 授权。 |
| OAuth 换码 | 已审核头条应用的 `client_key`、服务端 `client_secret`、登记的回调 URI；本人同意 `toutiao.video.create`，读取列表另需 `toutiao.video.data` | 生成授权 URL；本人授权；校验同一会话的 `state`；一次性 `code` 换得 `open_id`、Token、实际 `scope` 与有效期。只需把凭据放在本机/服务端，不需要发送到聊天。 |
| 凭据只读检查 | 上一步获得的 Token，且包含 `toutiao.video.data` | 调用 `list_videos()`；检查 `error_code=0`、账号对应和返回结构，不提交作品。若只授权了 create，跳过这一步。 |
| 上传和发布契约 | 获准的测试账号、`toutiao.video.create`、一段不超过 128 MiB 且不超过 1 分钟的测试视频、明确的测试发布内容 | 先 `upload_video()` 获取 `video.video_id`。确认上传响应后，由用户明确选择测试发布，再调用 `publish_video(video_id, text)`；核对 `item_id`、审核状态及请求体字段。当前官方发布页的 Body 参数表为“暂无数据”，故 `video_id`/`text` 需此步实测，不能仅凭 mock 测试宣称可用。 |
| 续期 | 已安全保存的 `refresh_token`、`client_key` | 临近 access token 到期时调用 `refresh_access_token()`，核对 `open_id` 未变化和新有效期；refresh token 到期后重新授权。 |

## 端点依据

| 端点 | 证据 |
| --- | --- |
| `GET https://so.toutiao.com/search`、`GET https://www.toutiao.com/article/{id}/` | 本仓库原有 `tou_tiao_api.py`，本轮保持兼容并加强输入与错误处理 |
| `GET https://mp.toutiao.com/mp/agw/media/user_login_status_api` | 本人已登录头条号页面网络请求：`code=0`、`data.is_login=true`；匿名 Python GET 返回 `data.is_login=false` |
| `GET https://mp.toutiao.com/mp/agw/media/check_user_auth` | 资料完善前曾观察到 `code=0`、`has_auth=false`；资料完善成功页出现后，独立 GET 为 HTTP 200、`code=100002`、`has_auth=false`。非零业务码语义未明，不据此判定发布权限 |
| `GET https://mp.toutiao.com/mp/agw/creator_center/draft_list?type=0&count=20` | 本人头条号草稿页实际请求与同源只读重试：`code=0`、`draft_list` 数组；临时草稿删除后复查已消失 |
| `POST https://mp.toutiao.com/mp/agw/creator_center/delete_draft?app_id=1231` | 本人网页删除指定临时草稿的请求与响应：JSON `drafts` 数组含 `draft_type`、`gid`，返回 `code=0`；只封装显式删除 |
| `GET https://open.snssdk.com/oauth/authorize/`、`POST https://open.snssdk.com/oauth/access_token/` | [头条获取授权码](https://open.douyin.com/platform/resource/docs/openapi/account-permission/toutiao-get-permission-code)、[获取 access token](https://open.douyin.com/platform/resource/docs/openapi/account-permission/get-access-token) |
| `POST https://open.snssdk.com/oauth/refresh_token/` | [官方刷新 access token 文档](https://open.douyin.com/platform/resource/docs/openapi/account-permission/refresh-access-token)、[头条帐号 OAuth 说明](https://open.douyin.com/platform/resource/docs/develop/permission/toutiao-or-xigua/OAuth2.0/) |
| `POST https://open.douyin.com/toutiao/video/upload/` | [官方上传视频文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/create-video/upload-video) |
| `POST https://open.douyin.com/toutiao/video/create/` | [官方发布视频文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/create-video/publish-video)；当前页面请求字段显示“暂无数据” |
| `GET https://open.douyin.com/toutiao/video/list/` | [官方视频列表文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/search-video/account-video-list/)，分页字段参照[公开 SDK](https://github.com/leafisme/douyin_open/blob/master/docs/Api/ToutiaoVideoListApi.md) |
| `POST https://open.douyin.com/toutiao/video/data/` | [官方特定视频数据文档](https://open.douyin.com/platform/resource/docs/openapi/video-management/toutiao/search-video/video-data/)；请求字段显示“暂无数据” |
| 图文发布范围 | [官方头条内容发布接入方案](https://open.douyin.com/platform/resource/docs/ability/content-management/toutiao-publish-solution/)说明开放接口暂不支持头条文章、微头条 |

创作者网页自动保存时确实调用了 `/mp/agw/article/publish`，本次只确认 `save=0`、响应 `pgc_id` 与草稿列表 `gid` 的关联；不能把该路径名或一次草稿结果解释为公开发布已验收。该请求有页面生成的签名参数和多项编辑器字段，仓库没有封装网页写入。先前的[公开项目协议记录](https://github.com/xc-2000/toutiao-auto-publisher/blob/main/README.md)只作背景参考。

## 验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q builder tou_tiao_api.py tou_tiao_creator_api.py main.py
```

测试使用假会话和假浏览器，不发送真实作品。匿名搜索首页与网页登录状态已做只读实测；真实 Python Cookie 会话、OAuth、详情、上传与发布需要对应账号在受控环境逐项实测。搜索分页在线返回重复作品，当前已禁止后续页请求。`.env` 已从版本追踪移除并忽略；使用 `.env.example` 查看变量名，不要提交 Cookie、Token 或 `client_secret`。
