# -*- coding: utf-8 -*-
"""游戏凭据收割服务: 自动获取游戏登录产生的长期凭据, 并同步到 GitHub

原理(逆向+实测验证): 游戏内嵌浏览器 QBrowser 的 cookie 库
    <游戏目录>/QBrowser/QCache/Cookies
保存着整套 QQ 登录态(uin/skey + game.qq.com 域的 p_skey/p_uin/pt4_token 长期正本)。

凭据刷新模型(2026-10-07 进程/cookie 库全程实测):
    - 游戏登录本身不写凭据; 登录时预热的 QBrowser(默认 baidu 主页)反而会重建
      profile, 清空上次的凭据
    - 只有在游戏里【打开 wuxia 活动页】的那一刻, ptlogin2 静默签发整套登录态写入
    - QQ 会话存活期间, 重开活动页会续回同一个 skey; 会话死透后才签发新值
    => 想让本服务收到新凭据, 玩游戏时记得顺手打开一次活动中心/周周载愿页

本服务监控该文件, 发现新凭据后:
    1. 复制读取(游戏运行中也不冲突), 白名单提取 cookie
    2. 用 AMS FLOW_INIT 只读探测凭据是否有效(不改任何签到状态)
    3. 按 uin 匹配 roles.json 里的角色, 回写新凭据(从不删除, 库被清空时跳过)
    4. --sync 时把 roles.json base64 后写入 GitHub Secret WUXIA_ROLES (gh CLI)

用法:
    python harvest.py --once            # 立即检查+收割一次(只更新本地 roles.json)
    python harvest.py --once --sync     # 收割并同步到 GitHub
    python harvest.py --watch           # 常驻监控, 每 60 秒检查一次(配 --sync)
    python harvest.py --install-task    # 注册 Windows 计划任务(每小时 --once --sync)
    python harvest.py --uninstall-task  # 删除计划任务

首次同步前需装好 GitHub CLI 并登录一次:
    winget install --id GitHub.cli
    gh auth login
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
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
ROLES_FILE = BASE_DIR / "roles.json"
LOG_FILE = BASE_DIR / "harvest.log"
TASK_WRAPPER = BASE_DIR / "harvest_task.cmd"
TASK_NAME = "WuxiaAutosignHarvest"
DEFAULT_CACHE = Path(r"E:\WeGameApps\天涯明月刀\QBrowser\QCache")
SECRET_NAME = "WUXIA_ROLES"

sys.path.insert(0, str(BASE_DIR.parent))
import wuxia_autosign.autosign as autosign


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
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


def harvest_once(args):
    """收割一次: 游戏jar -> 校验 -> 回写 roles.json -> (可选)同步 GitHub"""
    if not ROLES_FILE.exists():
        log("[!] 未找到 roles.json, 请先运行 python src/wuxia_autosign/login.py 登录一次")
        return False
    try:
        data = json.loads(ROLES_FILE.read_text("utf-8"))
    except Exception as e:
        log("[!] roles.json 无法解析(%s)" % e)
        return False
    try:
        uin, jar = read_jar(cache_dir(args))
    except RuntimeError as e:
        log("[!] %s" % e)
        return False
    if not uin or not jar.get("skey"):
        log("[!] 游戏 cookie 库里没有登录态(游戏里还没登录/打开过活动页), 本次跳过")
        return False

    matched = [r for r in data.get("roles", []) if r.get("uin") == uin]
    if not matched:
        log("[!] 游戏 cookie 库里的 QQ(%s) 不在 roles.json, "
            "请用该账号跑一次 login.py 添加角色" % autosign.mask(uin))
        return False

    if all(r.get("cookies", {}).get("skey") == jar.get("skey") for r in matched):
        log("游戏凭据无变化(QQ=%s), 跳过" % autosign.mask(uin))
        return True

    ok, info = probe(jar, matched[0])
    if not ok:
        log("[!] 游戏 cookie 库里的凭据已失效(%s), 不同步; 等下次游戏会话刷新后再试" % info)
        return False

    for r in matched:
        r["cookies"] = dict(jar)
    ROLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    log("已更新 %d 个角色的凭据(QQ=%s, %s)" % (len(matched), autosign.mask(uin), info))
    if args.sync:
        return sync_github()
    log("提示: 加 --sync 可同时推送到 GitHub Secret %s" % SECRET_NAME)
    return True


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


def sync_github():
    if shutil.which("gh") is None:
        b64 = base64.b64encode(ROLES_FILE.read_bytes()).decode()
        out = BASE_DIR / (SECRET_NAME + ".txt")
        out.write_text(b64, "utf-8")
        log("[!] 没有检测到 gh 命令(GitHub CLI), 无法自动同步。两选一:")
        log("    a) 安装并登录后重试: winget install --id GitHub.cli  然后 gh auth login")
        log("    b) 手动: 打开 %s 全选复制, 更新 GitHub Secret %s" % (out, SECRET_NAME))
        return False
    repo = git_repo()
    body = base64.b64encode(ROLES_FILE.read_bytes()).decode()
    cmd = ["gh", "secret", "set", SECRET_NAME, "--body", body]
    if repo:
        cmd += ["--repo", repo]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        log("[!] gh 执行失败: %s" % e)
        return False
    if r.returncode != 0:
        log("[!] gh secret set 失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
        log("    先运行 gh auth login 登录 GitHub 账号, 再重试")
        return False
    log("已同步 Secret %s -> GitHub%s" % (SECRET_NAME, (" (%s)" % repo) if repo else ""))
    return True


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
    ap = argparse.ArgumentParser(description="游戏凭据收割 + GitHub 同步服务")
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
