# -*- coding: utf-8 -*-
"""游戏凭据收割服务: 自动获取长期凭据并同步到 GitHub

执行流程:
    0. 体检现有凭据  roles.json 里每个角色的 cookie 用 FLOW_INIT 只读探测,
       全部有效则直接收工(每小时计划任务多数时候走到这就结束)
    1. 游戏目录缓存  <游戏目录>/QBrowser/QCache/Cookies
       游戏内打开一次 wuxia 活动页, ptlogin2 静默签发整套登录态写入
       (uin/skey + .game.qq.com 的 p_skey/p_uin/pt4_token 长期正本)
    2. QQNT 桥 SSO   (sso.py, 纯 HTTP)
       本机 QQ 客户端在线时, 通过 127.0.0.1:4301 桥签发在线账号的全套凭据

每条通道取到凭据后同样用 FLOW_INIT 只读探测(不产生签到副作用), 活的才回写
roles.json 并(可选)同步 GitHub Secret WUXIA_ROLES。

同步 GitHub 优先级: REST API(需 token) -> gh CLI -> 手工 txt
token 配置(三选一):
    a) 环境变量 WUXIA_GH_TOKEN 或 GH_TOKEN
    b) 文件 src/wuxia_autosign/.gh_token (一行, 已被 .gitignore)
    c) 安装并登录 gh CLI (winget install --id GitHub.cli && gh auth login)
token 需要 classic PAT 的 repo 权限, 或 fine-grained PAT 的 Actions secrets 写权限。
依赖 PyNaCl(加密 Secret 用): uv pip install pynacl

用法:
    python harvest.py --once            # 收割一次(只更新本地 roles.json)
    python harvest.py --once --sync     # 收割并同步 GitHub
    python harvest.py --watch           # 常驻监控, 每 60 秒检查一次(配 --sync)
    python harvest.py --install-task    # 注册 Windows 计划任务(每小时 --once --sync)
    python harvest.py --uninstall-task  # 删除计划任务
"""
import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
ROLES_FILE = BASE_DIR / "roles.json"
LOG_FILE = BASE_DIR / "harvest.log"
TASK_WRAPPER = BASE_DIR / "harvest_task.cmd"
TOKEN_FILE = BASE_DIR / ".gh_token"
TASK_NAME = "WuxiaAutosignHarvest"
DEFAULT_CACHE = Path(r"E:\WeGameApps\天涯明月刀\QBrowser\QCache")
SECRET_NAME = "WUXIA_ROLES"

sys.path.insert(0, str(BASE_DIR.parent))
import wuxia_autosign.autosign as autosign


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line)
    except UnicodeEncodeError:   # GBK 控制台打不出某些字符(如昵称里的 †)时降级
        enc = sys.stdout.encoding or "gbk"
        print(line.encode(enc, errors="replace").decode(enc))
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def cache_dir(args):
    return Path(args.game_dir or os.environ.get("WUXIA_GAME_DIR") or DEFAULT_CACHE)


def read_jar(cdir):
    """复制游戏 cookie 库后读取, 返回 (uin, jar); 库里没有登录态则 (None, {})"""
    src = cdir / "Cookies"
    if not src.exists():
        raise RuntimeError("没找到 %s (游戏目录不对? 用 --game-dir 指定 QCache 目录)" % src)
    tmp = Path(tempfile.gettempdir()) / ("wuxia_jar_%d.db" % os.getpid())
    try:
        shutil.copyfile(src, tmp)
        db = sqlite3.connect(str(tmp))
        rows = db.execute("SELECT name, value, host_key FROM cookies").fetchall()
        db.close()
    except Exception as e:
        raise RuntimeError("读取游戏 cookie 库失败(%s)" % e)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    jar = autosign.extract_cookies(
        {"name": n, "value": v, "domain": h} for n, v, h in rows)
    return jar.get("uin", "").lstrip("o"), jar


def probe(jar, role):
    """用 FLOW_INIT 只读探测凭据有效性(不产生签到/许愿副作用), 返回 (ok, 说明)"""
    try:
        resp = autosign.emit(autosign.cookie_header(jar), autosign.calc_gtk(jar["skey"]),
                             autosign.FLOW_INIT, role.get("area", ""), role.get("roleid", ""))
    except Exception as e:
        return False, "接口异常: %s" % e
    iret = autosign.is_login_expired(resp)
    # 0=正常; 99998=登录态有效但该角色未绑定大区; 101=凭据已死
    return iret in ("0", "99998"), "iRet=%s" % iret


def _match(data, uin):
    return [r for r in data.get("roles", []) if r.get("uin") == uin]


