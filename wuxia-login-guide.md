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
