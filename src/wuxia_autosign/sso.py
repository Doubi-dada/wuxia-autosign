# -*- coding: utf-8 -*-
"""QQ 客户端 SSO 凭据签发服务(多账号): 纯 HTTP 拿全套登录态(含 pt4_token 长期正本)

原理(2026-10-07 实测打通, 见 guide §8.2): 本机每个在线的 QQNT 实例各占一个
ptlogin2 快速登录桥端口(4301/4303/4305/4307/4309...), 各自公布自己登录的账号:

    1. xui.ptlogin2 xlogin             -> pt_local_token(防CSRF)
    2. 扫描所有桥端口 /pt_get_uins     -> 在线账号清单 {uin: (昵称, 端口)}
    3. 对目标账号在其端口 /pt_get_st   -> clientkey
    4. ssl.ptlogin2/jump appid=21000501  -> .qq.com 的 uin/skey/RK/ptcz
    5. ssl.ptlogin2/jump appid=716027609 -> .game.qq.com 的 p_skey/p_uin/pt4_token

坑(实测): 桥请求必须带 .ptlogin2.qq.com 域 cookie + 正确 Referer, 否则 400 空响应;
chromium 因 PNA 走不通, 必须脚本层直连(桥为自签证书, 要关校验)。

用法:
    python sso.py            # 扫描所有在线账号, 匹配 roles.json 的都签发并回写
    python sso.py --sync     # 有更新则同步 GitHub Secret WUXIA_ROLES
    python sso.py --show     # 只列出桥上在线的账号, 不写入
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
APPID_WUXIA = "21000501"     # wuxia.qq.com 活动中心
APPID_GAME = "716027609"     # game.qq.com 域(p_skey/pt4_token 的签发方)

sys.path.insert(0, str(BASE_DIR.parent))
import wuxia_autosign.autosign as autosign
from wuxia_autosign.harvest import log, probe, sync_github


class SsoClient:
    # 每个在线 QQNT 实例占一个; 4310 部分版本也会出现
    PORTS = (4301, 4303, 4305, 4307, 4309, 4310)

    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ctx),
            urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", UA)]
        self.tk = ""
        self._xlogin()

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

    def _xlogin(self):
        self._get(
            "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?appid=%s"
            "&proxy_url=https%%3A%%2F%%2Fgame.qq.com%%2Fcomm-htdocs%%2Fmilo%%2Fproxy.html"
            "&target=self&s_url=https%%3A%%2F%%2Fwuxia.qq.com%%2Fcp%%2Fa20230309_98549%%2Findex.html" % APPID_WUXIA,
            "https://wuxia.qq.com/")
        self.tk = self._ck().get("pt_local_token", "")
        if not self.tk:
            raise RuntimeError("xlogin 没有下发 pt_local_token")

    def _uins_on(self, port):
        """问一个桥端口要它登录的账号, 返回 [{uin,nickname}]; 端口不通返回 []"""
        try:
            body = self._get(
                "https://localhost.ptlogin2.qq.com:%d/pt_get_uins?callback=cb&r=%f&pt_local_tk=%s"
                % (port, time.time(), self.tk),
                "https://xui.ptlogin2.qq.com/", timeout=3)
        except Exception:
            return []
        m = re.search(r"var_sso_uin_list=(\[.*?\]);", body, re.S)
        if not m:
            # 可能 token 过期了, 刷新后重试一次
            self._xlogin()
            try:
                body = self._get(
                    "https://localhost.ptlogin2.qq.com:%d/pt_get_uins?callback=cb&r=%f&pt_local_tk=%s"
                    % (port, time.time(), self.tk),
                    "https://xui.ptlogin2.qq.com/", timeout=3)
            except Exception:
                return []
            m = re.search(r"var_sso_uin_list=(\[.*?\]);", body, re.S)
            if not m:
                return []
        try:
            return json.loads(m.group(1))
        except Exception:
            return []

    def accounts(self):
        """扫描所有桥端口, 返回 {uin: {'nick': 昵称, 'port': 端口}}"""
        out = {}
        for port in self.PORTS:
            for u in self._uins_on(port):
                uin = str(u.get("uin", ""))
                if uin and uin not in out:
                    out[uin] = {"nick": u.get("nickname", ""), "port": port}
        return out

    def mint(self, uin, port):
        """对指定账号走完整签发链, 返回 jar(dict); 每账号独立提取, 互不覆盖"""
        def clientkey():
            # 实测: 非实例首选账号必须带 pt_local_data=1, 否则 400;
            # 偶发 400 时换新 pt_local_token 重试一次
            for _ in range(2):
                try:
                    self._get(
                        "https://localhost.ptlogin2.qq.com:%d/pt_get_st?clientuin=%s&callback=cb2&r=%f&pt_local_tk=%s&pt_local_data=1"
                        % (port, uin, time.time(), self.tk),
                        "https://xui.ptlogin2.qq.com/")
                except Exception:
                    pass
                key = self._ck().get("clientkey", "")
                if key:
                    return key
                self._xlogin()
            raise RuntimeError("账号 %s 在端口 %d 没有拿到 clientkey" % (autosign.mask(uin), port))

        # jump #1: wuxia appid -> .qq.com 全域登录态
        self._get(
            "https://ssl.ptlogin2.qq.com/jump?clientuin=%s&clientkey=%s"
            "&u1=https%%3A%%2F%%2Fwuxia.qq.com%%2Fcp%%2Fa20230309_98549%%2Findex.html"
            "&appid=%s&daid=8&keyindex=19" % (uin, clientkey(), APPID_WUXIA),
            "https://xui.ptlogin2.qq.com/")
        # jump #2: game 域 appid -> p_skey/p_uin/pt4_token 长期正本
        self._get(
            "https://ssl.ptlogin2.qq.com/jump?clientuin=%s&clientkey=%s"
            "&u1=https%%3A%%2F%%2Fgame.qq.com%%2Fcomm-htdocs%%2Fmilo%%2Fproxy.html"
            "&appid=%s&daid=8&keyindex=19" % (uin, clientkey(), APPID_GAME),
            "https://xui.ptlogin2.qq.com/")

        jar = autosign.extract_cookies(
            {"name": c.name, "value": c.value, "domain": c.domain} for c in self.jar)
        if not jar.get("skey"):
            raise RuntimeError("jump 后没有 skey, 链路异常")
        jar["uin"] = jar.get("uin", "o" + uin)
        return jar


def update_roles(data):
    """扫描桥上所有在线账号, 给 roles.json 里匹配的角色签发新凭据(就地更新 data)。

    返回 (更新数, 匹配到的角色数)。
    """
    cli = SsoClient()
    accs = cli.accounts()
    if not accs:
        raise RuntimeError("桥上没有在线账号(QQ 客户端开了吗?)")
    updated = matched_n = 0
    for uin, info in accs.items():
        matched = [r for r in data.get("roles", []) if r.get("uin") == uin]
        if not matched:
            log("[sso] QQ%s(%s, 端口%d) 不在 roles.json, 跳过" % (autosign.mask(uin), info["nick"], info["port"]))
            continue
        matched_n += len(matched)
        jar = cli.mint(uin, info["port"])
        ok, desc = probe(jar, matched[0])
        if not ok:
            log("[sso] QQ%s 新签凭据探测失败(%s), 不写入" % (autosign.mask(uin), desc))
            continue
        changed = any(r.get("cookies", {}).get("skey") != jar.get("skey") for r in matched)
        for r in matched:
            r["cookies"] = dict(jar)
        if changed:
            updated += len(matched)
            log("[sso] QQ%s(%s) 已签发并更新 %d 个角色(%s, 正本=%s)"
                % (autosign.mask(uin), info["nick"], len(matched), desc,
                   "有" if jar.get("pt4_token") else "无"))
        else:
            log("[sso] QQ%s(%s) 凭据与当前一致(%s)" % (autosign.mask(uin), info["nick"], desc))
    return updated, matched_n


def main():
    ap = argparse.ArgumentParser(description="QQ 客户端 SSO 多账号凭据签发(含 pt4_token)")
    ap.add_argument("--sync", action="store_true", help="有更新则同步 GitHub Secret WUXIA_ROLES")
    ap.add_argument("--show", action="store_true", help="只列出桥上在线账号, 不写入")
    args = ap.parse_args()

    try:
        if args.show:
            accs = SsoClient().accounts()
            if not accs:
                log("桥上没有在线账号")
            for uin, info in accs.items():
                log("  QQ%s (%s) @ 端口%d" % (autosign.mask(uin), info["nick"], info["port"]))
            return
        if not ROLES_FILE.exists():
            log("[!] 未找到 roles.json, 请先运行 login.py")
            sys.exit(1)
        data = json.loads(ROLES_FILE.read_text("utf-8"))
        updated, matched = update_roles(data)
        if updated:
            ROLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            log("已回写 roles.json (%d 个角色更新)" % updated)
            if args.sync:
                sync_github()
        elif matched:
            log("匹配角色的凭据均无需更新")
        else:
            log("桥上账号都不在 roles.json")
            sys.exit(1)
    except RuntimeError as e:
        log("[!] %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
