# wuxia.qq.com 活动中心 登录态与状态存储分析指南

> 分析对象：`https://wuxia.qq.com/cp/a20230309_98549/index.html`（《天涯明月刀》活动中心）
> 前端代码：`./js/{config,flow,game,index,render,utils}.js` + `ide.js`（milo 封装）

---

## 1. 站点技术栈

| 层 | 实现 |
|---|---|
| 页面类型 | TGIDEAS 活动页（AMS 体系），`actName = '天涯明月刀-活动中心'` |
| 登录 SDK | `milo.js`（LoginManager）+ `ide.js`（`IDE.checkLogin / loginByQQ`） |
| 登录入口 | QQ：`IDE.loginByQQ({ appId: '21000501' })`（PT 快速登录）；微信：`loginByWX`（托管到腾讯游戏活动号 `wx1cd4fbe9335888fe`） |
| 登录强校验 | POST `https://ams.game.qq.com/ams/userLoginSvr` |
| 业务接口 | AMS 流程，actId `49_TXrbop`，token：`MAQQ9C`(查绑定) / `CVYmrd`(提交绑定) / `FcnDDL`(活动中心菜单) |
| 跨域前提 | `document.domain = 'qq.com'`（game.js 设置，供 game.qq.com 登录态同步用） |

---

## 2. 登录态相关 Cookie 清单（核心）

登录态按 `acctype` 分三类，由 milo 同时写在 **`.qq.com` 域（setQQ）** 和 **当前域（setLocal）**，**TTL = 7200 秒（2 小时）**。

### 2.1 QQ / PT 登录（acctype 为空或 pt）
| Cookie | 说明 |
|---|---|
| `uin` | QQ 号（带 `o` 前缀），身份标识 |
| `skey` | PT 登录态密钥，**最核心鉴权凭证** |
| `luin` / `lskey` | 副登录态 |
| `p_uin` / `p_skey` | game.qq.com 域代理登录态（长期正本） |
| `IED_LOG_INFO_NEW`（当前域）/ `IED_LOG_INFO2`（qq.com 域） | milo 登录信息缓存 |
| `logintype` | 数值型登录方式（1=QQ，2=微信） |

### 2.2 QQ 互联（acctype=qc）
`acctype` + `appid` + `openid` + `access_token`（+ `refresh_token`）

### 2.3 微信托管（acctype=wx）
`acctype` + `appid` + `openid` + `access_token` + `refresh_token`；另有 `wxopenid` / `wxrefresh_token` / `qm_keyst`

### 2.4 辅助 / 非登录态（勿混淆）
- `ieg_ams_token / ieg_ams_session_token / ieg_ams_token_time`：AMS 接口会话 token，非账号登录态
- `pgv_pvid`、`ts_uid`、aegis 等：纯统计
- ide.js 注销时清理的完整清单：
  - 当前域：`openid, appid, acctype, access_token, IED_LOG_INFO_NEW, refresh_token, ieg_ams_token_time, ieg_ams_session_token, ieg_ams_token, login_origin`
  - qq.com 域：`uin, skey, appid, acctype, access_token, openid, refresh_token, IED_LOG_INFO2`

**一句话判断登录态**：看 `.qq.com` 下 `acctype`；有 `uin+skey` → QQ PT；有 `openid+access_token+appid` → 互联/微信。有效性由 `userLoginSvr` 后端判定。

---

## 3. 登录判定流程（代码实锤）

```js
// game.js — 鉴权失败时直接上报，证明 PT 登录态 = uin + skey
aegis.infoAll(JSON.stringify({ skey: IDE.get('skey'), uin: IDE.get('uin'), cookie: document.cookie }))

// utils.js — 业务直接依赖的三个 cookie
milo.cookie.get('acctype'); milo.cookie.get('openid'); milo.cookie.get('access_token')
```

页面加载状态机（game.js `init`）：
```
IDE.checkLogin
 ├─ 成功 → paasData.openid = userInfo.userUin → 隐藏登录框 → 查绑定/进活动
 └─ 失败 → $('#login') 显示 → 自动 login()（IDE.loginByQQ 弹 ptlogin2 静默快登）
```

`checkRealLogin` 服务端校验逻辑：先读 `acctype`，**缺省按 `pt` 处理，用 `uin+skey` 强校验**；非 PT 用 `appid+openid+access_token` 校验。

---

## 4. 大区/角色绑定状态的存储

**绑定关系不存 cookie、不长期存 localStorage，唯一真源在 AMS 服务端（按 uin 存 bindarea）。**

