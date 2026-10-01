#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetPulse v0.3.0 — 为 AI agent 而生的 VPS 巡检工具
用法:
  python3 netpulse.py                                  本机体检
  python3 netpulse.py --host root@a --host root@b      本地发起：批量巡检多台机器
  python3 netpulse.py --host root@a --json | jq .       agent 标准用法
  python3 netpulse.py --share                          另存 Markdown 报告
只依赖 requests（缺失时自动尝试安装，远端无需手动干预）。
"""

import argparse
import concurrent.futures as futures
import json
import os
import re
import socket
import subprocess
import sys
import time

VERSION = "0.3.0"

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


def ok(t): return p("✓ " + t, C.GREEN)
def warn(t): return p("! " + t, C.YELLOW)
def bad(t): return p("✗ " + t, C.RED)
def info(t): return p("→ " + t, C.CYAN)


def dw(s):
    """显示宽度（CJK/符号按 2 宽计），用于表格对齐。"""
    w = 0
    for ch in str(s):
        w += 2 if ('\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f'
                   or '\uff00' <= ch <= '\uffef' or ord(ch) > 0x2500) else 1
    return w


def pad(s, width):
    s = str(s)
    return s + " " * max(0, width - dw(s))


def banner():
    print()
    print(p("  NetPulse", C.BOLD + C.CYAN) + p(f"  v{VERSION}", C.DIM))
    print(p("  为 AI agent 而生的 VPS 巡检工具", C.DIM))
    print(p("  " + "─" * 44, C.DIM))


def section(title):
    print()
    print(p(f"  ━━ {title} ", C.BOLD + C.BLUE) + p("━" * 30, C.DIM))


def kv(label, value, color=C.BOLD):
    print(f"    {p(pad(label, 10), C.DIM)}{p(str(value), color)}")


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


def check_dns_leak(ip_country):
    """DNS 泄露检测：解析出口的属地是否与 IP 属地一致。"""
    try:
        d = requests.get("http://edns.ip-api.com/json", timeout=10).json()
        dns = d.get("dns", {})
        rip, rgeo = dns.get("ip"), dns.get("geo", "")
    except Exception:
        return {"ok": False}
    leak = bool(ip_country and rgeo and not rgeo.startswith(ip_country))
    return {"ok": True, "resolver_ip": rip, "resolver_geo": rgeo, "leak": leak}


TARGETS = [
    ("Netflix", "https://www.netflix.com/title/80018499"),
    ("Disney+", "https://www.disneyplus.com/"),
    ("YouTube Premium", "https://www.youtube.com/premium"),
    ("HBO Max", "https://www.max.com/"),
    ("Hulu", "https://www.hulu.com/"),
    ("Prime Video", "https://www.primevideo.com/"),
    ("TikTok", "https://www.tiktok.com/"),
    ("Spotify", "https://open.spotify.com/"),
    ("ChatGPT", "https://chatgpt.com/"),
    ("Claude", "https://claude.ai/"),
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
    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        for name, code in ex.map(probe, TARGETS):
            results[name] = code
    return results


def check_ipv6():
    try:
        ip = requests.get("https://api64.ipify.org?format=json", timeout=10).json().get("ip", "")
        return ip if ":" in ip else None
    except Exception:
        return None


LAT_TARGETS = [
    ("Cloudflare", "1.1.1.1", 443),
    ("Google", "8.8.8.8", 443),
    ("百度", "www.baidu.com", 443),
]


def tcp_ping(t):
    label, host, port = t
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=5)
        s.close()
        return (label, round((time.time() - t0) * 1000))
    except Exception:
        return (label, None)


def check_latency_all():
    results = {}
    with futures.ThreadPoolExecutor(max_workers=3) as ex:
        for label, ms in ex.map(tcp_ping, LAT_TARGETS):
            results[label] = ms
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
    purity = data["purity"]
    if "代理" in purity:
        tips.append("检测到代理特征：当前走的是代理/VPN 出口，结果反映的是出口节点的情况。")
    elif "机房" in purity:
        tips.append("机房 IP：适合建站/做节点；注册风控严的平台账号时多加小心。")
    elif "家宽" in purity:
        tips.append("IP 比较干净：适合账号类、出海业务。")
    dns = data.get("dns") or {}
    if dns.get("ok") and dns.get("leak"):
        tips.append(f"DNS 有泄露：解析出口属地（{dns.get('resolver_geo')}）与 IP 属地不一致。")
    if not data.get("ipv6"):
        tips.append("无 IPv6 出口，部分场景（如 App Store 审核、IPv6-only 网络）会受限。")
    unlocked = sum(1 for v in data["unlock"].values() if v == 200)
    if unlocked >= 7:
        tips.append("流媒体/AI 解锁良好，拿来看剧或跑 AI 没压力。")
    elif unlocked <= 2:
        tips.append("多数服务不可访问，检查下出口是否被墙，或换个干净 IP。")
    return tips


def collect(progress=False):
    def step(msg, fn):
        if progress:
            print(p(f"  ▸ {msg}…", C.DIM), flush=True)
        return fn()

    data = {
        "tool": "netpulse",
        "version": VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": "local",
    }
    data["ip"] = step("正在查询 IP 信息", fetch_ip_info)
    data["purity"] = purity_verdict(data["ip"])
    country = (data["ip"] or {}).get("country")
    data["dns"] = step("正在检测 DNS 泄露", lambda: check_dns_leak(country))
    data["unlock"] = step("正在检测流媒体/AI 解锁", check_unlock_all)
    data["ipv6"] = step("正在检测 IPv6", check_ipv6)
    data["latency"] = step("正在测试延迟", check_latency_all)
    data["speed"] = step("正在测速", check_speed_all)
    data["summary"] = build_summary(data)
    return data


# ================= 远端执行（本地发起） =================

def run_remote(host):
    """经 SSH 把本脚本喂给远端 python3 执行，取回 JSON。失败时返回带 error 的 dict。"""
    try:
        with open(os.path.realpath(__file__), "rb") as f:
            script = f.read()
    except OSError as e:
        return {"tool": "netpulse", "version": VERSION, "host": host,
                "error": f"无法读取本脚本：{e}"}
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new",
           host, "python3 - --json --no-color"]
    try:
        pr = subprocess.run(cmd, input=script, capture_output=True, timeout=300)
    except FileNotFoundError:
        return {"tool": "netpulse", "version": VERSION, "host": host,
                "error": "未找到 ssh 命令，无法使用 --host"}
    except subprocess.TimeoutExpired:
        return {"tool": "netpulse", "version": VERSION, "host": host,
                "error": f"SSH 执行超时：{host}"}
    if pr.returncode != 0:
        err = pr.stderr.decode(errors="replace").strip().splitlines()
        return {"tool": "netpulse", "version": VERSION, "host": host,
                "error": f"远端执行失败：{err[-1] if err else 'exit ' + str(pr.returncode)}"}
    try:
        data = json.loads(pr.stdout.decode())
    except Exception:
        return {"tool": "netpulse", "version": VERSION, "host": host,
                "error": "远端返回的不是合法 JSON，可能是远端 python3 不可用"}
    data["host"] = host
    return data


# ================= 输出渲染 =================

def ms_color(ms):
    if ms is None:
        return C.DIM
    return C.GREEN if ms < 150 else (C.YELLOW if ms < 300 else C.RED)


def render_terminal(data):
    banner()
    ip = data["ip"] or {}

    section(f"1/5 · IP 信息  [{data['host']}]")
    if data["ip"]:
        kv("IP", ip.get("query", "-"))
        kv("位置", f"{ip.get('country', '?')} · {ip.get('city', '?')} ({ip.get('timezone', '?')})")
        kv("ASN", ip.get("as", "?"))
        kv("运营商", ip.get("isp", "?"))
    else:
        print("    " + bad("IP 查询失败"))

    section("2/5 · IP 纯净度")
    label = data["purity"]
    fn = warn if ("机房" in label or "代理" in label) else (ok if "家宽" in label else info)
    print("    " + fn(label))
    print(p("    机房 IP 适合建站/做节点，但部分平台风控更严；家宽/原生 IP 更\"干净\"，适合账号类业务。", C.DIM))

    section("3/5 · DNS 泄露检测")
    dns = data.get("dns") or {}
    if not dns.get("ok"):
        print("    " + bad("检测失败"))
    elif dns.get("leak"):
        print("    " + warn(f"存在泄露：解析出口 {dns.get('resolver_ip')}（{dns.get('resolver_geo')}）"))
    else:
        print("    " + ok(f"无泄露：解析出口 {dns.get('resolver_ip')}（{dns.get('resolver_geo')}）"))

    section(f"4/5 · 流媒体 / AI 解锁检测（{sum(1 for v in data['unlock'].values() if v == 200)}/{len(data['unlock'])}）")
    for name, code in data["unlock"].items():
        if code == 200:
            print("    " + ok(pad(name, 16) + "可访问"))
        elif code in (403, 451):
            print("    " + warn(pad(name, 16) + f"疑似区域限制（{code}）"))
        elif code is None:
            print("    " + bad(pad(name, 16) + "检测失败"))
        else:
            print("    " + bad(pad(name, 16) + f"不可访问（{code}）"))
    print(p("    状态码为初步判断，Netflix 等建议用专项脚本二次确认。", C.DIM))

    section("5/5 · 网络质量")
    v6 = data.get("ipv6")
    print("    " + (ok(f"IPv6 出口：{v6}") if v6 else info("IPv6 出口：无")))
    lat = data.get("latency") or {}
    for label, ms in lat.items():
        print(f"    {p(pad('延迟 ' + label, 16), C.DIM)}{p(str(ms) + ' ms' if ms else '失败', ms_color(ms))}")
    for name, mbps in (data.get("speed") or {}).items():
        if mbps:
            print(f"    {p(pad('下载 ' + name, 16), C.DIM)}{p(str(mbps) + ' Mbps', C.BOLD)}")
        else:
            print("    " + bad(f"下载 {name}：测速失败"))

    section("体检总结")
    for t in data["summary"]:
        print("    " + info(t))
    print()


def short_purity(label):
    if "机房" in label:
        return "机房IP"
    if "家宽" in label:
        return "家宽IP"
    if "代理" in label:
        return "代理"
    return "未知"


def render_compare(datas):
    datas = [d for d in datas if not d.get("error")]
    if len(datas) < 2:
        return
    section(f"多机对比（{len(datas)} 台）")
    cols = ["主机", "出口 IP", "纯净度", "解锁", "延迟", "下载"]
    rows = []
    for d in datas:
        unl = sum(1 for v in d["unlock"].values() if v == 200)
        lats = [v for v in (d.get("latency") or {}).values() if v]
        spds = [v for v in (d.get("speed") or {}).values() if v]
        rows.append([
            d["host"],
            (d.get("ip") or {}).get("query", "-"),
            short_purity(d["purity"]),
            f"{unl}/{len(d['unlock'])}",
            f"{min(lats)}ms" if lats else "-",
            f"{max(spds)}Mbps" if spds else "-",
        ])
    widths = [max(dw(r[i]) for r in [cols] + rows) for i in range(len(cols))]
    print("    " + "  ".join(p(pad(c, widths[i]), C.DIM) for i, c in enumerate(cols)))
    print("    " + p("  ".join("─" * widths[i] for i in range(len(cols))), C.DIM))
    for r in rows:
        print("    " + "  ".join(pad(c, widths[i]) for i, c in enumerate(r)))
    print()


def render_share_md(datas):
    if len(datas) == 1:
        d = datas[0]
        title = f"# NetPulse 体检报告（v{VERSION}）"
    else:
        title = f"# NetPulse 批量巡检报告（v{VERSION}）"
    L = [title, ""]
    for d in datas:
        if d.get("error"):
            L += [f"## {d['host']}", f"- ✗ {d['error']}", ""]
            continue
        ip = d.get("ip") or {}
        L += [f"## {d['host']}（{d.get('timestamp', '')}）",
              f"- 出口 IP：{ip.get('query', '-')}（{ip.get('country', '?')} {ip.get('city', '?')}）",
              f"- ASN：{ip.get('as', '?')} / {ip.get('isp', '?')}",
              f"- 纯净度：{d['purity']}"]
        dns = d.get("dns") or {}
        if dns.get("ok"):
            L.append(f"- DNS：{'⚠️ 存在泄露' if dns.get('leak') else '✅ 无泄露'}（{dns.get('resolver_geo')}）")
        unl = sum(1 for v in d["unlock"].values() if v == 200)
        L.append(f"- 解锁：{unl}/{len(d['unlock'])}")
        for name, code in d["unlock"].items():
            mark = "✅" if code == 200 else ("⚠️" if code in (403, 451) else "❌")
            L.append(f"  - {mark} {name}")
        L.append(f"- IPv6：{d.get('ipv6') or '无'}")
        for label, ms in (d.get("latency") or {}).items():
            L.append(f"- 延迟 {label}：{str(ms) + ' ms' if ms else '失败'}")
        for name, mbps in (d.get("speed") or {}).items():
            L.append(f"- 下载 {name}：{str(mbps) + ' Mbps' if mbps else '失败'}")
        L += ["", "---", ""]
    L.append("_由 NetPulse 生成 · 状态码为初步判断_")
    return "\n".join(L)


def main():
    global NO_COLOR
    ap = argparse.ArgumentParser(description="NetPulse — 为 AI agent 而生的 VPS 巡检工具")
    ap.add_argument("--host", action="append", default=[], metavar="user@host",
                    help="本地发起：经 SSH 在远端执行（可重复指定，批量巡检）")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读的 JSON（agent/脚本友好）")
    ap.add_argument("--share", action="store_true", help="另存 Markdown 报告")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    a = ap.parse_args()
    NO_COLOR = a.no_color or a.json

    if a.host:
        with futures.ThreadPoolExecutor(max_workers=min(8, len(a.host))) as ex:
            datas = list(ex.map(run_remote, a.host))
    else:
        datas = [collect(progress=not a.json)]

    failed = [d for d in datas if d.get("error")]

    if a.json:
        out = datas[0] if len(datas) == 1 else datas
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for d in datas:
            if d.get("error"):
                print(bad(f"[{d['host']}] {d['error']}"))
            else:
                render_terminal(d)
        render_compare(datas)

    if a.share:
        md = render_share_md(datas)
        fn = f"netpulse-report-{time.strftime('%Y%m%d-%H%M%S')}.md"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(md)
        print()
        print(ok(f"报告已保存：{fn}"))

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
