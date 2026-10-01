#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetPulse v0.2.0 — 一键服务器网络体检（agent 友好）
用法:
  python3 netpulse.py                     本机体检（彩色终端输出）
  python3 netpulse.py --json              本机体检（JSON 输出，方便 agent/脚本解析）
  python3 netpulse.py --host root@1.2.3.4
      本地发起：经 SSH 在远端执行，结果取回本地展示
  python3 netpulse.py --host root@1.2.3.4 --json
      agent 标准用法：本地发起 + JSON 输出
  python3 netpulse.py --share             另存 Markdown 报告
只依赖 requests（缺失时自动尝试安装，远端无需手动干预）。
"""

import argparse
import concurrent.futures as futures
import json
import os
import subprocess
import sys
import time

VERSION = "0.2.0"

# ---------- 依赖自举（agent 友好：远端无需手动装依赖） ----------
try:
    import requests
except ImportError:
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "requests"],
                       check=True, timeout=180)
        import requests
    except Exception:
        sys.exit("缺少依赖 requests，且自动安装失败，请手动运行：pip install requests")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

NO_COLOR = False


class C:
    RST = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"


def p(text, color=""):
    if NO_COLOR or not sys.stdout.isatty() or not color:
        return text
    return f"{color}{text}{C.RST}"


def ok(t): return p("✅ " + t, C.GREEN)
def warn(t): return p("⚠️ " + t, C.YELLOW)
def bad(t): return p("❌ " + t, C.RED)
def info(t): return p("ℹ️ " + t, C.CYAN)


def banner():
    print(p(r"""
  _   _      _   ____        _          
 | \ | | ___| |_|  _ \ _   _| |___  ___ 
 |  \| |/ _ \ __| |_) | | | | / __|/ _ \
 | |\  |  __/ |_|  __/| |_| | \__ \  __/
 |_| \_|\___|\__|_|    \__,_|_|___/\___|
    """, C.CYAN))
    print(p(f"  NetPulse v{VERSION} · 一键服务器网络体检", C.DIM))
    print(p("  只检测目标机器的网络出口，不收集、不上传任何数据", C.DIM))
    print()


def section(title):
    print()
    print(p(f"━━ {title} ━━━━━━━━━━━━━━━━━━━━━━", C.BOLD + C.BLUE))


# ================= 数据采集（静默，只返回数据） =================

def fetch_ip_info():
    url = ("http://ip-api.com/json/?fields=status,message,country,countryCode,"
           "city,isp,org,as,query,hosting,proxy,timezone")
    try:
        d = requests.get(url, timeout=10).json()
    except Exception:
        return None
    return d if d.get("status") == "success" else None


def purity_verdict(d):
    if not d:
        return "未知"
    if d.get("proxy"):
        return "检测到代理特征"
    if d.get("hosting"):
        return "机房 IP（数据中心）"
    if d.get("hosting") is False:
        return "疑似家宽/原生 IP"
    return "未知"


TARGETS = [
    ("Netflix", "https://www.netflix.com/title/80018499"),
    ("Disney+", "https://www.disneyplus.com/"),
    ("YouTube Premium", "https://www.youtube.com/premium"),
    ("TikTok", "https://www.tiktok.com/"),
    ("ChatGPT", "https://chatgpt.com/"),
    ("Gemini", "https://gemini.google.com/"),
]


def probe(name_url):
    name, url = name_url
    try:
        r = requests.get(url, headers=UA, timeout=8, allow_redirects=True)
        return (name, r.status_code)
    except Exception:
        return (name, None)


def check_unlock_all():
    results = {}
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        for name, code in ex.map(probe, TARGETS):
            results[name] = code
    return results


SPEED_URLS = [
    ("Cloudflare", "https://speed.cloudflare.com/__down?bytes=20000000"),
    ("CacheFly", "http://cachefly.cachefly.net/10mb.test"),
]


def speed_one(name_url):
    name, url = name_url
    try:
        t0 = time.time()
        r = requests.get(url, timeout=25, stream=True)
        r.raise_for_status()
        n = 0
        for chunk in r.iter_content(65536):
            n += len(chunk)
            if time.time() - t0 > 20:
                break
        dt = time.time() - t0
        mbps = n * 8 / dt / 1e6 if dt > 0 else 0
        return (name, round(mbps, 1))
    except Exception:
        return (name, None)


def check_speed_all():
    results = {}
    with futures.ThreadPoolExecutor(max_workers=2) as ex:
        for name, mbps in ex.map(speed_one, SPEED_URLS):
            results[name] = mbps
    return results


def build_summary(data):
    tips = []
    purity, unlock = data["purity"], data["unlock"]
    if "代理" in purity:
        tips.append("检测到代理特征：当前走的是代理/VPN 出口，以上结果反映的是出口节点的情况。")
    elif "机房" in purity:
        tips.append("机房 IP：适合建站/做节点；注册风控严的平台账号时多加小心。")
    elif "家宽" in purity:
        tips.append("IP 比较干净：适合账号类、出海业务。")
    unlocked = sum(1 for v in unlock.values() if v == 200)
    if unlocked >= 4:
        tips.append("流媒体/AI 解锁良好，拿来看剧或跑 AI 没压力。")
    elif unlocked <= 1:
        tips.append("多数服务不可访问，检查下出口是否被墙，或换个干净 IP。")
    return tips


def collect(progress=False):
    def step(msg, fn):
        if progress:
            print(p(f"▸ {msg}…", C.DIM), flush=True)
        return fn()

    data = {
        "tool": "netpulse",
        "version": VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": "local",
    }
    data["ip"] = step("正在查询 IP 信息", fetch_ip_info)
    data["purity"] = purity_verdict(data["ip"])
    data["unlock"] = step("正在检测流媒体/AI 解锁", check_unlock_all)
    data["speed"] = step("正在测速", check_speed_all)
    data["summary"] = build_summary(data)
    return data


# ================= 远端执行（本地发起） =================

def run_remote(host):
    """经 SSH 把本脚本喂给远端 python3 执行，取回 JSON。"""
    here = os.path.realpath(__file__)
    try:
        with open(here, "rb") as f:
            script = f.read()
    except OSError as e:
        sys.exit(f"无法读取本脚本：{e}")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new",
           host, "python3 - --json --no-color"]
    try:
        pr = subprocess.run(cmd, input=script, capture_output=True, timeout=300)
    except FileNotFoundError:
        sys.exit("未找到 ssh 命令，无法使用 --host")
    except subprocess.TimeoutExpired:
        sys.exit(f"SSH 执行超时：{host}")
    if pr.returncode != 0:
        err = pr.stderr.decode(errors="replace").strip().splitlines()
        sys.exit(f"远端执行失败（{host}）：{err[-1] if err else 'exit ' + str(pr.returncode)}")
    try:
        data = json.loads(pr.stdout.decode())
    except Exception:
        sys.exit(f"远端返回的不是合法 JSON（{host}），可能是远端 python3 不可用")
    data["host"] = host
    return data


# ================= 输出渲染 =================

def render_terminal(data):
    banner()
    ip = data["ip"]

    section("1/4 · IP 信息")
    if ip:
        print(f"  IP      : {p(ip['query'], C.BOLD)}")
        print(f"  位置    : {ip.get('country', '?')} {ip.get('city', '?')} ({ip.get('timezone', '?')})")
        print(f"  ASN     : {ip.get('as', '?')}")
        print(f"  运营商  : {ip.get('isp', '?')}")
        print(f"  组织    : {ip.get('org', '?')}")
    else:
        print(bad("IP 查询失败"))

    section("2/4 · IP 纯净度")
    label = data["purity"]
    fn = warn if ("机房" in label or "代理" in label) else (ok if "家宽" in label else info)
    print("  " + fn(f"结论：{label}"))
    print(p("  这是什么意思？机房 IP 适合建站/做节点，但部分平台（社交/电商/AI）风控更严；", C.DIM))
    print(p("  家宽/原生 IP 更\"干净\"，适合账号类、出海业务。", C.DIM))

    section("3/4 · 流媒体 / AI 解锁检测（初步）")
    for name, code in data["unlock"].items():
        if code == 200:
            print("  " + ok(f"{name}: 可访问（HTTP 200）"))
        elif code in (403, 451):
            print("  " + warn(f"{name}: 疑似区域限制（HTTP {code}）"))
        elif code is None:
            print("  " + bad(f"{name}: 检测失败（超时或网络错误）"))
        else:
            print("  " + bad(f"{name}: 不可访问（HTTP {code}）"))
    print(p("  说明：状态码只是初步判断，Netflix 等建议用专项脚本二次确认。", C.DIM))

    section("4/4 · 速度测试")
    for name, mbps in data["speed"].items():
        if mbps:
            print(f"  {p(name + ':', C.BOLD)} {mbps} Mbps 下载")
        else:
            print("  " + bad(f"{name}: 测速失败"))

    section("体检总结")
    unlocked = sum(1 for v in data["unlock"].values() if v == 200)
    print("  " + p(f"• 目标：{data['host']}", C.BOLD))
    print("  " + p(f"• IP 类型：{data['purity']}", C.BOLD))
    print("  " + p(f"• 解锁情况：{unlocked}/{len(data['unlock'])} 个服务可访问", C.BOLD))
    sp = [v for v in data["speed"].values() if v]
    if sp:
        print("  " + p(f"• 下载速度：约 {max(sp)} Mbps（取最优）", C.BOLD))
    print()
    for t in data["summary"]:
        print("  " + info(t))


def render_share_md(data):
    ip = data["ip"]
    L = [f"# NetPulse 体检报告（v{VERSION}）", "",
         f"检测时间：{data['timestamp']}  ·  目标：{data['host']}", ""]
    if ip:
        L += ["## IP 信息",
              f"- IP：{ip['query']}",
              f"- 位置：{ip.get('country')} {ip.get('city')}",
              f"- ASN：{ip.get('as')}",
              f"- ISP：{ip.get('isp')}", ""]
    L += ["## 纯净度", f"- {data['purity']}", "", "## 解锁检测", ""]
    for name, code in data["unlock"].items():
        mark = "✅" if code == 200 else ("⚠️" if code in (403, 451) else "❌")
        L.append(f"- {mark} {name}（HTTP {code if code else '检测失败'}）")
    L += ["", "## 速度", ""]
    for name, mbps in data["speed"].items():
        L.append(f"- {name}：{str(mbps) + ' Mbps' if mbps else '测速失败'}")
    L += ["", "_由 NetPulse 生成 · 状态码为初步判断_"]
    return "\n".join(L)


def main():
    global NO_COLOR
    ap = argparse.ArgumentParser(description="NetPulse — 一键服务器网络体检（agent 友好）")
    ap.add_argument("--host", metavar="user@host",
                    help="本地发起：经 SSH 在远端执行，结果取回本地")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读的 JSON（agent/脚本友好）")
    ap.add_argument("--share", action="store_true", help="另存 Markdown 报告")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    a = ap.parse_args()
    NO_COLOR = a.no_color or a.json

    data = run_remote(a.host) if a.host else collect(progress=not a.json)

    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        render_terminal(data)

    if a.share:
        md = render_share_md(data)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in data["host"])
        fn = f"netpulse-report-{safe}-{time.strftime('%Y%m%d-%H%M%S')}.md"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(md)
        print()
        print(ok(f"报告已保存：{fn}"))


if __name__ == "__main__":
    main()
