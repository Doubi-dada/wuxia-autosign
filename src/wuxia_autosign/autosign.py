# -*- coding: utf-8 -*-
"""天刀「周周载愿」自动签到 (actId=625474)
支持: 多角色(roles.json) / 本地运行 / GitHub Actions 无人值守 / Server酱微信推送
"""
import json
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request
import urllib.parse

BASE_DIR = Path(__file__).parent
ROLES_FILE = BASE_DIR / "roles.json"
# 旧版单角色文件(首次运行自动迁移成 roles.json, 之后不再使用):
# 依次找 包目录 / src/ / 仓库根
LEGACY_DIRS = (BASE_DIR, BASE_DIR.parent, BASE_DIR.parent.parent)

ACT_ID = "625474"
SDID = "f049bee175806c1823da9aa01cedb2aa"
E_CODE = "536206"
BASE = "https://comm.ams.game.qq.com/ams/ame/amesvr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"

FLOW_INIT = "1021555"   # 初始化
FLOW_SIGN = "1020763"   # 每日许愿(签到)
FLOW_GET = "1020761"    # 周末一键领取

# 发给 AMS 的 cookie, 按此顺序拼接: uin/skey 是鉴权核心,
# p_uin/p_skey/pt4_token 是 game.qq.com 域的长期正本, 保留给后续自愈刷新用
COOKIE_KEEP = ("uin", "skey", "RK", "ptcz", "p_uin", "p_skey", "pt4_token")

DEFAULTS = {
    "gift_index": 3,
    "run_time": "09:05",
    "push_key": "",
}


def extract_cookies(cookie_list):
    """从 playwright state 的 cookies 数组里挑出需要的键值。

    milo 会把 skey/uin 同时写到 .qq.com 和当前域(值相同), 这里 .qq.com 域优先去重。
    """
    jar = {}
    for c in cookie_list or []:
        n, v, dom = c.get("name"), c.get("value", ""), c.get("domain", "")
        if n in COOKIE_KEEP and v:
            if n not in jar or dom == ".qq.com":
                jar[n] = v
    return jar


def cookie_header(jar):
    return "; ".join("%s=%s" % (n, jar[n]) for n in COOKIE_KEEP if n in jar)


def load_roles():
    """读取 roles.json; 不存在时尝试把旧版 state.json+config.json 迁移过来"""
    if ROLES_FILE.exists():
        try:
            data = json.loads(ROLES_FILE.read_text("utf-8"))
        except Exception as e:
            sys.exit("[!] roles.json 无法解析(%s), 请重新运行 login.py" % e)
        data.setdefault("defaults", {})
        data.setdefault("roles", [])
        return data
    for d in LEGACY_DIRS:
        if (d / "state.json").exists() and (d / "config.json").exists():
            return migrate_legacy(d)
    sys.exit("[!] 未找到 roles.json, 请先运行 python login.py 完成登录和配置")


def migrate_legacy(d):
    """把旧版单角色的 state.json + config.json 合并成 roles.json(旧文件保留不动)"""
    try:
        state = json.loads((d / "state.json").read_text("utf-8"))
        cfg = json.loads((d / "config.json").read_text("utf-8"))
    except Exception as e:
        sys.exit("[!] 旧版 state.json/config.json 无法解析(%s), 请重新运行 login.py" % e)
    jar = extract_cookies(state.get("cookies", []))
    uin = jar.get("uin", "").lstrip("o")
    if not jar.get("skey"):
        sys.exit("[!] 旧版 state.json 里没有 skey, 登录态无效, 请重新运行 login.py")
    data = {
        "defaults": {k: cfg.get(k, v) for k, v in DEFAULTS.items()},
        "roles": [{
            "name": cfg.get("name") or ("QQ%s" % (uin or "?")),
            "uin": uin,
            "area": cfg.get("area", ""),
            "roleid": cfg.get("roleid", ""),
            "cookies": jar,
        }],
    }
    try:
        ROLES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        print("[migrate] 已把旧版 state.json + config.json 合并为 roles.json")
    except OSError as e:
        print("[migrate] roles.json 写入失败(%s), 本次直接用内存数据" % e)
    return data


def apply_env(defaults):
    """允许用环境变量(或 GitHub Variables)覆盖默认值, 免去重新登录。

    返回被强制覆盖的键集合; gift_index 被覆盖时对所有角色生效。
    """
    forced = set()
    for env_key, key, is_int in (("WUXIA_GIFT_INDEX", "gift_index", True),
                                 ("WUXIA_RUN_TIME", "run_time", False),
                                 ("WUXIA_PUSH_KEY", "push_key", False)):
        v = os.environ.get(env_key, "").strip()
        if not v:
            continue
        if is_int:
            if not v.isdigit():
                continue
            v = int(v)
        defaults[key] = v
        forced.add(key)
    return forced


