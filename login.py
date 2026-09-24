# -*- coding: utf-8 -*-
"""本地登录助手: 生成 GitHub Secrets 需要的两段内容

用法:
    python login.py           # 交互式选择登录方式(默认是网页登录, 推荐)
    python login.py --headed  # 网页登录(默认, 首次登录必须用这个)
    python login.py --qr      # 扫码登录(仅限之前已经成功登录过的情况)

依赖: playwright-cli
    npm install -g @playwright/cli
"""
import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
URL = "https://wuxia.qq.com/cp/a20230309_98549/index.html"
STATE_FILE = BASE / "state.json"
CFG_FILE = BASE / "config.json"
SECRET_FILES = (("WUXIA_STATE", STATE_FILE), ("WUXIA_CONFIG", CFG_FILE))

# 登录成功后, 活动 iframe 的地址里会带上 area / roleid
EXPR = "(function(){var f=document.querySelector('iframe');return f?f.src:'';})()"


def cli(*args, timeout=90):
    """调用 playwright-cli, 统一按 UTF-8 解码输出"""
    try:
        r = subprocess.run(["playwright-cli"] + list(args),
                           capture_output=True, timeout=timeout, shell=True)
    except subprocess.TimeoutExpired:
        return ""

    def dec(b):
        return b.decode("utf-8", errors="replace") if b else ""

    return dec(r.stdout) + dec(r.stderr)


def ensure_playwright():
    if shutil.which("playwright-cli") is None:
        sys.exit(
            "\n[!] 没有检测到 playwright-cli\n"
            "    请先安装 Node.js (https://nodejs.org)，然后执行:\n"
            "        npm install -g @playwright/cli\n"
            "        playwright-cli install\n"
            "    装好后重新运行 python login.py\n"
        )


def clean_old_files():
    """清掉上一轮残留的二维码和 Secrets 文本"""
    for pat in ("qr_login*.png", "WUXIA_*.txt"):
        for p in BASE.glob(pat):
            try:
                p.unlink()
            except OSError:
                pass


def choose_mode(args):
    if args.qr:
        return "qr"
    if args.headed:
        return "headed"
    print("请选择登录方式:")
    print("  1) 网页登录【推荐, 首次登录必须选这个】: 弹出浏览器窗口, 自己扫码或点头像登录")
    print("  2) 扫码登录: 程序生成二维码图片(已经成功登录过才用这个)")
    print("")
    print("  注意: 首次登录不要选扫码登录! 因为首次登录后页面上还要手动选一次大区,")
    print("        二维码模式下脚本检测不到这一步, 会卡住。先用网页登录跑通一次以后再考虑。")
    while True:
        try:
            c = input("输入 1 或 2 后回车 (直接回车=1): ").strip() or "1"
        except EOFError:      # 没有交互终端时直接用默认方式
            c = "1"
        if c == "1":
            return "headed"
        if c == "2":
            return "qr"
        print("    输入无效, 请重新输入")


def read_role():
    """从页面读取 area / roleid, 未登录则返回空"""
    out = cli("eval", EXPR, timeout=30).replace("\\u0026", "&")
    m_area = re.search(r"area=([^&\"'\\\s]+)", out)
    m_role = re.search(r"roleid=([^&\"'\\\s]+)", out)
    if m_area and m_role:
        return m_area.group(1), m_role.group(1)
    return "", ""


