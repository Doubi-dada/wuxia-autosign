# -*- coding: utf-8 -*-
"""天刀「周周载愿」自动签到 (actId=625474)
支持: 本地运行 / GitHub Actions 无人值守 / Server酱微信推送
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
STATE_FILE = BASE_DIR / "state.json"
CFG_FILE = BASE_DIR / "config.json"
ACT_ID = "625474"
SDID = "f049bee175806c1823da9aa01cedb2aa"
E_CODE = "536206"
BASE = "https://comm.ams.game.qq.com/ams/ame/amesvr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"

FLOW_INIT = "1021555"   # 初始化
FLOW_SIGN = "1020763"   # 每日许愿(签到)
FLOW_GET = "1020761"    # 周末一键领取

DEFAULT_CFG = {
    "area": "",
    "roleid": "",
    "gift_index": 3,
    "run_time": "09:05",
    "push_key": "",
}


def load_cfg():
    if not CFG_FILE.exists():
        sys.exit("[!] 未找到 config.json, 请先运行 python login.py 完成登录和配置")
    cfg = dict(DEFAULT_CFG)
    try:
        cfg.update(json.loads(CFG_FILE.read_text("utf-8")))
    except Exception as e:
        sys.exit("[!] config.json 无法解析(%s), 请重新运行 python login.py" % e)
    apply_env(cfg)
    return cfg


def apply_env(cfg):
    """允许用环境变量(或 GitHub Variables)覆盖奖励序号/时间等, 免去重新登录"""
    overrides = {
        "WUXIA_AREA": "area",
        "WUXIA_ROLEID": "roleid",
        "WUXIA_GIFT_INDEX": "gift_index",
        "WUXIA_RUN_TIME": "run_time",
        "WUXIA_PUSH_KEY": "push_key",
    }
    for env_key, cfg_key in overrides.items():
        v = os.environ.get(env_key, "").strip()
        if not v:
            continue
        if cfg_key == "gift_index":
            if not v.isdigit():
                continue
            v = int(v)
        cfg[cfg_key] = v


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


def load_cookies():
    if not STATE_FILE.exists():
        raise RuntimeError("未找到 state.json, 请先在本地运行 python login.py 完成登录")
    try:
        state = json.loads(STATE_FILE.read_text("utf-8"))
    except Exception as e:
        raise RuntimeError("state.json 无法解析(%s), 请重新运行 python login.py" % e)
    keep = ("skey", "uin", "RK", "ptcz", "qlogin_uid")
    parts, skey, uin = [], "", ""
    for c in state.get("cookies", []):
        if c["name"] in keep:
            parts.append(c["name"] + "=" + c["value"])
        if c["name"] == "skey":
            skey = c["value"]
        if c["name"] == "uin":
            uin = c["value"].lstrip("o")
    if not skey:
        raise RuntimeError("state.json 里没有 skey, 登录态无效, 请重新运行 python login.py")
    return "; ".join(parts), skey, uin


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


def run(cfg, quiet=False, interactive=False, gift_id=0, claim_only=False):
    """执行签到主流程, 返回 (ok, 消息)

    gift_id: 直接指定奖励的 giftId, 0 表示按 gift_index/interactive 选择
    claim_only: 只领奖, 不做今天的许愿
    """
    cookie, skey, uin = load_cookies()
    gtk = calc_gtk(skey)
    area, role = cfg.get("area", ""), cfg.get("roleid", "")
    if not area or not role:
        return False, "config.json 缺少 area/roleid, 请先运行 login.py 绑定角色"

    try:
        resp = emit(cookie, gtk, FLOW_INIT, area, role)
    except Exception as e:
        return False, "接口异常: %s" % e
    iret = is_login_expired(resp)
    if iret == "101":
        return False, "登录态已失效(iRet=101), 请在本地重新运行 login.py 重新登录, 并更新 Secrets 里的 WUXIA_STATE"
    if iret == "99998":
        return False, "账号未绑定大区(iRet=99998), 请用浏览器打开活动中心登录一次完成绑定"

    week, signed, gifts, toget = parse_init(resp)
    day = "一二三四五六日"[week - 1]
    lines = ["账号QQ=%s 大区=%s 角色=%s" % (mask(uin), area, mask(role, 4, 4)),
             "配置: 奖励序号=%s 执行时间=%s" % (cfg.get("gift_index", 3), cfg.get("run_time", "09:05")),
             "今日周%s 已许愿=%s 待领奖=%d" % (day, bool(signed), toget)]

    # 周末: 一键领取本周奖励
    if week in (6, 7) and toget > 0:
        r = emit(cookie, gtk, FLOW_GET, area, role)
        lines.append("周末领取: %s" % r.get("flowRet", {}).get("sMsg", ""))

    if claim_only:
        return True, "\n".join(lines)

    if signed:
        lines.append("今日已许愿, 无需操作")
        return True, "\n".join(lines)

    if not gift_id:
        if interactive:
            for i, g in enumerate(gifts, 1):
                print("    %d. %s (giftId=%s)" % (i, g["name"], g["giftId"]))
            while True:
                s = input("请选择奖励序号 (直接回车=%s): " % cfg.get("gift_index", 3)).strip()
                if not s:
                    idx = int(cfg.get("gift_index", 3))
                    break
                if s.isdigit() and 1 <= int(s) <= len(gifts):
                    idx = int(s)
                    break
                print("    输入无效, 请重新输入")
        else:
            idx = int(cfg.get("gift_index", 3))
        idx = max(1, min(idx, len(gifts)))
        gift_id = gifts[idx - 1]["giftId"]

    r = emit(cookie, gtk, FLOW_SIGN, area, role, {"giftId": gift_id})
    iret2 = is_login_expired(r)
    name = r.get("modRet", {}).get("sPackageName", "")
    if iret2 == "0":
        lines.append("许愿成功: %s" % (name or "ok"))
        return True, "\n".join(lines)
    lines.append("许愿失败: %s" % r.get("flowRet", {}).get("sMsg", iret2))
    return False, "\n".join(lines)


def safe_run(cfg, **kw):
    """包装 run(), 把异常转成友好提示, 避免直接抛堆栈"""
    try:
        return run(cfg, **kw)
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:
        return False, "运行异常: %s" % e


def main():
    ap = argparse.ArgumentParser(description="天刀周周载愿自动签到")
    ap.add_argument("--gift", type=int, default=0, help="指定 giftId")
    ap.add_argument("--index", type=int, default=0, help="按序号选奖励(1起)")
    ap.add_argument("--claim-only", action="store_true", help="仅领取")
    ap.add_argument("--cron", action="store_true", help="无人值守模式: 检查run_time时间窗+推送")
    args = ap.parse_args()

    cfg = load_cfg()
    if args.index:
        cfg["gift_index"] = args.index

    # 无人值守模式: 到点后每小时检查, 未许愿则补签, 已许愿则秒退(服务器为准)
    if args.cron:
        now = bj_now()
        rt = cfg.get("run_time", "09:05")
        try:
            run_hour = int(rt.split(":")[0])
        except ValueError:
            run_hour = 9
        if now.hour < run_hour:
            print("[cron] 未到设定时间 %s, 当前 %s, 退出" % (rt, now.strftime("%H:%M")))
            return
        late = now.hour > run_hour
        ok, msg = safe_run(cfg, claim_only=args.claim_only)
        print(msg)
        if "今日已许愿" in msg:
            # 今天已签过(可能是准点那次跑成功了), 不再推送
            print("[cron] 今日已完成, 无需重复")
            return
        title = "[天刀签到] 成功" if ok else "[天刀签到] 失败"
        if late:
            title += "(补签)" if ok else "(延迟重试)"
        push_serverchan(cfg.get("push_key", ""), title, msg)
        return

    interactive = not (args.index or args.gift or args.claim_only)
    ok, msg = safe_run(cfg, interactive=interactive,
                       gift_id=args.gift, claim_only=args.claim_only)
    print(msg)


if __name__ == "__main__":
    main()