def check_roles(data):
    """体检 roles.json 现有凭据, 返回 (失效角色列表)"""
    dead = []
    for r in data.get("roles", []):
        name = r.get("name") or ("QQ%s" % r.get("uin", "?"))
        jar = r.get("cookies", {})
        if not jar.get("skey"):
            log("[check] %s 没有 skey, 视为失效" % name)
            dead.append(r)
            continue
        ok, info = probe(jar, r)
        if ok:
            log("[check] %s 有效(%s)" % (name, info))
        else:
            log("[check] %s 已失效(%s)" % (name, info))
            dead.append(r)
    return dead


def try_jar(args, data):
    """通道1: 游戏目录缓存。返回 updated(已更新) / ok(有效无需动) / fail(转下一通道)"""
    try:
        uin, jar = read_jar(cache_dir(args))
    except RuntimeError as e:
        log("[jar] %s, 转 SSO" % e)
        return "fail"
    if not uin or not jar.get("skey"):
        log("[jar] 游戏 cookie 库里没有登录态(游戏重启后需开一次活动页), 转 SSO")
        return "fail"
    matched = _match(data, uin)
    if not matched:
        log("[jar] 游戏 cookie 库的 QQ(%s) 不在 roles.json, 转 SSO" % autosign.mask(uin))
        return "fail"
    changed = any(r.get("cookies", {}).get("skey") != jar.get("skey") for r in matched)
    ok, info = probe(jar, matched[0])
    if not ok:
        log("[jar] 游戏缓存凭据已失效(%s), 转 SSO" % info)
        return "fail"
    if not changed:
        log("[jar] 凭据无变化且有效(%s)" % info)
        return "ok"
    for r in matched:
        r["cookies"] = dict(jar)
    log("[jar] 已更新 %d 个角色的凭据(%s)" % (len(matched), info))
    return "updated"


def try_sso(data):
    """通道2: QQNT 桥 SSO 多账号(见 sso.py)。返回 updated / ok / fail"""
    from wuxia_autosign.sso import update_roles   # 延迟导入
    try:
        updated, matched = update_roles(data)
    except RuntimeError as e:
        log("[sso] %s" % e)
        return "fail"
    if updated:
        return "updated"
    if matched:
        return "ok"
    return "fail"


def harvest_once(args):
    """体检 -> 游戏缓存 -> SSO; 有更新时回写 roles.json 并(可选)同步"""
    if not ROLES_FILE.exists():
        log("[!] 未找到 roles.json, 请先运行 python src/wuxia_autosign/login.py 登录一次")
        return False
    try:
        data = json.loads(ROLES_FILE.read_text("utf-8"))
    except Exception as e:
        log("[!] roles.json 无法解析(%s)" % e)
        return False

    # 0. 先体检现有凭据, 全部有效就不折腾收割通道
    dead = check_roles(data)
    if data.get("roles") and not dead:
        log("[check] %d 个角色凭据全部有效, 无需收割" % len(data["roles"]))
        return True

    for step in (lambda: try_jar(args, data), lambda: try_sso(data)):
        status = step()
        if status == "updated":
            ROLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            if args.sync:
                return sync_github()
            log("提示: 加 --sync 可同时推送到 GitHub Secret %s" % SECRET_NAME)
            return True
        if status == "ok":
            return True
    still = ", ".join((r.get("name") or r.get("uin", "?")) for r in dead)
    log("两条通道都没能修复失效角色: %s" % still)
    return False