def calc_gtk(skey):
    """g_tk = DJB33X hash(skey)"""
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
        h &= 0xFFFFFFFF
    return h & 0x7FFFFFFF


def mask(s, head=3, tail=2):
    """脱敏: QQ号/角色ID 只留头尾, 防止 GitHub Actions 公开日志泄露账号"""
    s = str(s)
    return s if len(s) <= head + tail else s[:head] + "*" * (len(s) - head - tail) + s[-tail:]


def emit(cookie, gtk, flow_id, sarea, srole, extra=None):
    body = {
        "sServiceType": "wuxia", "iActivityId": ACT_ID,
        "sServiceDepartment": "group_9", "iFlowId": flow_id,
        "g_tk": gtk, "e_code": E_CODE, "g_code": "0",
        "sArea": sarea, "sRole": srole,
    }
    if extra:
        body.update(extra)
    url = BASE + "?sServiceType=wuxia&iActivityId=" + ACT_ID \
        + "&sServiceDepartment=group_9&sSDID=" + SDID
    req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode())
    req.add_header("Cookie", cookie)
    req.add_header("Referer", "https://wuxia.qq.com/")
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_init(resp):
    d = resp["modRet"]["jData"]["data"]
    return int(d["week"]), int(d["isTodaySign"]), d["giftNameArr"], int(d["toGetGiftCount"])


def is_login_expired(resp):
    """iRet=101 登录失效; 99998 未绑定大区"""
    ret = str(resp.get("flowRet", {}).get("iRet", resp.get("modRet", {}).get("iRet", "0")))
    return ret


