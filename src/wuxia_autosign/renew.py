# -*- coding: utf-8 -*-
"""会话保活/体检服务: 定期带全套登录态打开活动页, 确认 QQ 会话存活并维持黏性

2026-10-07 实测结论(guide §5/§8, 决定了本脚本的定位):
    - 长期正本(p_skey/pt4_token/ptcz)【不能】凭自身换新 skey: proxy.html 只搬运
      不签发, 无 skey 打开页面会退回扫码登录(--no-skey 可复现)
    - QQ 客户端桥(localhost.ptlogin2.qq.com:4301)被 NTQQ 的 sec 握手+pt_local_tk
      校验封死, 连页面自身在 chromium 里都走不通
    - skey 签发权只在 ptlogin2, 背书方只有"游戏进程"(即 harvest.py 的游戏内活动页)
    - 但已登录会话黏性极强: 跨游戏重启/跨浏览器重开活动页, 都续同一个 skey
      (iRet=0), 直到服务端会话死透

=> 本脚本的价值: 每天/每周跑一次, 活着则确认 + 维持; 死了则第一时间报警,
   提示去开一次游戏活动页(harvest)或 login.py 重新登录。

用法:
    python renew.py                 # 对所有角色做保活体检
    python renew.py --role 月心澜   # 只体检指定角色(序号/名称/QQ号)
    python renew.py --no-skey       # 诊断模式: 不注入 skey, 复现"正本不能独立续签"
    python renew.py --headed        # 有头模式, 观察页面过程
    python renew.py --sync          # 成功后同步 GitHub Secret WUXIA_ROLES

依赖: pip install playwright  &&  python -m playwright install chromium
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
ROLES_FILE = BASE_DIR / "roles.json"
URL = "https://wuxia.qq.com/cp/a20230309_98549/index.html"

sys.path.insert(0, str(BASE_DIR.parent))
import wuxia_autosign.autosign as autosign
from wuxia_autosign.harvest import log, probe, sync_github   # 复用日志/探测/同步

# 各 cookie 要写入的域(登录态分层见 guide §2); milo 双写时 .qq.com 为准
DOMAIN_MAP = {
    "uin": ".qq.com", "skey": ".qq.com", "RK": ".qq.com", "ptcz": ".qq.com",
    "p_uin": ".game.qq.com", "p_skey": ".game.qq.com", "pt4_token": ".game.qq.com",
}
# 续签必备的长期正本
CORE = ("ptcz", "p_skey")


def build_cookies(jar, no_skey=False):
    out = []
    for n, dom in DOMAIN_MAP.items():
        v = jar.get(n)
        if not v or (no_skey and n == "skey"):
            continue
        out.append({"name": n, "value": v, "domain": dom, "path": "/"})
    return out


def renew_role(role, args):
    """单角色 SSO 续签, 成功时把新 cookie 写回 role['cookies'], 返回 (ok, 消息)"""
    from playwright.sync_api import sync_playwright
    name = role.get("name") or ("QQ%s" % role.get("uin", "?"))
    jar = role.get("cookies", {})
    missing = [k for k in CORE if not jar.get(k)]
    if missing:
        return False, "[%s] 缺少长期正本 %s, 无法 SSO 续签(先开一次游戏活动页跑 harvest, 或 login.py)" % (name, ",".join(missing))
    old_skey = jar.get("skey", "")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context()
        ctx.add_cookies(build_cookies(jar, args.no_skey))
        page = ctx.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            browser.close()
            return False, "[%s] 页面加载失败: %s" % (name, e)
        ok, info, new_jar = False, "", jar
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(6)
            new_jar = autosign.extract_cookies(ctx.cookies())
            if new_jar.get("skey"):
                ok, info = probe(new_jar, role)
                if ok:
                    break
        browser.close()
    if not ok:
        return False, ("[%s] 会话已失效(%s); 请开一次游戏活动页跑 harvest, 或重跑 login.py"
                       % (name, info or "等待超时未取到 skey"))
    tag = "换发新 skey" if new_jar.get("skey") != old_skey else "会话仍存活(skey 未变)"
    role["cookies"] = dict(new_jar)
    return True, "[%s] 会话体检通过: %s (%s)" % (name, tag, info)


def main():
    ap = argparse.ArgumentParser(description="会话保活/体检服务(维持并确证 QQ 登录态)")
    ap.add_argument("--role", default="", help="只续签指定角色: 序号/名称/QQ号")
    ap.add_argument("--no-skey", action="store_true", help="实验用: 不注入 skey, 验证正本可否独立续签")
    ap.add_argument("--headed", action="store_true", help="有头模式, 观察页面过程")
    ap.add_argument("--wait", type=int, default=30, help="等待自愈链路的秒数(默认30)")
    ap.add_argument("--sync", action="store_true", help="成功后同步 GitHub Secret")
    args = ap.parse_args()

    if not ROLES_FILE.exists():
        log("[!] 未找到 roles.json, 请先运行 login.py")
        sys.exit(1)
    data = json.loads(ROLES_FILE.read_text("utf-8"))
    roles = autosign.select_roles(data, args.role)
    changed = False
    for r in roles:
        try:
            ok, msg = renew_role(r, args)
        except Exception as e:
            ok, msg = False, "[%s] 运行异常: %s" % (r.get("name") or "?", e)
        log(msg)
        changed = changed or ok
    if changed:
        ROLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        log("已回写 roles.json")
        if args.sync:
            sync_github()
    else:
        log("没有角色体检通过, roles.json 未改动")


if __name__ == "__main__":
    main()
