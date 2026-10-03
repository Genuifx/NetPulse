#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetPulse v0.4.0 — 为 AI agent 而生的 VPS 巡检工具
用法:
  python3 netpulse.py                                  本机体检
  python3 netpulse.py --host root@a --host root@b      本地发起：批量巡检多台机器
  python3 netpulse.py --host root@a --json | jq .       agent 标准用法
  python3 netpulse.py --share [-o 报告.md]              另存 Markdown 报告
  python3 netpulse.py --no-speed                       跳过测速（按流量计费时用）
  python3 netpulse.py --timeout 60                     整体时间预算（秒）

设计原则（v0.4.0 起）：
- 每项检测返回统一结构 {status, value, evidence, error, duration_ms}，
  status ∈ ok / negative / unknown / error / skipped。
  「检测失败」绝不折叠成否定结论；总结只基于有证据的项生成。
- AI 可用性用 API 端点判定（401 缺 key 即证明可达），不用首页状态码；
  「未知」不等于「不可用」。
- --json 模式 stdout 只有纯 JSON；退出码 0=全部正常，1=部分主机失败，2=全部失败。

只依赖 requests（懒加载：缺失时检测项标记 error，--help 照常可用）。
"""

import argparse
import concurrent.futures as futures
import functools
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

VERSION = "0.4.1"
SCHEMA = 1

# ---------- 依赖懒加载：import 失败不退出，保证 --help 可用 ----------
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    _HAS_REQUESTS = False

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

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
def neg(t): return p("✗ " + t, C.RED)
def unk(t): return p("? " + t, C.YELLOW)
def err(t): return p("! " + t, C.RED)
def warn(t): return p("! " + t, C.YELLOW)
def info(t): return p("→ " + t, C.CYAN)
def skip(t): return p("– " + t, C.DIM)


def dw(s):
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


# ================= 检测框架 =================

def _mk(status, value=None, evidence=None, error=None):
    return {"status": status, "value": value, "evidence": evidence or {},
            "error": error, "duration_ms": 0}


def check(fn):
    """检测项装饰器：计时 + 异常隔离（单项抛异常只标 error，不拖垮整体）。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            r = fn(*args, **kwargs)
            if not isinstance(r, dict) or "status" not in r:
                r = _mk("error", error="检测函数返回结构异常")
        except Exception as e:
            r = _mk("error", error=f"{type(e).__name__}: {e}")
        r["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return r
    return wrapper


def _session():
    s = requests.Session()
    s.trust_env = False  # 不走环境代理：报告里只应有本机直连的一套数据
    s.headers.update({"User-Agent": UA})
    return s


def _need_requests():
    if not _HAS_REQUESTS:
        return _mk("error", error="缺少依赖 requests（远端自动安装失败或本地未安装）")
    return None


# ================= 各检测项 =================

@check
def check_ip(session):
    miss = _need_requests()
    if miss:
        return miss
    url = ("http://ip-api.com/json/?fields=status,message,country,countryCode,"
           "city,isp,org,as,query,hosting,proxy,timezone")
    r = session.get(url, timeout=10)
    d = r.json()
    if d.get("status") != "success":
        return _mk("error", error=d.get("message", "IP 查询失败"),
                   evidence={"source": "ip-api.com"})
    return _mk("ok", value=d, evidence={"source": "ip-api.com"})


def check_purity(ip_check):
    """纯函数：只做标记转述，不做过度推断（"未被标记"≠家宽）。"""
    if ip_check["status"] != "ok":
        return _mk("unknown", value="未知", error="IP 查询失败，无法判断")
    d = ip_check["value"]
    if d.get("proxy"):
        return _mk("ok", value="检测到代理特征",
                   evidence={"proxy": True, "hosting": d.get("hosting")})
    if d.get("hosting"):
        return _mk("ok", value="机房 IP（数据中心）",
                   evidence={"hosting": True})
    if d.get("hosting") is False:
        return _mk("ok", value="未被标记为机房/代理",
                   evidence={"hosting": False, "proxy": False})
    return _mk("unknown", value="未知")


@check
def check_dns(session, country):
    miss = _need_requests()
    if miss:
        return miss
    # 随机子域名绕缓存；失败则回退固定域名
    for host in (f"http://{random.randrange(10**11, 10**12)}.edns.ip-api.com/json",
                 "http://edns.ip-api.com/json"):
        try:
            d = session.get(host, timeout=10).json()
            dns = d.get("dns") or {}
            rip, rgeo = dns.get("ip"), dns.get("geo") or ""
            if not rip and not rgeo:
                continue
            if not country or not rgeo:
                return _mk("unknown", value="无结论",
                           evidence={"resolver_ip": rip, "resolver_geo": rgeo},
                           error="任一侧属地未知，无法判断")
            leak = not rgeo.startswith(country)
            return _mk("ok", value="属地不一致（观察）" if leak else "无异常",
                       evidence={"resolver_ip": rip, "resolver_geo": rgeo,
                                 "leak": leak})
        except Exception:
            continue
    return _mk("unknown", value="无结论", error="DNS 解析器信息获取失败")


LOGIN_HINT = re.compile(r"log[\s_-]*in|sign[\s_-]*in", re.I)


def _classify_page(resp):
    """流媒体首页：只看状态码会误判，登录跳转与验证页都算未知。"""
    code = resp.status_code
    final = resp.url or ""
    head = (resp.text or "")[:4000].lower()
    ev = {"http_code": code, "final_url": final}
    if "cf-mitigated" in resp.headers or "just a moment" in head:
        return _mk("unknown", value="无结论", evidence=ev, error="疑似 Cloudflare 机器人验证")
    if LOGIN_HINT.search(final):
        return _mk("unknown", value="无结论", evidence=ev, error="跳转到登录页，需人工确认")
    if code == 200:
        return _mk("ok", value="HTTP 可达（初步判断）", evidence=ev)
    if code in (403, 451):
        return _mk("negative", value="疑似区域限制", evidence=ev)
    return _mk("negative", value=f"HTTP {code}", evidence=ev)


def _classify_api(resp, name):
    """AI 的 API 端点：401/400（缺 key 但到达服务端）= 可达，这是真信号。
    首页 403 很多时候只是反爬，不能据此判死刑。"""
    code = resp.status_code
    body = (resp.text or "")[:2000].lower()
    final = resp.url or ""
    ev = {"http_code": code, "final_url": final}
    if "cf-mitigated" in resp.headers or "just a moment" in body:
        return _mk("unknown", value="无结论", evidence=ev, error="疑似 Cloudflare 机器人验证")
    if code == 401:
        return _mk("ok", value="API 可达（需自备 key）", evidence=ev)
    if code == 400 and ("key" in body or "auth" in body):
        return _mk("ok", value="API 可达（需自备 key）", evidence=ev)
    if code == 403 and ("unsupported_country" in body or "country" in body):
        return _mk("negative", value="区域限制", evidence=ev)
    if code == 403:
        return _mk("unknown", value="无结论", evidence=ev, error="被拒绝（原因不明，可能是反爬）")
    if code == 200:
        return _mk("ok", value="API 可达", evidence=ev)
    return _mk("negative", value=f"HTTP {code}", evidence=ev)


# (名称, URL, 是否 API 探针)
TARGETS = [
    ("Netflix", "https://www.netflix.com/title/80018499", False),
    ("Disney+", "https://www.disneyplus.com/", False),
    ("YouTube Premium", "https://www.youtube.com/premium", False),
    ("HBO Max", "https://www.max.com/", False),
    ("Hulu", "https://www.hulu.com/", False),
    ("Prime Video", "https://www.primevideo.com/", False),
    ("TikTok", "https://www.tiktok.com/", False),
    ("Spotify", "https://open.spotify.com/", False),
    ("ChatGPT", "https://api.openai.com/v1/models", True),
    ("Claude", "https://api.anthropic.com/v1/models", True),
    ("Gemini", "https://generativelanguage.googleapis.com/v1beta/models", True),
]
AI_NAMES = ("ChatGPT", "Claude", "Gemini")


def _probe_one(session, item):
    name, url, is_api = item
    try:
        r = session.get(url, timeout=8, allow_redirects=True)
    except Exception as e:
        return name, _mk("error", error=f"{type(e).__name__}: {e}",
                         evidence={"url": url})
    if is_api:
        return name, _classify_api(r, name)
    return name, _classify_page(r)


def _agg(children):
    """容器项状态由子项推导：不能永远报 ok，否则"全部失败"永远触发不了。"""
    sts = [c.get("status") for c in children.values()]
    if not sts:
        return "error"
    if all(s == "error" for s in sts):
        return "error"
    if all(s == "skipped" for s in sts):
        return "skipped"
    if all(s in ("error", "unknown", "skipped") for s in sts):
        return "unknown"
    return "ok"


@check
def check_unlock(session):
    miss = _need_requests()
    if miss:
        return miss
    results = {}
    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        for name, chk in ex.map(lambda it: _probe_one(session, it), TARGETS):
            results[name] = chk
    return _mk(_agg(results), value=results)


@check
def check_ipv6(session):
    """用 v6-only 端点：通 = 有 IPv6 出口；不通 = 无结论（不断言"无 IPv6"）。"""
    miss = _need_requests()
    if miss:
        return miss
    try:
        ip = session.get("https://api6.ipify.org?format=json",
                         timeout=10).json().get("ip", "")
    except Exception as e:
        return _mk("unknown", value="无结论",
                   error=f"IPv6 探测失败：{type(e).__name__}")
    if ":" in ip:
        return _mk("ok", value=ip, evidence={"source": "api6.ipify.org"})
    return _mk("unknown", value="无结论", error="未返回 IPv6 地址")


LAT_TARGETS = [
    ("Cloudflare", "1.1.1.1", 443),
    ("Google", "8.8.8.8", 443),
    ("百度", "www.baidu.com", 443),
]


def _tcp_once(host, port):
    ip = socket.gethostbyname(host)  # 先解析，计时只含建连
    t0 = time.perf_counter()
    s = socket.create_connection((ip, port), timeout=5)
    s.close()
    return (time.perf_counter() - t0) * 1000


@check
def check_latency():
    results = {}
    for label, host, port in LAT_TARGETS:
        samples = []
        for _ in range(3):
            try:
                samples.append(_tcp_once(host, port))
            except Exception:
                pass
        if samples:
            samples.sort()
            ms = samples[len(samples) // 2]
            results[label] = _mk("ok", value=round(ms),
                                 evidence={"samples": [round(x) for x in samples]})
        else:
            results[label] = _mk("error", error="连接失败")
    return _mk(_agg(results), value=results)


SPEED_URLS = [
    ("Cloudflare", "https://speed.cloudflare.com/__down?bytes=20000000"),
    ("CacheFly", "http://cachefly.cachefly.net/10mb.test"),
]


def _speed_one(session, name, url, budget=15):
    """串行测速；计时从首字节开始（含建连/TTFB 不计入）；
    响应过小（如错误页文本）直接判 error，不编造速率。"""
    t0 = time.perf_counter()
    try:
        r = session.get(url, timeout=(5, 10), stream=True)
        r.raise_for_status()
        n = 0
        first = None
        for chunk in r.iter_content(65536):
            if first is None:
                first = time.perf_counter()
            n += len(chunk)
            if time.perf_counter() - t0 > budget:
                break
        if n < 1_000_000:
            return _mk("error", error=f"响应仅 {n} 字节，非有效测速数据",
                       evidence={"url": url, "bytes": n})
        dt = time.perf_counter() - first
        mbps = round(n * 8 / dt / 1e6, 1) if dt > 0 else 0
        return _mk("ok", value=mbps,
                   evidence={"url": url, "bytes": n, "seconds": round(dt, 1)})
    except Exception as e:
        return _mk("error", error=f"{type(e).__name__}: {e}",
                   evidence={"url": url})


@check
def check_speed(session):
    miss = _need_requests()
    if miss:
        return miss
    results = {}
    for name, url in SPEED_URLS:  # 串行：并发会互相抢带宽
        results[name] = _speed_one(session, name, url)
    return _mk(_agg(results), value=results)


# ================= 汇总（只基于有证据的项） =================

def _measured(checks, name):
    c = checks.get(name) or {}
    return c.get("status") in ("ok", "negative")


def build_summary(checks):
    tips = []
    ip = checks.get("ip") or {}
    purity = checks.get("purity") or {}
    if _measured(checks, "purity"):
        label = purity.get("value", "")
        if "代理" in label:
            tips.append("检测到代理特征：当前走的是代理/VPN 出口，结果反映的是出口节点的情况。")
        elif "机房" in label:
            tips.append("机房 IP：适合建站/做节点；注册风控严的平台账号时多加小心。")
        elif "未被标记" in label:
            tips.append("IP 未被标记为机房/代理，初步判断相对干净；单数据源结论，仅供参考。")
    dns = checks.get("dns") or {}
    if dns.get("status") == "ok" and (dns.get("evidence") or {}).get("leak"):
        tips.append(f"DNS 解析器属地（{dns['evidence'].get('resolver_geo')}）与 IP 属地不一致 "
                    f"（观察项：VPS 上 anycast 解析器常见，不直接等同于泄露）。")

    unlock = (checks.get("unlock") or {}).get("value") or {}
    streaming = {k: v for k, v in unlock.items() if k not in AI_NAMES}
    ai = {k: v for k, v in unlock.items() if k in AI_NAMES}
    s_ok = [k for k, v in streaming.items() if v.get("status") == "ok"]
    s_measured = [k for k, v in streaming.items() if v.get("status") in ("ok", "negative")]
    if s_measured:
        tips.append(f"流媒体 {len(s_ok)}/{len(s_measured)} 初步可达"
                    + ("" if len(s_ok) == len(s_measured) else "，其余疑似区域限制"))
    a_ok = [k for k, v in ai.items() if v.get("status") == "ok"]
    a_neg = [k for k, v in ai.items() if v.get("status") == "negative"]
    a_unk = [k for k, v in ai.items() if v.get("status") in ("unknown", "error")]
    if len(a_ok) == 3:
        tips.append("AI 服务 API 均可达（ChatGPT / Claude / Gemini，需自备 key）。")
    elif a_ok:
        tips.append(f"AI 服务部分可达：{ '、'.join(a_ok) } API 可达"
                    + (f"，{ '、'.join(a_neg) } 疑似区域限制" if a_neg else "")
                    + (f"，{ '、'.join(a_unk) } 无结论" if a_unk else "") + "。")
    elif a_neg:
        tips.append(f"AI 服务疑似区域限制：{ '、'.join(a_neg) }。")
    elif a_unk:
        tips.append("AI 服务检测无结论（被验证页拦截或探测失败），不代表不可用，建议人工复核。")

    lat = (checks.get("latency") or {}).get("value") or {}
    lat_ok = [(k, v["value"]) for k, v in lat.items() if v.get("status") == "ok"]
    if lat_ok:
        best = min(lat_ok, key=lambda x: x[1])
        if best[1] < 150:
            tips.append(f"延迟最低为{best[0]} {best[1]}ms，网络响应良好。")
        else:
            tips.append(f"延迟最低为{best[0]} {best[1]}ms。")
    return tips


# ================= 采集 =================

def _skipped(name):
    return {"status": "skipped", "value": None, "evidence": {},
            "error": "超出时间预算", "duration_ms": 0}


def collect(progress=False, deadline=None, no_speed=False):
    def budget_ok():
        return deadline is None or time.monotonic() < deadline

    def step(msg, fn):
        if progress:
            print(p(f"  ▸ {msg}…", C.DIM), flush=True)
        if not budget_ok():
            return _skipped(msg)
        return fn()

    session = _session() if _HAS_REQUESTS else None
    t0 = time.perf_counter()
    checks = {}
    checks["ip"] = step("正在查询 IP 信息", lambda: check_ip(session))
    checks["purity"] = check_purity(checks["ip"])
    country = (checks["ip"].get("value") or {}).get("country")
    checks["dns"] = step("正在检测 DNS", lambda: check_dns(session, country))
    checks["unlock"] = step("正在检测流媒体/AI", lambda: check_unlock(session))
    checks["ipv6"] = step("正在检测 IPv6", lambda: check_ipv6(session))
    checks["latency"] = step("正在测试延迟", check_latency)
    if no_speed:
        checks["speed"] = {"status": "skipped", "value": None, "evidence": {},
                           "error": "--no-speed", "duration_ms": 0}
    else:
        checks["speed"] = step("正在测速", lambda: check_speed(session))
    summary = build_summary(checks)
    measured = any(c.get("status") in ("ok", "negative") for c in checks.values())
    return {
        "host": "local",
        "status": "ok" if measured else "error",
        "error_code": None if measured else "all_failed",
        "error": None if measured else "所有检测项均未能完成（超时或网络不可达），无有效结论",
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "checks": checks,
        "summary": summary,
    }


# ================= 远端执行（本地发起） =================

_SSH_ERR_PATTERNS = [
    (re.compile(r"permission denied", re.I), "auth"),
    (re.compile(r"timed out|timeout|no route to host", re.I), "timeout"),
    (re.compile(r"python3.*not found|command not found", re.I), "no_python"),
    (re.compile(r"connection (refused|reset|closed)", re.I), "transport"),
]


def _host_error(host, code, message):
    return {"host": host, "status": "error", "error_code": code,
            "error": message, "duration_ms": 0, "checks": {}, "summary": []}


def run_remote(host, timeout=300, no_speed=False):
    """经 SSH 把本脚本喂给远端 python3 执行，取回 JSON envelope。
    任何异常都转成 error 结果，不抛异常（批量隔离）。
    --no-speed / --timeout 会透传给远端（timeout 扣掉 SSH 开销余量）。"""
    t0 = time.perf_counter()
    if host.startswith("-"):
        return _host_error(host, "bad_host", "主机名不能以 - 开头")
    try:
        with open(os.path.realpath(__file__), "rb") as f:
            script = f.read()
    except OSError as e:
        return _host_error(host, "local_error", f"无法读取本脚本：{e}")
    remote = ["python3", "-", "--json", "--no-color"]
    if no_speed:
        remote.append("--no-speed")
    remote += ["--timeout", str(max(15, timeout - 20))]
    # "--" 结束 ssh 选项，防止 --host '-oProxyCommand=...' 注入本地命令执行
    cmd = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new",
           "--", host, " ".join(remote)]
    try:
        pr = subprocess.run(cmd, input=script, capture_output=True,
                            timeout=timeout)
    except FileNotFoundError:
        return _host_error(host, "no_ssh", "未找到 ssh 命令，无法使用 --host")
    except subprocess.TimeoutExpired:
        return _host_error(host, "timeout", f"SSH 执行超时（{timeout}s）：{host}")
    # 先尝试解析 stdout：远端可能已输出合法 envelope 只是非零退出
    #（例如远端全部检测失败 exit 2），结果不应被丢掉
    try:
        data = json.loads(pr.stdout.decode())
    except Exception:
        data = None
    if isinstance(data, dict) and "results" in data and data.get("results"):
        r = data["results"][0]
        r["host"] = host
        r["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return r
    if pr.returncode != 0:
        err = pr.stderr.decode(errors="replace").strip().splitlines()
        tail = err[-1] if err else f"exit {pr.returncode}"
        code = "transport"
        for pat, c in _SSH_ERR_PATTERNS:
            if pat.search(tail):
                code = c
                break
        return _host_error(host, code,
                           f"远端执行失败 [{code}]：{tail} "
                           f"（远端需要 python3 + requests，可手动 pip install requests）")
    return _host_error(host, "bad_output",
                       "远端返回的不是合法 JSON（远端 python3 可能不可用）")


# ================= 输出渲染 =================

def ms_color(ms):
    if ms is None:
        return C.DIM
    return C.GREEN if ms < 150 else (C.YELLOW if ms < 300 else C.RED)


_STATUS_SYM = {
    "ok": ("✓", C.GREEN),
    "negative": ("✗", C.RED),
    "unknown": ("?", C.YELLOW),
    "error": ("!", C.RED),
    "skipped": ("–", C.DIM),
}


def _sym(status):
    s, c = _STATUS_SYM.get(status, ("·", C.DIM))
    return p(s + " ", c)


def render_terminal(result):
    banner()
    checks = result.get("checks") or {}
    ip = (checks.get("ip") or {}).get("value") or {}

    section(f"1/5 · IP 信息  [{result['host']}]")
    if (checks.get("ip") or {}).get("status") == "ok":
        kv("IP", ip.get("query", "-"))
        kv("位置", f"{ip.get('country', '?')} · {ip.get('city', '?')} ({ip.get('timezone', '?')})")
        kv("ASN", ip.get("as", "?"))
        kv("运营商", ip.get("isp", "?"))
    else:
        print("    " + err(f"IP 查询失败：{(checks.get('ip') or {}).get('error')}"))

    section("2/5 · IP 纯净度")
    purity = checks.get("purity") or {}
    label = purity.get("value", "未知")
    fn = warn if ("机房" in label or "代理" in label) else (ok if "未被标记" in label else info)
    print("    " + fn(label))
    if purity.get("status") == "unknown":
        print(p(f"    （{purity.get('error')}）", C.DIM))
    print(p("    “未被标记”指 ip-api 未将其标为机房/代理，不等于原生住宅 IP。", C.DIM))

    section("3/5 · DNS 解析器归属地")
    dns = checks.get("dns") or {}
    dev = dns.get("evidence") or {}
    if dns.get("status") == "ok":
        leak = dev.get("leak")
        line = (warn(f"属地不一致（观察）：{dev.get('resolver_ip')}（{dev.get('resolver_geo')}）")
                if leak else ok(f"无异常：{dev.get('resolver_ip')}（{dev.get('resolver_geo')}）"))
        print("    " + line)
    else:
        print("    " + unk(f"无结论：{dns.get('error')}"))
    print(p("    VPS 的解析器走 anycast 很常见，属地差异仅作观察，不直接等同于“泄露”。", C.DIM))

    section("4/5 · 流媒体 / AI 检测")
    unlock = (checks.get("unlock") or {}).get("value") or {}
    for name, u in unlock.items():
        st = u.get("status")
        note = u.get("value") or u.get("error") or ""
        ev = u.get("evidence") or {}
        extra = f"（{ev.get('http_code')}）" if ev.get("http_code") else ""
        print(f"    {_sym(st)}{p(pad(name, 16), C.DIM)}{p(note, C.BOLD)}{p(extra, C.DIM)}")
    print(p("    AI 服务走 API 端点判定：401（缺 key 但到达服务端）= 可达；首页状态码不作为依据。", C.DIM))

    section("5/5 · 网络质量")
    v6 = checks.get("ipv6") or {}
    if v6.get("status") == "ok":
        print("    " + ok(f"IPv6 出口：{v6.get('value')}"))
    else:
        print("    " + unk(f"IPv6：{v6.get('error') or '无结论'}"))
    lat = (checks.get("latency") or {}).get("value") or {}
    for label, l in lat.items():
        if l.get("status") == "ok":
            print(f"    {p(pad('延迟 ' + label, 16), C.DIM)}"
                  f"{p(str(l['value']) + ' ms', ms_color(l['value']))}")
        else:
            print("    " + err(f"延迟 {label}：{l.get('error')}"))
    speed = (checks.get("speed") or {}).get("value") or {}
    for name, s in speed.items():
        if s.get("status") == "ok":
            print(f"    {p(pad('下载 ' + name, 16), C.DIM)}{p(str(s['value']) + ' Mbps', C.BOLD)}")
        elif s.get("status") == "skipped":
            print("    " + skip(f"下载 {name}：已跳过（--no-speed）"))
        else:
            print("    " + err(f"下载 {name}：{s.get('error')}"))

    section("体检总结")
    for t in result.get("summary") or []:
        print("    " + info(t))
    if result.get("status") == "error":
        print("    " + err(f"本机采集失败：{result.get('error')}"))
    print()


def short_purity(checks):
    label = (checks.get("purity") or {}).get("value", "")
    if "机房" in label:
        return "机房IP"
    if "未被标记" in label:
        return "未标记"
    if "代理" in label:
        return "代理"
    return "未知"


def render_compare(results):
    results = [r for r in results if r.get("status") == "ok"]
    if len(results) < 2:
        return
    section(f"多机对比（{len(results)} 台）")
    cols = ["主机", "出口 IP", "纯净度", "流媒体", "AI", "延迟", "下载"]
    rows = []
    for r in results:
        checks = r.get("checks") or {}
        unlock = (checks.get("unlock") or {}).get("value") or {}
        s_ok = sum(1 for k, v in unlock.items()
                   if k not in AI_NAMES and v.get("status") == "ok")
        s_n = sum(1 for k, v in unlock.items() if k not in AI_NAMES)
        a_ok = sum(1 for k, v in unlock.items()
                   if k in AI_NAMES and v.get("status") == "ok")
        lat = (checks.get("latency") or {}).get("value") or {}
        lats = [v["value"] for v in lat.values() if v.get("status") == "ok"]
        spd = (checks.get("speed") or {}).get("value") or {}
        spds = [v["value"] for v in spd.values() if v.get("status") == "ok"]
        rows.append([
            r["host"],
            ((checks.get("ip") or {}).get("value") or {}).get("query", "-"),
            short_purity(checks),
            f"{s_ok}/{s_n}",
            f"{a_ok}/3",
            f"{min(lats)}ms" if lats else "-",
            f"{max(spds)}Mbps" if spds else "-",
        ])
    widths = [max(dw(r[i]) for r in [cols] + rows) for i in range(len(cols))]
    print("    " + "  ".join(p(pad(c, widths[i]), C.DIM) for i, c in enumerate(cols)))
    print("    " + p("  ".join("─" * widths[i] for i in range(len(cols))), C.DIM))
    for r in rows:
        print("    " + "  ".join(pad(c, widths[i]) for i, c in enumerate(r)))
    print()


def render_share_md(results, version):
    L = [f"# NetPulse 巡检报告（v{version}）", ""]
    for r in results:
        if r.get("status") == "error":
            L += [f"## {r['host']}", f"- ✗ 采集失败 [{r.get('error_code')}]：{r.get('error')}", ""]
            continue
        checks = r.get("checks") or {}
        ip = (checks.get("ip") or {}).get("value") or {}
        L += [f"## {r['host']}",
              f"- 出口 IP：{ip.get('query', '-')}（{ip.get('country', '?')} {ip.get('city', '?')}）",
              f"- ASN：{ip.get('as', '?')} / {ip.get('isp', '?')}",
              f"- 纯净度：{(checks.get('purity') or {}).get('value', '未知')}"]
        dns = checks.get("dns") or {}
        dev = dns.get("evidence") or {}
        if dns.get("status") == "ok":
            L.append(f"- DNS 解析器：{dev.get('resolver_ip')}（{dev.get('resolver_geo')}）"
                     f"{'，疑似泄露' if dev.get('leak') else ''}")
        else:
            L.append(f"- DNS：无结论（{dns.get('error')}）")
        unlock = (checks.get("unlock") or {}).get("value") or {}
        for name, u in unlock.items():
            sym = {"ok": "✅", "negative": "❌", "unknown": "❓",
                   "error": "⚠️", "skipped": "⏭️"}.get(u.get("status"), "❓")
            L.append(f"  - {sym} {name}：{u.get('value') or u.get('error')}")
        v6 = checks.get("ipv6") or {}
        L.append(f"- IPv6：{v6.get('value') if v6.get('status') == 'ok' else '无结论'}")
        lat = (checks.get("latency") or {}).get("value") or {}
        for label, l in lat.items():
            L.append(f"- 延迟 {label}：{str(l['value']) + ' ms' if l.get('status') == 'ok' else l.get('error')}")
        spd = (checks.get("speed") or {}).get("value") or {}
        for name, s in spd.items():
            L.append(f"- 下载 {name}：{str(s['value']) + ' Mbps' if s.get('status') == 'ok' else (s.get('error') or '跳过')}")
        for t in r.get("summary") or []:
            L.append(f"- 💡 {t}")
        L += ["", "---", ""]
    L.append("_由 NetPulse 生成 · 检测结论均为初步判断，unknown 表示无结论而非不可用_")
    return "\n".join(L)


# ================= 主流程 =================

def _utcnow():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main():
    global NO_COLOR
    ap = argparse.ArgumentParser(description="NetPulse — 为 AI agent 而生的 VPS 巡检工具")
    ap.add_argument("--host", action="append", default=[], metavar="user@host",
                    help="本地发起：经 SSH 在远端执行（可重复指定，批量巡检）")
    ap.add_argument("--json", action="store_true", help="输出机器可读的 JSON（agent/脚本友好）")
    ap.add_argument("--share", action="store_true", help="另存 Markdown 报告")
    ap.add_argument("-o", "--output", metavar="PATH", help="报告保存路径（配合 --share）")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    ap.add_argument("--timeout", type=int, default=120, metavar="SEC",
                    help="整体时间预算（秒），默认 120")
    ap.add_argument("--no-speed", action="store_true", help="跳过测速（按流量计费时用）")
    a = ap.parse_args()
    NO_COLOR = a.no_color or a.json

    deadline = time.monotonic() + a.timeout
    t0 = time.perf_counter()

    if a.host:
        with futures.ThreadPoolExecutor(max_workers=min(8, len(a.host))) as ex:
            futs = {ex.submit(run_remote, h, a.timeout, a.no_speed): h
                    for h in a.host}
            tmp = []
            for f in futures.as_completed(futs):
                h = futs[f]
                try:
                    tmp.append(f.result())
                except Exception as e:  # 最后一道隔离：单台异常不影响其他
                    tmp.append(_host_error(h, "local_error",
                                           f"本地处理异常：{type(e).__name__}: {e}"))
            order = {h: i for i, h in enumerate(a.host)}
            results = sorted(tmp, key=lambda r: order.get(r["host"], 0))
    else:
        results = [collect(progress=not a.json, deadline=deadline,
                           no_speed=a.no_speed)]

    envelope = {
        "schema": SCHEMA,
        "tool": "netpulse",
        "version": VERSION,
        "generated_at": _utcnow(),
        "results": results,
    }

    if a.json:
        # stdout 只输出纯 JSON，任何提示走 stderr
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r.get("status") == "error":
                print(err(f"[{r['host']}] [{r.get('error_code')}] {r.get('error')}"))
            else:
                render_terminal(r)
        render_compare(results)

    if a.share:
        md = render_share_md(results, VERSION)
        fn = a.output or f"netpulse-report-{time.strftime('%Y%m%d-%H%M%S')}.md"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(md)
        print(info(f"报告已保存：{fn}"), file=sys.stderr)

    statuses = [r.get("status") for r in results]
    if all(s == "ok" for s in statuses):
        sys.exit(0)
    elif all(s == "error" for s in statuses):
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