def push_serverchan(key, title, desp=""):
    """Server酱推送 https://sct.ftqq.com"""
    if not key:
        return
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode()
    req = urllib.request.Request("https://sctapi.ftqq.com/%s.send" % key, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("[push]", r.read().decode("utf-8")[:100])
    except Exception as e:
        print("[push failed]", e)


def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


def run_role(role, defaults, interactive=False, gift_id=0, claim_only=False):
    """对单个角色执行签到主流程, 返回 (ok, 消息)

    gift_id: 直接指定奖励的 giftId, 0 表示按 gift_index/interactive 选择
    claim_only: 只领奖, 不做今天的许愿
    """
    name = role.get("name") or ("QQ%s" % role.get("uin", "?"))
    jar = role.get("cookies", {})
    skey = jar.get("skey", "")
    uin = role.get("uin") or jar.get("uin", "").lstrip("o")
    area, roleid = role.get("area", ""), role.get("roleid", "")
    if not skey:
        return False, "[%s] 缺少 skey 登录态, 请重新运行 login.py 并更新 Secrets 里的 WUXIA_ROLES" % name
    if not area or not roleid:
        return False, "[%s] 缺少 area/roleid, 请重新运行 login.py 绑定角色" % name

    cookie, gtk = cookie_header(jar), calc_gtk(skey)
    try:
        resp = emit(cookie, gtk, FLOW_INIT, area, roleid)
    except Exception as e:
        return False, "[%s] 接口异常: %s" % (name, e)
    iret = is_login_expired(resp)
    if iret == "101":
        return False, "[%s] 登录态已失效(iRet=101), 请在本地重新运行 login.py, 并更新 Secrets 里的 WUXIA_ROLES" % name
    if iret == "99998":
        return False, "[%s] 账号未绑定大区(iRet=99998), 请用浏览器打开活动中心登录一次完成绑定" % name

    gift_index = role.get("gift_index", defaults.get("gift_index", 3))
    week, signed, gifts, toget = parse_init(resp)
    day = "一二三四五六日"[week - 1]
    lines = ["[%s] QQ=%s 大区=%s 角色=%s" % (name, mask(uin), area, mask(roleid, 4, 4)),
             "奖励序号=%s 今日周%s 已许愿=%s 待领奖=%d"
             % (gift_index, day, bool(signed), toget)]

    # 周末: 一键领取本周奖励
    if week in (6, 7) and toget > 0:
        r = emit(cookie, gtk, FLOW_GET, area, roleid)
        lines.append("周末领取: %s" % r.get("flowRet", {}).get("sMsg", ""))

    if claim_only:
        return True, "\n".join(lines)

    if signed:
        lines.append("今日已许愿, 无需操作")
        return True, "\n".join(lines)

    if not gifts:
        return False, "\n".join(lines + ["没读到奖励列表, 请稍后重试"])

    if not gift_id:
        if interactive:
            for i, g in enumerate(gifts, 1):
                print("    %d. %s (giftId=%s)" % (i, g["name"], g["giftId"]))
            while True:
                s = input("请选择奖励序号 (直接回车=%s): " % gift_index).strip()
                if not s:
                    idx = int(gift_index)
                    break
                if s.isdigit() and 1 <= int(s) <= len(gifts):
                    idx = int(s)
                    break
                print("    输入无效, 请重新输入")
        else:
            idx = int(gift_index)
        idx = max(1, min(idx, len(gifts)))
        gift_id = gifts[idx - 1]["giftId"]

    r = emit(cookie, gtk, FLOW_SIGN, area, roleid, {"giftId": gift_id})
    iret2 = is_login_expired(r)
    pkg = r.get("modRet", {}).get("sPackageName", "")
    if iret2 == "0":
        lines.append("许愿成功: %s" % (pkg or "ok"))
        return True, "\n".join(lines)
    lines.append("许愿失败: %s" % r.get("flowRet", {}).get("sMsg", iret2))
    return False, "\n".join(lines)


def safe_run_role(role, defaults, **kw):
    """包装 run_role(), 把异常转成友好提示, 避免直接抛堆栈"""
    try:
        return run_role(role, defaults, **kw)
    except Exception as e:
        return False, "[%s] 运行异常: %s" % (role.get("name") or "?", e)


def select_roles(data, sel):
    """按 序号/名称/QQ号/roleid 挑选要跑的角色; 不选则全部"""
    roles = data.get("roles", [])
    if not sel:
        return roles
    if sel.isdigit():
        i = int(sel)
        if 1 <= i <= len(roles):
            return [roles[i - 1]]
    hits = [r for r in roles
            if sel in (r.get("name") or "") or sel in str(r.get("uin") or "")
            or sel == str(r.get("roleid") or "")]
    if hits:
        return hits
    print("[!] 没有匹配的角色: %s , 现有角色:" % sel)
    for i, r in enumerate(roles, 1):
        print("    %d. %s (QQ%s %s区)" % (i, r.get("name", "?"), r.get("uin", "?"), r.get("area", "?")))
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="天刀周周载愿自动签到(多角色)")
    ap.add_argument("--role", default="", help="只跑指定角色: 序号/名称/QQ号")
    ap.add_argument("--gift", type=int, default=0, help="指定 giftId")
    ap.add_argument("--index", type=int, default=0, help="按序号选奖励(1起)")
    ap.add_argument("--claim-only", action="store_true", help="仅领取")
    ap.add_argument("--cron", action="store_true", help="无人值守模式: 检查run_time时间窗+推送")
    args = ap.parse_args()

    data = load_roles()
    defaults = data.setdefault("defaults", {})
    forced = apply_env(defaults)
    if "gift_index" in forced:   # 环境变量强制的奖励序号对所有角色生效
        for r in data.get("roles", []):
            r["gift_index"] = defaults["gift_index"]
    if args.index:
        args.gift = 0

    # 无人值守模式: 到点后每小时检查, 未许愿则补签, 已许愿则秒退(服务器为准)
    if args.cron:
        now = bj_now()
        rt = defaults.get("run_time", "09:05")
        try:
            run_hour = int(rt.split(":")[0])
        except ValueError:
            run_hour = 9
        if now.hour < run_hour:
            print("[cron] 未到设定时间 %s, 当前 %s, 退出" % (rt, now.strftime("%H:%M")))
            return
        late = now.hour > run_hour
        results = [safe_run_role(r, defaults, claim_only=args.claim_only,
                                 gift_id=args.gift if args.gift else 0)
                   for r in data.get("roles", [])]
        if not results:
            print("[cron] roles.json 里没有角色, 退出")
            return
        for _, m in results:
            print(m)
        ok_n = sum(1 for ok, _ in results if ok)
        if all(ok and "今日已许愿" in m for ok, m in results):
            # 所有角色今天都已签过(可能是准点那次跑成功了), 不再推送
            print("[cron] 所有角色今日已完成, 无需重复")
            return
        title = "[天刀签到] %d/%d 成功" % (ok_n, len(results))
        if late:
            title += "(补签)" if ok_n == len(results) else "(延迟重试)"
        push_serverchan(defaults.get("push_key", ""), title,
                        "\n\n---\n\n".join(m for _, m in results))
        return

    roles = select_roles(data, args.role)
    if args.index:
        for r in roles:
            r["gift_index"] = args.index
    interactive = not (args.index or args.gift or args.claim_only) and len(roles) == 1
    for r in roles:
        ok, msg = safe_run_role(r, defaults, interactive=interactive,
                                gift_id=args.gift, claim_only=args.claim_only)
        print(msg)
        print()


if __name__ == "__main__":
    main()