| 层 | 位置 | 说明 |
|---|---|---|
| 持久层 | AMS 服务端 | flow_177324 查询（token `MAQQ9C`）、flow_177325 提交（token `CVYmrd`, `iAreaChooseType:2`），返回 `Farea/FroleId/FroleName` |
| 传参层 | URL query | 游戏客户端拉起时带 `idc/roleid/playername/svrID`；URL 优先；iframe 子页透传 `area/playername/roleid/uin/from` |
| 运行层 | 内存 `paasData` | `setUrlParamByIde()` 仅回填 `urlParam.idc/playername/roleid` 与 `areaID/roleID`，不落盘 |
| 缓存层 | localStorage（wuxia.qq.com） | `local539865`（授权缓存，30s）、`wuxiadays539865`（授权状态，10 天）、`preLoaded`（GET 参数）、`from539865`（来源） |

兼容处理：`svrID`（2 位测试服号）拼接成 4 位 `idc`；`idc=000 → 2501`。

判定顺序：**URL 参数优先 → 没有 → 查 AMS → 没绑定 → 弇角色选择器**（`gameact.qq.com/comm-htdocs/js/game_area/wuxia_server_select.js`，分 `_SQ`/`_WX`）。

---

## 5. 登录态自动更新机制（skey 2h 过期怎么办）

**代码中无定时续期。7200s 只是 milo 写 cookie 的 TTL，"自动更新" = checkLogin 失败后的自愈链路：**

1. **正本同步（主链路）**：`userLoginSvr` 强校验失败 → `proxyManager.get()` 从 **game.qq.com 域**拉回长期登录态（`pt:{p_skey,p_uin}` / `wg:{appid,openid,access_token,refresh_token}` / qc / wx）→ `setLocal/setQQ(..., 7200)` 重写短期 cookie → **重试 checkRealLogin**。普通用户无感续期的原理就在这。
2. **静默重登**：正本也失效时，fail 分支自动 `IDE.loginByQQ`；QQ 客户端在线或长期凭据（`p_skey/superkey/ptcz`）存活时，ptlogin2 iframe **静默签发全新 skey**。
3. **微信刷新**：`wxrefresh_token/qm_keyst` → `c.y.qq.com/base/fcgi-bin/login_get_musickey.fcg` 换新 `wxaccess_token` → setLocal 7200s；失败 `-40030` 则需重登。

skey 由 ptlogin2 服务端签发，**本地无法续签**，寿命跟随 QQ 会话。

---

## 6. 自动化（autosign）实践要点

### 6.1 凭据分层保存
| 凭据 | 实际寿命 | 策略 |
|---|---|---|
| `wuxia.qq.com` 的 `uin/skey` | ~2h | 不单独保存，视为缓存 |
| `.qq.com` 的 `p_uin/p_skey/superkey/ptcz` | 天级~周级 | **长期凭据，保留整个 `.qq.com` + `game.qq.com` cookie jar** |
| `wxrefresh_token/qm_keyst` | 天级 | 微信态换新 access_token 用 |

### 6.2 每次任务的标准流程
```
1. 载入长期 cookie jar（.qq.com 全量 + game.qq.com）
2. 打开活动页（或无头浏览器加载 ide.js 调 IDE.checkLogin）
   → 触发自愈：proxy 同步/静默重登 → 刷新 uin/skey（7200s）
3. userLoginSvr 校验通过后，再调 AMS 业务接口（带 iUin/acctype）
4. 失效信号：userLoginSvr 拒绝 / 返回未登录 code
   → 重走登录链路；长期会话彻底失效 → 需人工重新授权一次（扫码/客户端确认）
```

### 6.3 风控底线
- skey 不存在无限续期；脚本只能把"无感续期"窗口做到 QQ 会话极限
- AMS 接口带 `ieg_ams_token` 防重放，正常走页面流程即可获得
- 建议任务间隔合理化，避免高频触发 userLoginSvr

---

## 7. 附：关键资源清单

| 资源 | 地址 |
|---|---|
| 活动页 | `https://wuxia.qq.com/cp/a20230309_98549/index.html` |
| 业务 JS | `https://wuxia.qq.com/cp/a20230309_98549/js/*.js` |
| IDE 登录组件 | `https://game.gtimg.cn/images/js/ide/latest/ide.js` |
| milo | `https://ossweb-img.qq.com/images/js/milo_bundle/milo.js` |
| 登录强校验 | `https://ams.game.qq.com/ams/userLoginSvr` |
| 角色选择器 | `https://gameact.qq.com/comm-htdocs/js/game_area/wuxia_server_select.js`（`_SQ`/`_WX`） |
| 微信 token 刷新 | `https://c.y.qq.com/base/fcgi-bin/login_get_musickey.fcg` |

---

## 8. 附: 游戏内嵌浏览器(QBrowser)凭据模型（2026-10-07 实测）

游戏用 `WuXia_Client_dx12.exe → QBrowser.exe → QBrowserProcess.exe`(CEF) 打开活动页，cookie 库在 `<游戏目录>/QBrowser/QCache/Cookies`(Chromium SQLite，值明文)。

