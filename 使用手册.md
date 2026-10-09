# 天刀「周周载愿」自动签到 · 使用手册

> 基础配置约 10 分钟；再做第 6 步的「全自动凭证维护」后，日常**什么都不用管**。
> 你不需要会编程，也不用装 git，只要会「复制粘贴」。

---

## 系统是怎么跑的

```
你的电脑(每天开着 QQ 即可)
  └─ 凭证管家 harvest：每小时体检账号凭证，失效就自动续新
        ↓ 自动更新
GitHub（免费帮你定时干活）
  └─ Actions：每天 09:05 左右替所有角色许愿 / 周末领奖，可推送到微信
        ↓
腾讯游戏服务器
```

- 你的电脑**不需要为了签到而开机**——签到全在 GitHub 上跑；电脑上的管家只负责让凭证不过期。
- 支持多个 QQ / 多个角色一起签。
- 签到结果可以用「Server酱」推送到微信。

---

## 第 1 步：把代码变成你自己的仓库

用浏览器打开本项目仓库，点右上角 **Fork** → **Create fork**，几秒后你就有了自己的一份。

> 想更安全：Settings → 拉到最底 General → Danger Zone → Change visibility 改成 **Private**。

## 第 2 步：在电脑上装两个东西（每台电脑只做一次）

**2.1 安装 Python**：打开 <https://www.python.org/downloads/> 下载最新版，安装时**一定勾选** `☑ Add python.exe to PATH`。

**2.2 安装浏览器工具**：「开始菜单 → 输入 `powershell` → 回车」，粘贴下面两行（第二行要几分钟，别关窗口）：

```powershell
pip install playwright
python -m playwright install chromium
```

> 提示 `'pip' 不是命令`：Python 没装好（2.1 的勾没打），重装即可。
> 用 uv 管环境的话第一行换成 `uv pip install playwright`。

## 第 3 步：在自己电脑上登录一次

**3.1 拿代码**：在你 Fork 的仓库页面 **Code → Download ZIP**，解压到好找的地方（例如 `D:\wuxia-autosign`）。
在解压出来的文件夹里点一下顶部**地址栏**，输入 `powershell` 回车，直接在这个文件夹打开黑窗口。

**3.2 开始登录**：

```powershell
python src/wuxia_autosign/login.py
```

直接回车（= 选 1 网页登录）会弹出浏览器窗口，在里面扫码或点头像登录；**首次登录**页面可能让你选大区，跟着选一下。登录成功后不用别的操作，程序每 5 秒自动检测，检测到就继续。

看到这个就成功了：

```
[登录信息已生成] 接下来照着做 3 步就全部搞定
本次登录 QQ: 220*****28   大区: 2001   角色: 4643***********1906
roles.json 里现在有 1 个角色:
    - 月心澜 (QQ2200455428 2001区)
```

> 想再加角色/再加一个 QQ：换账号（或游戏里换角色后再登录一次网页）重跑一遍 `login.py` 即可，所有角色每天一起签。

## 第 4 步：把凭证填到 GitHub

仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**：

| Name | Secret 粘贴什么 |
|---|---|
| `WUXIA_ROLES` | 窗口里提示的 `WUXIA_ROLES.txt` 整行内容（双击文件 → Ctrl+A → Ctrl+C 最稳） |

三个**可选** Variables（同一个页面点上面的 Variables 标签页）：

| Name | 作用 | 默认 |
|---|---|---|
| `WUXIA_GIFT_INDEX` | 每天许愿第几个奖励（1~8，登录窗口会列出对照） | 3 |
| `WUXIA_RUN_TIME` | 每天几点签（北京时间，只看小时） | 09:05 |
| `WUXIA_PUSH_KEY` | Server酱 SendKey，签到结果推微信（建议放 Secrets） | 不推送 |

> 想给不同角色固定不同奖励：编辑 `src/wuxia_autosign/roles.json`，在对应角色里加一行 `"gift_index": 5`。

## 第 5 步：跑一次验证