def wait_login(mode, timeout):
    """轮询等待登录成功, 返回 (area, roleid); 超时返回 (None, None)"""
    deadline = time.time() + timeout
    last_qr = 0
    waited = 0
    while time.time() < deadline:
        if mode == "qr" and time.time() - last_qr > 45:
            qr = BASE / ("qr_login_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
            cli("screenshot", "--filename=" + str(qr))
            last_qr = time.time()
            print("\n>>> 请用手机 QQ 扫描这个二维码:\n    %s\n" % qr)
            print("    (二维码约 2 分钟换一张, 请以最新提示的文件为准; 扫完记得在手机上点『确认登录』)")

        area, roleid = read_role()
        if roleid:
            return area, roleid

        time.sleep(5)
        waited += 5
        if waited % 30 == 0:
            print("    等待登录中... 已等待 %d 秒" % waited)
            if mode == "headed":
                print("    (如果页面停在让选大区/选角色, 请在窗口里手动选一下, 选完页面会自己刷新)")
    return None, None


def save_state():
    """保存登录态并校验, 返回 uin"""
    cli("state-save", str(STATE_FILE))
    if not STATE_FILE.exists():
        sys.exit("[!] 登录态保存失败, 没有生成 state.json, 请重新运行 python login.py")
    try:
        ck = {c["name"]: c["value"] for c in json.loads(STATE_FILE.read_text("utf-8")).get("cookies", [])}
    except Exception as e:
        sys.exit("[!] state.json 损坏(%s), 请重新运行 python login.py" % e)
    if not ck.get("skey"):
        sys.exit("[!] state.json 里没有 skey, 说明没有真正登录成功, 请重新运行 python login.py")
    return ck.get("uin", "").lstrip("o")


def write_config(area, roleid):
    """生成 config.json, 奖励和时间用默认值(可在 GitHub Variables 里改)"""
    cfg = {}
    if CFG_FILE.exists():
        try:
            cfg = json.loads(CFG_FILE.read_text("utf-8"))
        except Exception:
            cfg = {}
    cfg["area"] = area
    cfg["roleid"] = roleid
    cfg.setdefault("gift_index", 3)
    cfg.setdefault("run_time", "09:05")
    cfg.setdefault("push_key", "")
    CFG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    return cfg


def fetch_gifts(area, roleid):
    """登录后拉取当前活动的奖励列表, 拿到序号->名称对照"""
    try:
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        import autosign
        cookie, skey, _ = autosign.load_cookies()
        resp = autosign.emit(cookie, autosign.calc_gtk(skey), autosign.FLOW_INIT, area, roleid)
        return autosign.parse_init(resp)[2]
    except Exception:
        return []


def mask(s, head=3, tail=2):
    s = str(s)
    return s if len(s) <= head + tail else s[:head] + "*" * (len(s) - head - tail) + s[-tail:]


def b64(path):
    return base64.b64encode(path.read_bytes()).decode()


def emit_secret_files():
    """把两段 Secrets 内容写成文件并打印, 方便直接复制"""
    out = {}
    for name, src in SECRET_FILES:
        p = BASE / (name + ".txt")
        content = b64(src)
        p.write_text(content, "utf-8")   # 单文件单行内容, 不含换行
        out[name] = p
    return out


def print_result(files, cfg, uin, area, gifts):
    gift_idx = cfg.get("gift_index", 3)
    gift_name = ""
    if gifts and 1 <= gift_idx <= len(gifts):
        gift_name = gifts[gift_idx - 1]["name"]

    print("\n" + "=" * 66)
    print("[登录信息已生成] 接下来照着做 3 步就全部搞定")
    print("=" * 66)
    print("账号 QQ: %s   大区: %s   角色: %s" % (mask(uin), area, mask(cfg["roleid"], 4, 4)))
    print("")
    print("已生成两个文件, 用记事本打开后 Ctrl+A / Ctrl+C 全选复制即可:")
    for name in ("WUXIA_STATE", "WUXIA_CONFIG"):
        print("    %-14s -> %s" % (name, files[name]))
    print("")
    print("或者在下面直接照抄(每段都是一整行, 不要漏字符):\n")
    for name in ("WUXIA_STATE", "WUXIA_CONFIG"):
        print("----- %s -----" % name)
        print(b64(dict(SECRET_FILES)[name]))
        print("----- %s 结束 -----\n" % name)

    print("=" * 66)
    print("第 1 步 (必做): 把上面两段内容填到 GitHub")
    print("=" * 66)
    print("仓库页面 -> Settings -> Secrets and variables -> Actions")
    print("         -> New repository secret")
    print("")
    print("    Name 填 WUXIA_STATE  , Secret 框粘贴第 1 段")
    print("    Name 填 WUXIA_CONFIG , Secret 框粘贴第 2 段")
    print("")

    print("=" * 66)
    print("第 2 步 (想换奖励才做): 设置每天许愿哪个奖励")
    print("=" * 66)
    print("同一个页面 -> 点上面的 Variables 标签页 -> New repository variable")
    print("")
    print("    Name 填 WUXIA_GIFT_INDEX")
    print("    Value 填下面挑一个数字:\n")
    if gifts:
        for i, g in enumerate(gifts, 1):
            mark = "  <-- 当前默认" if i == gift_idx else ""
            print("        %d = %s%s" % (i, g["name"], mark))
    else:
        for i in range(1, 9):
            print("        %d%s" % (i, "  <-- 当前默认" if i == gift_idx else ""))
        print("        (没读到今天的奖励名称, 序号以游戏活动页从上往下数为准)")
    print("\n    当前默认: 第 %d 个%s。跳过第 2 步就一直用它。"
          % (gift_idx, ("（%s）" % gift_name) if gift_name else ""))
    print("")

    print("=" * 66)
    print("第 3 步 (想改时间才做): 设置每天几点自动签到")
    print("=" * 66)
    print("还是那个页面 -> New repository variable")
    print("")
    print("    Name 填 WUXIA_RUN_TIME")
    print("    Value 填 24 小时制的北京时间, 例如 09:05 / 12:00 / 22:30")
    print("")
    print("    当前默认: %s。跳过第 3 步就一直用它。" % cfg.get("run_time", "09:05"))
    print("    说明: 只看小时, 分钟写什么都一样; 实际执行会在该小时的 05 分左右,")
    print("          遇到 GitHub 排队时可能晚十几分钟, 属正常。")
    print("")
    print("    顺带可以在这里加 WUXIA_PUSH_KEY = Server酱的 SendKey (<https://sct.ftqq.com>),")
    print("    填了以后签到成功/失败会推到微信; 不填就不推送。")
    print("")

    print("=" * 66)
    print("最后: 到 Actions 页面手动 Run workflow 跑一次, 看到『许愿成功』就全部搞定。")
    print("=" * 66)
    print("[!] 上面两段内容和 state.json / config.json / 两个 .txt 都是你的私人登录凭证,")
    print("    不要发给任何人, 也不要上传仓库(已被 .gitignore 自动忽略)。")


def main():
    ap = argparse.ArgumentParser(description="天刀周周载愿 - 本地登录助手")
    ap.add_argument("--qr", action="store_true", help="扫码登录(仅限已成功登录过的情况)")
    ap.add_argument("--headed", action="store_true", help="网页登录(默认, 推荐)")
    ap.add_argument("--timeout", type=int, default=300, help="等待登录的秒数(默认300)")
    args = ap.parse_args()

    ensure_playwright()
    clean_old_files()
    mode = choose_mode(args)

    print("\n[1/3] 正在打开登录页面...")
    cli("close")
    if mode == "headed":
        cli("open", "--headed", URL)
        print("      浏览器窗口已弹出, 请在窗口里完成登录。")
        print("      首次登录的话, 页面上可能让你选大区, 跟着选一下即可。")
        print("      登录成功后不用做别的操作, 本程序会自动检测。")
    else:
        cli("open", URL)
        print("      页面已在后台打开, 马上生成二维码给你扫。")

    time.sleep(4)
    area, roleid = wait_login(mode, args.timeout)
    if not roleid:
        cli("close")
        sys.exit("\n[!] 等待超时, 没有检测到登录。\n"
                 "    建议改用默认方式重来: python login.py  (选 1 网页登录)\n"
                 "    如果是扫码方式, 记得扫完要在手机上点『确认登录』。")

    print("\n[2/3] 登录成功, 正在保存登录态...")
    uin = save_state()
    cfg = write_config(area, roleid)
    gifts = fetch_gifts(area, roleid)

    print("\n[3/3] 正在生成 GitHub 需要的内容...")
    files = emit_secret_files()
    cli("close")
    print_result(files, cfg, uin, area, gifts)


if __name__ == "__main__":
    main()