| 时点 | cookie 库变化 |
|---|---|
| 游戏退出 | 不变，凭据保留 |
| 客户端启动/登录角色 | 登录链路**不写**凭据；登录时预热的 QBrowser（默认 baidu 主页）会重建 profile，**清空上次凭据** |
| **在游戏里打开 wuxia 活动页** | **ptlogin2 静默签发整套登录态写入**：`.qq.com` 的 uin/skey/RK/ptcz + `.game.qq.com` 的 p_skey/p_uin/pt4_token（长期正本）。这就是"游戏内免登录"的全部真相 |

补充实测结论：
- 打开活动页的 URL（从缓存还原）：`index.html?game_id=609020401&idc=20&svrID=1&roleid=...&playername=...&tabid=1|1`——只带角色定位参数（§4 的 svrID 拼接规则在此验证），鉴权全靠 cookie
- 反作弊会屏蔽外部进程读取 QBrowser 命令行（PEB），但 cookie 库可直接复制读取
- QQ 会话存活期间重开活动页，ptlogin2 续回**同一个 skey**；会话死透后才签发新值
- 自动化意义：监控该库即可零扫码收割新鲜凭据（见 `src/wuxia_autosign/harvest.py`），前提是玩游戏时顺手开一次活动页

### 8.1 SSO 续签通道实测矩阵（2026-10-07，chromium headless + NTQQ）

| 通道 | 结果 |
|---|---|
| 游戏内活动页（QBrowser + 游戏 IPC 背书） | ✅ `harvest.py` 已产品化 |
| 长期正本自换（p_skey/pt4_token/ptcz + headless 开活动页） | ❌ `proxy.html` 只搬运正本不签发；无 skey 时页面退回扫码登录 |
| QQ 客户端桥（`localhost.ptlogin2.qq.com:4301`），浏览器内 | ❌ chromium 的 Private Network Access 拦公网→localhost；sec 握手端口(9410/16873)本机未监听 |
| **QQ 客户端桥，纯 HTTP 复刻** | ✅ **打通**（见 §8.2），`sso.py` 已产品化 |
| 带活 skey 的保活访问 | ✅ 会话黏性：跨游戏重启/跨浏览器重开活动页均续同一 skey，直到服务端会话死透 |

结论：skey 签发权在 ptlogin2，背书方有游戏进程（harvest）和 QQNT 客户端桥（sso）；`p_skey/pt4_token` 本身不能独立兑换新 skey，但可以通过客户端桥重新签发。`renew.py` 据此定位为"会话保活/体检"。

补充（2026-10-07 实测）：`comm.ams.game.qq.com` 在 `.game.qq.com` 的 cookie 范围内，AMS 鉴权可用 **p_skey 兜底**——skey 已死而正本存活时 FLOW_INIT 依旧 iRet=0。即本地凭据集的有效期 = **skey 与 p_skey 任一存活**，这也是 `harvest.py --once` 体检所测的真实状态。

### 8.2 QQNT 客户端桥 SSO 链路（纯 HTTP，2026-10-07 实测打通）

QQNT 在线时监听 `127.0.0.1:4301`（另见 4001/4310/5283/8082/9210）。关键坑：桥请求**必须携带 `.ptlogin2.qq.com` 域 cookie**（尤其 `pt_local_token`）且 Referer 合法，否则一律 400 空响应；chromium 因 PNA 无法从页面发起，需在脚本层直连（自签证书要关校验）。

```
1. GET xui.ptlogin2.qq.com/cgi-bin/xlogin?appid=21000501&proxy_url=...milo/proxy.html
   -> Set-Cookie: pt_local_token / pt_login_sig / pt_guid_sig
2. GET localhost.ptlogin2.qq.com:4301/pt_get_uins?pt_local_tk=<token>   [带cookie+Referer]
   -> var var_sso_uin_list=[{"uin":...,"nickname":...}]   (QQNT 当前登录账号)
3. GET .../4301/pt_get_st?clientuin=<uin>&pt_local_tk=<token>
   -> Set-Cookie: clientkey (96字符, 短时效)
4. GET ssl.ptlogin2.qq.com/jump?clientuin&clientkey&appid=21000501&u1=<wuxia>&daid=8&keyindex=19
   -> .qq.com 的 uin/skey/RK/ptcz
5. GET 同上但 appid=716027609&u1=game.qq.com/comm-htdocs/milo/proxy.html
   -> .game.qq.com 的 p_skey/p_uin/pt4_token（长期正本就是这么签出来的）
```

多账号与端口：每个在线 QQNT 实例各占一个桥端口（4301/4303/4305/…），`pt_get_uins` 返回该实例登录的全部账号（一个实例可挂多个号）。**坑：非实例首选账号的 `pt_get_st` 必须带 `pt_local_data=1`，否则 400**；偶发 400 时换新 `pt_local_token` 重试即可。`sso.py` 已实现全端口扫描 + 按账号签发，只回写 roles.json 里匹配的角色。

限制：签的是 **QQNT 在线账号**的凭据——目标账号不在线时签不了；账号在 roles.json 里没有对应角色时 `sso.py` 会跳过并提示。