仓库顶部 **Actions** → 若有黄色提示条点绿色按钮启用 → 左侧「天刀周周载愿自动签到」→ 右边 **Run workflow**（分支 main）→ 等一分钟左右看日志：

```
[月心澜] QQ=220*****28 大区=2001 角色=4643***********1906
奖励序号=3 今日周四 已许愿=False 待领奖=0
许愿成功: 铸神令*30
```

看到「许愿成功」就完成了。日志若写 `未到设定时间 09:05` 属正常——说明配置已生效、还没到点，它会在设定小时的 05 分左右真正执行。

## 第 6 步（推荐）：全自动凭证维护，之后不再扫码

第 3 步的凭证会过期（几天到几十天）。配置本步骤后，电脑会自动续期并自动更新 GitHub，**从此不再需要扫码登录**。

**6.1 建 GitHub 访问令牌**（用于自动改 Secret）：
GitHub → 头像 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token：

- Repository access：Only select repositories → 勾选你的 wuxia-autosign 仓库
- Permissions → Repository permissions → **Secrets → Read and write**

生成后把整行令牌（`github_pat_` 开头）用记事本存成文件 `src/wuxia_autosign/.gh_token`（一行；该文件已被 gitignore，不会被上传）。

**6.2 装加密依赖**（自动改 Secret 需要）：

```powershell
uv pip install pynacl      # 或 pip install pynacl
```

**6.3 注册每小时自动任务**：

```powershell
python src/wuxia_autosign/harvest.py --install-task
```

立刻手动跑一次确认：

```powershell
python src/wuxia_autosign/harvest.py --once --sync
```

看到 `已通过 GitHub API 更新 Secret WUXIA_ROLES` 即全部打通。

**6.4 日常只需保持一件事**：电脑上 **QQ 客户端（QQNT）里登录着游戏对应的 QQ 号**（多开也行）。管家每小时的动作：

```
体检现有凭证 → 全部有效就收工
            → 有失效：先试游戏内嵌浏览器缓存(玩游戏时开过活动页就有货)
                      再走 QQ 在线桥签发一套全新凭证
            → 自动更新本地 + GitHub
```

运行日志在 `src/wuxia_autosign/harvest.log`；想卸载任务：`python src/wuxia_autosign/harvest.py --uninstall-task`。

---

## 多账号 / 多角色速查

| 想做什么 | 怎么做 |
|---|---|
| 加一个角色 | 游戏里切角色 → 重跑 `login.py` → 重新粘一次 `WUXIA_ROLES` Secret |
| 加一个 QQ 账号 | QQNT 多开登录该号 → 重跑 `login.py` → 粘 Secret |
| 看电脑上有哪些 QQ 在线 | `python src/wuxia_autosign/sso.py --show` |
| 体检凭据死活 | `python src/wuxia_autosign/renew.py` |
| 本地立刻签一次 | `python src/wuxia_autosign/autosign.py --claim-only`（只看状态不许愿）/ 不带参数（单角色会让你选奖励） |

---

## 常见问题

| 现象 | 怎么办 |
|---|---|
| 日志 `登录态已失效(iRet=101)` | 配了第 6 步：确认 QQNT 登着游戏号，等下个整点或手动 `harvest.py --once --sync`；没配：重做第 3 步并更新 Secret |
| 日志 `账号未绑定大区(iRet=99998)` | 该号没在活动页登录过：浏览器打开 <https://wuxia.qq.com/cp/a20230309_98549/index.html> 登录选区一次，再跑第 3 步 |
| `base64` 相关报错 | 复制的 Secret 内容不完整：双击 `.txt` → Ctrl+A → Ctrl+C，别手动选半行 |
| 提示 `没有安装 Python 版 playwright` / `chromium 内核不可用` | 按提示执行 `pip install playwright` 和 `python -m playwright install chromium`（见 2.2） |
| 一直 `等待登录中...` | 浏览器窗口是否停在「选择大区」；扫码方式记得在手机上点「确认登录」 |
| 日志 `未到设定时间 09:05` | 正常，到点自动执行 |
| Actions 没有定时跑 | 按第 5 步启用 workflows；长期无活动仓库会被暂停，手动跑一次即恢复 |
| 自动同步提示 `GitHub API 拒绝: HTTP 403` | PAT 权限不对：确认 Fine-grained token 勾了本仓库 + Secrets Read and write（见 6.1），改完 Update token 即可，不用换文件 |
| `sso` 提示 `桥不可达` / `桥上没有在线账号` | QQ 客户端没开或没登录账号 |
| 想换电脑 | 新电脑重做第 2 步 + 拷贝整个项目文件夹（含 `.gh_token`、roles.json）+ `--install-task` |