def git_repo():
    """从 origin 远程地址解析 owner/repo"""
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                           cwd=str(BASE_DIR.parent.parent),
                           capture_output=True, text=True, timeout=10)
        url = r.stdout.strip()
    except Exception:
        url = ""
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def gh_token():
    """token 优先级: 环境变量 -> .gh_token 文件 -> gh auth token"""
    for k in ("WUXIA_GH_TOKEN", "GH_TOKEN"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    if TOKEN_FILE.exists():
        v = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    if shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return ""


def sync_github_api(token):
    """REST API + SealedBox 加密更新 Secret; True成功 / None未配好或失败(走兜底)"""
    repo = git_repo()
    if not repo:
        log("[!] 解析不到 origin 仓库地址, 无法走 API")
        return None
    try:
        import nacl.encoding
        import nacl.public
    except ImportError:
        log("[!] 缺少 PyNaCl(加密 Secret 需要): uv pip install pynacl")
        return None
    api = "https://api.github.com/repos/%s/actions/secrets" % repo
    hdr = {"Authorization": "Bearer %s" % token,
           "Accept": "application/vnd.github+json",
           "User-Agent": "wuxia-autosign"}

    def call(url, data=None, method="GET"):
        req = urllib.request.Request(
            url, method=method, data=json.dumps(data).encode() if data else None)
        for k, v in hdr.items():
            req.add_header(k, v)
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")

    try:
        _, pk = call(api + "/public-key")
        box = nacl.public.SealedBox(nacl.public.PublicKey(pk["key"], nacl.encoding.Base64Encoder()))
        plain = base64.b64encode(ROLES_FILE.read_bytes()).decode("utf-8")
        enc = base64.b64encode(box.encrypt(plain.encode("utf-8"))).decode("utf-8")
        call(api + "/" + SECRET_NAME, {"encrypted_value": enc, "key_id": pk["key_id"]}, "PUT")
        log("已通过 GitHub API 更新 Secret %s (%s)" % (SECRET_NAME, repo))
        return True
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(150).decode("utf-8", "replace")
        except Exception:
            pass
        log("[!] GitHub API 拒绝: HTTP %s %s (token 权限对吗?)" % (e.code, detail))
    except Exception as e:
        log("[!] GitHub API 异常: %s" % e)
    return None


def sync_github():
    """更新 Secret: API -> gh CLI -> 手工 txt, 三级兜底"""
    token = gh_token()
    if token and sync_github_api(token) is True:
        return True
    if shutil.which("gh"):
        repo = git_repo()
        body = base64.b64encode(ROLES_FILE.read_bytes()).decode()
        cmd = ["gh", "secret", "set", SECRET_NAME, "--body", body]
        if repo:
            cmd += ["--repo", repo]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as e:
            log("[!] gh 执行失败: %s" % e)
            r = None
        if r and r.returncode == 0:
            log("已通过 gh CLI 更新 Secret %s%s" % (SECRET_NAME, (" (%s)" % repo) if repo else ""))
            return True
        if r:
            log("[!] gh secret set 失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
    out = BASE_DIR / (SECRET_NAME + ".txt")
    out.write_text(base64.b64encode(ROLES_FILE.read_bytes()).decode(), "utf-8")
    log("[!] 自动同步都不可用。手动: 打开 %s 全选复制, 更新 GitHub Secret %s" % (out, SECRET_NAME))
    log("    想自动同步: 建一个 PAT 放进环境变量 WUXIA_GH_TOKEN 或文件 %s" % TOKEN_FILE)
    return False


def jar_sig(cdir):
    try:
        st = (cdir / "Cookies").stat()
        return (int(st.st_mtime), st.st_size)
    except OSError:
        return None


def watch(args):
    cdir = cache_dir(args)
    log("开始监控 %s (每 %d 秒检查一次, Ctrl+C 退出, sync=%s)"
        % (cdir, args.interval, args.sync))
    last = jar_sig(cdir)
    harvest_once(args)   # 启动时先来一次
    while True:
        time.sleep(args.interval)
        cur = jar_sig(cdir)
        if cur != last:
            time.sleep(6)   # 等数据库写稳定
            last = jar_sig(cdir)
            log("检测到游戏 cookie 库变化, 开始收割...")
            harvest_once(args)


def install_task():
    """注册每小时一次的计划任务(当前用户权限即可)"""
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    runner = pyw if pyw.exists() else py
    script = str(Path(__file__).resolve())
    TASK_WRAPPER.write_text(
        '@echo off\r\n"%s" "%s" --once --sync\r\n' % (runner, script), "utf-8")
    r = subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", TASK_NAME,
         "/TR", '"%s"' % TASK_WRAPPER, "/SC", "HOURLY", "/MO", "1"],
        capture_output=True, text=True)
    if r.returncode != 0:
        log("[!] 计划任务创建失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
        return False
    log("已创建计划任务 %s (每小时执行一次 --once --sync), 日志见 %s" % (TASK_NAME, LOG_FILE))
    return True


def uninstall_task():
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    log("计划任务已删除" if ok else "[!] 删除失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
    return ok


def main():
    ap = argparse.ArgumentParser(description="凭据收割服务: 游戏缓存/QQNT桥SSO -> GitHub")
    ap.add_argument("--once", action="store_true", help="立即检查+收割一次")
    ap.add_argument("--watch", action="store_true", help="常驻监控模式")
    ap.add_argument("--sync", action="store_true", help="收割成功后同步 GitHub Secret")
    ap.add_argument("--interval", type=int, default=60, help="watch 模式检查间隔秒数(默认60)")
    ap.add_argument("--game-dir", default="", help="QBrowser/QCache 目录(默认自动定位)")
    ap.add_argument("--install-task", action="store_true", help="注册每小时运行的计划任务")
    ap.add_argument("--uninstall-task", action="store_true", help="删除计划任务")
    args = ap.parse_args()

    if args.install_task:
        sys.exit(0 if install_task() else 1)
    if args.uninstall_task:
        sys.exit(0 if uninstall_task() else 1)
    if args.watch:
        try:
            watch(args)
        except KeyboardInterrupt:
            log("监控已停止")
    elif args.once:
        sys.exit(0 if harvest_once(args) else 1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
