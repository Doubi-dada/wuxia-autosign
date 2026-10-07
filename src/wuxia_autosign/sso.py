# -*- coding: utf-8 -*-
"""QQ 客户端 SSO 凭据签发服务: 纯 HTTP 拿全套登录态(含 pt4_token 长期正本), 无浏览器/无游戏/无密码

原理(2026-10-07 实测打通, 见 guide §8.2): 本机 QQNT 客户端在线时, 在 127.0.0.1:4301
提供 ptlogin2 快速登录桥。链路:

    1. xui.ptlogin2 xlogin        -> 拿 pt_local_token(防CSRF)+pt_login_sig
    2. 桥 /pt_get_uins            -> 在线账号列表(谁登着 QQNT 就是谁)
    3. 桥 /pt_get_st              -> clientkey(必须带 .ptlogin2.qq.com cookie + 正确 Referer)
    4. ssl.ptlogin2/jump appid=21000501        -> .qq.com 的 uin/skey/RK/ptcz
    5. ssl.ptlogin2/jump appid=716027609       -> .game.qq.com 的 p_skey/p_uin/pt4_token

注意:
    - 签出来的是【QQNT 当前登录账号】的凭据; 目标账号不同时不会写入 roles.json
    - 桥端口固定探测 4301(QQNT 实测), 拒连说明 QQ 客户端没开
    - chromium 里走不通这条路(Private Network Access 拦公网->localhost), 所以用纯 HTTP

用法:
    python sso.py            # 走完链路, 按 uin 匹配 roles.json 并回写(需先 FLOW_INIT 验活)
    python sso.py --sync     # 成功后同步 GitHub Secret WUXIA_ROLES
    python sso.py --show     # 只显示桥上在线账号, 不写入
"""
import argparse
import http.cookiejar
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent
ROLES_FILE = BASE_DIR / "roles.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
S_URL = "https://wuxia.qq.com/cp/a20230309_98549/index.html"
BRIDGE = "https://localhost.ptlogin2.qq.com:4301"
APPID_WUXIA = "21000501"     # wuxia.qq.com 活动中心
APPID_GAME = "716027609"     # game.qq.com 域(p_skey/pt4_token 的签发方)

sys.path.insert(0, str(BASE_DIR.parent))
import wuxia_autosign.autosign as autosign
from wuxia_autosign.harvest import log, probe, sync_github


class SsoClient:
    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ctx),
            urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", UA)]

    def _get(self, url, referer, timeout=10):
        req = urllib.request.Request(url)
        req.add_header("Referer", referer)
        r = self.op.open(req, timeout=timeout)
        try:
            return r.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def _ck(self):
        return {c.name: c.value for c in self.jar}

    def mint(self):
        """走完整条 SSO 链, 返回 (uin, jar_dict); 失败抛 RuntimeError"""
        self._get(
            "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?appid=%s"
            "&proxy_url=https%%3A%%2F%%2Fgame.qq.com%%2Fcomm-htdocs%%2Fmilo%%2Fproxy.html"
            "&target=self&s_url=https%%3A%%2F%%2Fwuxia.qq.com%%2Fcp%%2Fa20230309_98549%%2Findex.html" % APPID_WUXIA,
            "https://wuxia.qq.com/")
        tk = self._ck().get("pt_local_token", "")
        if not tk:
            raise RuntimeError("xlogin 没有下发 pt_local_token")

        try:
            body = self._get("%s/pt_get_uins?callback=cb&r=%f&pt_local_tk=%s"
                             % (BRIDGE, time.time(), tk),
                             "https://xui.ptlogin2.qq.com/")
        except Exception as e:
            raise RuntimeError("桥不可达(%s); QQ 客户端(QQNT)开了吗?" % e)
        m = re.search(r"var_sso_uin_list=(\[.*?\]);", body, re.S)
        if not m:
            raise RuntimeError("桥返回异常: %s" % body[:120])
        users = json.loads(m.group(1))
        if not users:
            raise RuntimeError("QQ 客户端在线但没有已登录账号")
        uin = str(users[0]["uin"])
        nick = users[0].get("nickname", "")

        def clientkey():
            self._get("%s/pt_get_st?clientuin=%s&callback=cb2&r=%f&pt_local_tk=%s"
                      % (BRIDGE, uin, time.time(), tk), "https://xui.ptlogin2.qq.com/")
            key = self._ck().get("clientkey", "")
            if not key:
                raise RuntimeError("没有拿到 clientkey")
            return key

        # jump #1: wuxia appid -> .qq.com 全域登录态
        self._get("https://ssl.ptlogin2.qq.com/jump?clientuin=%s&clientkey=%s"
                  "&u1=https%%3A%%2F%%2Fwuxia.qq.com%%2Fcp%%2Fa20230309_98549%%2Findex.html"
                  "&appid=%s&daid=8&keyindex=19" % (uin, clientkey(), APPID_WUXIA),
                  "https://xui.ptlogin2.qq.com/")
        # jump #2: game 域 appid -> p_skey/p_uin/pt4_token 长期正本
        self._get("https://ssl.ptlogin2.qq.com/jump?clientuin=%s&clientkey=%s"
                  "&u1=https%%3A%%2F%%2Fgame.qq.com%%2Fcomm-htdocs%%2Fmilo%%2Fproxy.html"
                  "&appid=%s&daid=8&keyindex=19" % (uin, clientkey(), APPID_GAME),
                  "https://xui.ptlogin2.qq.com/")

        jar = autosign.extract_cookies(
            {"name": c.name, "value": c.value, "domain": c.domain} for c in self.jar)
        if not jar.get("skey"):
            raise RuntimeError("jump 后没有 skey, 链路异常")
        jar["uin"] = jar.get("uin", "o" + uin)
        return uin, nick, jar


def main():
    ap = argparse.ArgumentParser(description="QQ 客户端 SSO 凭据签发(含 pt4_token 长期正本)")
    ap.add_argument("--sync", action="store_true", help="成功后同步 GitHub Secret WUXIA_ROLES")
    ap.add_argument("--show", action="store_true", help="只显示桥上在线账号, 不写入")
    args = ap.parse_args()

    try:
        uin, nick, jar = SsoClient().mint()
    except RuntimeError as e:
        log("[!] %s" % e)
        sys.exit(1)
    log("SSO 签发成功: QQ=%s(%s) skey=%s.. p_skey=%s pt4_token=%s"
        % (autosign.mask(uin), nick, jar["skey"][:3],
           "有" if jar.get("p_skey") else "无", "有" if jar.get("pt4_token") else "无"))
    if args.show:
        return

    if not ROLES_FILE.exists():
        log("[!] 未找到 roles.json, 请先运行 login.py")
        sys.exit(1)
    data = json.loads(ROLES_FILE.read_text("utf-8"))
    matched = [r for r in data.get("roles", []) if r.get("uin") == uin]
    if not matched:
        log("[!] QQNT 当前登录的是 QQ%s(%s), 不在 roles.json; 想给这个号签到请先用它跑一次 login.py"
            % (autosign.mask(uin), nick))
        sys.exit(1)
    ok, info = probe(jar, matched[0])
    if not ok:
        log("[!] 新签凭据探测失败(%s), 不写入" % info)
        sys.exit(1)
    for r in matched:
        r["cookies"] = dict(jar)
    ROLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    log("已更新 %d 个角色的凭据(%s)" % (len(matched), info))
    if args.sync:
        sync_github()
    else:
        log("提示: 加 --sync 可同时推送到 GitHub Secret WUXIA_ROLES")


if __name__ == "__main__":
    main()