---

## 附：命令一览

```powershell
python src/wuxia_autosign/login.py                # 登录/追加角色(默认网页登录)
python src/wuxia_autosign/login.py --qr           # 扫码登录(仅限之前成功登录过)
python src/wuxia_autosign/autosign.py             # 本地立刻签一次(单角色交互选奖励)
python src/wuxia_autosign/autosign.py --claim-only# 只看状态/领奖, 不许愿

python src/wuxia_autosign/harvest.py --once --sync   # 体检+收割+同步 GitHub(手动跑一次)
python src/wuxia_autosign/harvest.py --install-task  # 注册每小时自动维护计划任务
python src/wuxia_autosign/harvest.py --uninstall-task# 卸载计划任务
python src/wuxia_autosign/sso.py --show              # 看桥上有哪些 QQ 在线
python src/wuxia_autosign/renew.py                   # 会话体检
```

## 附：文件说明

| 文件 | 作用 |
|---|---|
| `src/wuxia_autosign/login.py` | 本地登录助手（Python playwright，多角色追加） |
| `src/wuxia_autosign/autosign.py` | 签到主程序（GitHub 上自动运行） |
| `src/wuxia_autosign/harvest.py` | 凭证管家：体检 → 游戏缓存/QQ在线桥 收割 → 同步 GitHub |
| `src/wuxia_autosign/sso.py` | QQ 客户端在线桥多账号签发（纯本地 HTTP，产出含长期票根） |
| `src/wuxia_autosign/renew.py` | 会话保活体检（死了报警并指引） |
| `.github/workflows/autosign.yml` | GitHub 定时任务配置（每小时触发） |
| `WUXIA_ROLES.txt` | 第 4 步要复制的内容（= roles.json 的 base64） |
| `src/wuxia_autosign/roles.json` | 所有角色的登录态+配置（隐私） |
| `src/wuxia_autosign/.gh_token` | GitHub 访问令牌（隐私） |
| `src/wuxia_autosign/harvest.log` | 凭证管家运行日志 |
| `qr_login_*.png` | 扫码登录的一次性二维码，可删 |

---

## 安全须知（请务必看）

1. `WUXIA_ROLES.txt`、`roles.json`、`state.json` 都是**私人登录凭证**，拿到的人可以登录你的游戏账号；`.gh_token` 是 GitHub 令牌，泄露等于别人能改你的仓库。**都不要发给任何人、不要传群/网盘。**
2. 这些文件已被 `.gitignore` 屏蔽，git 不会自动上传，也别手动绕过。
3. 运行日志里 QQ 号、角色 ID 均打码显示。
4. 建议仓库设为 **Private**。
5. 贴日志求助时只贴报错几行，不要贴凭证文件内容、不要在公开场合写自己的 QQ 号。

---

## 免责声明

本项目是爱好者编写的**非官方第三方工具**，与腾讯及《天涯明月刀》官方**没有任何关系**。

- 它通过调用游戏的**公开活动接口**完成签到，不使用外挂、不修改游戏客户端、不影响游戏平衡。
- 即便如此，这类自动化请求仍有被官方**风控或限制**的可能，后果由使用者自行承担。
- 请妥善保管自己的登录凭证，因泄露造成的损失与本项目无关。
- 使用前请自行判断是否符合当地法律法规及游戏用户协议。

## License

[MIT](LICENSE) © 2026 wuxia-autosign contributors
