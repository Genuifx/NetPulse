#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NetPulse v0.4.0 回归测试：可信结论 + 可靠输出。不依赖外网（分类器用 mock）。"""
import importlib.util
import json
import subprocess
import sys

spec = importlib.util.spec_from_file_location("np", "netpulse.py")
np = importlib.util.module_from_spec(spec)
spec.loader.exec_module(np)

passed = failed = 0


def t(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}")


class R:  # 假响应
    def __init__(self, code, url, text="", headers=None):
        self.status_code = code
        self.url = url
        self.text = text
        self.headers = headers or {}


print("== AI 判定：API 端点为准，首页 403 不判死刑 ==")
r = np._classify_api(R(401, "https://api.openai.com/v1/models",
                        '{"error":{"code":"invalid_api_key"}}'), "ChatGPT")
t("ChatGPT API 401 → ok（可达）", r["status"] == "ok")
r = np._classify_api(R(403, "https://api.openai.com/v1/models",
                        '{"error":{"code":"unsupported_country"}}'), "ChatGPT")
t("API 403 unsupported_country → negative", r["status"] == "negative")
r = np._classify_api(R(403, "https://api.openai.com/v1/models", "blocked"), "ChatGPT")
t("API 403 原因不明 → unknown（不断言）", r["status"] == "unknown")
r = np._classify_api(R(200, "https://x", "", {"cf-mitigated": "challenge"}), "Claude")
t("Cloudflare 验证页 → unknown", r["status"] == "unknown")

print("== 首页探测：登录跳转不算解锁 ==")
r = np._classify_page(R(200, "https://x/login", "<html>ok</html>"))
t("200 但跳登录页 → unknown", r["status"] == "unknown")
r = np._classify_page(R(200, "https://www.netflix.com/title/1", "<html>ok</html>"))
t("普通 200 → ok（初步）", r["status"] == "ok")

print("== 总结：只基于证据，不过度结论 ==")
def svc(status):
    return {"status": status, "value": "x" if status == "ok" else None,
            "evidence": {}, "error": None, "duration_ms": 1}

# GPT 报告的场景：ChatGPT/Claude 403（negative），流媒体 200
checks = {
    "ip": {"status": "ok", "value": {"country": "Japan"}, "evidence": {}, "error": None, "duration_ms": 1},
    "purity": {"status": "ok", "value": "机房 IP（数据中心）", "evidence": {}, "error": None, "duration_ms": 0},
    "dns": {"status": "ok", "value": "无泄露", "evidence": {"leak": False}, "error": None, "duration_ms": 1},
    "unlock": {"status": "ok", "value": {
        "Netflix": svc("ok"), "Disney+": svc("ok"), "YouTube Premium": svc("ok"),
        "HBO Max": svc("ok"), "Hulu": svc("ok"), "Prime Video": svc("ok"),
        "TikTok": svc("ok"), "Spotify": svc("ok"),
        "ChatGPT": svc("negative"), "Claude": svc("negative"), "Gemini": svc("unknown"),
    }, "evidence": {}, "error": None, "duration_ms": 1},
    "ipv6": {"status": "unknown", "value": "无结论", "evidence": {}, "error": "探测失败", "duration_ms": 1},
    "latency": {"status": "ok", "value": {}, "evidence": {}, "error": None, "duration_ms": 1},
    "speed": {"status": "skipped", "value": None, "evidence": {}, "error": "--no-speed", "duration_ms": 0},
}
s = np.build_summary(checks)
t("不出现'跑 AI 没压力'", not any("没压力" in x for x in s))
t("AI 区域限制被如实指出", any("区域限制" in x for x in s))
t("IPv6 unknown 不生成'无 IPv6'断言", not any("无 IPv6" in x for x in s))
t("流媒体结论基于实测计数", any("8/8" in x for x in s))

# 全 unknown 的 AI：不判死刑
checks2 = dict(checks)
unlock2 = dict(checks["unlock"])
unlock2["value"] = {k: svc("unknown") for k in
                    ["Netflix", "Disney+", "YouTube Premium", "HBO Max", "Hulu",
                     "Prime Video", "TikTok", "Spotify", "ChatGPT", "Claude", "Gemini"]}
unlock2["value"].update({"Netflix": svc("ok")})
checks2["unlock"] = unlock2
s2 = np.build_summary(checks2)
t("AI 全 unknown → '不代表不可用'", any("不代表不可用" in x for x in s2))

print("== DNS：空数据不开无罪证明 ==")
t("纯净度措辞不用'家宽/原生'",
  "家宽" not in np.check_purity(
      {"status": "ok", "value": {"proxy": False, "hosting": False},
       "evidence": {}, "error": None, "duration_ms": 1})["value"])

print("== CLI：JSON 纯净 + 退出码 ==")
p = subprocess.run([sys.executable, "netpulse.py", "--json", "--no-speed",
                    "--share", "-o", "/tmp/np-test-report.md"],
                   capture_output=True, text=True, timeout=180)
try:
    env = json.loads(p.stdout)
    t("--json --share 的 stdout 可被 json 解析", isinstance(env, dict))
    t("envelope 有 schema/results", env.get("schema") == 1 and "results" in env)
except Exception as e:
    t(f"--json --share 的 stdout 可被 json 解析（{e}）", False)
t("--share 提示走 stderr", "报告已保存" in p.stderr and "报告已保存" not in p.stdout)

p2 = subprocess.run([sys.executable, "netpulse.py", "--host", "root@192.0.2.99",
                     "--host", "root@198.51.100.7", "--json", "--no-speed"],
                    capture_output=True, text=True, timeout=120)
try:
    env2 = json.loads(p2.stdout)
    t("批量全失败仍输出合法 JSON", isinstance(env2, dict))
    t("每台独立 error 结果", all(r["status"] == "error" for r in env2["results"])
      and len(env2["results"]) == 2)
except Exception as e:
    t(f"批量全失败仍输出合法 JSON（{e}）", False)
t("批量全失败退出码为 2", p2.returncode == 2)

p3 = subprocess.run([sys.executable, "netpulse.py", "--help"],
                    capture_output=True, text=True, timeout=30)
t("--help 正常工作", p3.returncode == 0 and "--host" in p3.stdout)

print("== 容器状态聚合：子项全失败不再报 ok ==")
def child(st):
    return {"status": st, "value": None, "evidence": {}, "error": None, "duration_ms": 1}
t("全 error → error", np._agg({"a": child("error"), "b": child("error")}) == "error")
t("全 skipped → skipped", np._agg({"a": child("skipped")}) == "skipped")
t("混合 ok/error → ok", np._agg({"a": child("ok"), "b": child("error")}) == "ok")
t("全 unknown → unknown", np._agg({"a": child("unknown")}) == "unknown")

print("== 终端渲染冒烟（回归 warn 未定义 crash）==")
np.NO_COLOR = True
fake_checks = {
    "ip": {"status": "ok",
            "value": {"query": "203.0.113.10", "country": "Japan", "city": "Tokyo",
                      "timezone": "Asia/Tokyo", "as": "AS16509", "isp": "Example"},
            "evidence": {}, "error": None, "duration_ms": 1},
    "purity": {"status": "ok", "value": "机房 IP（数据中心）", "evidence": {}, "error": None, "duration_ms": 0},
    "dns": {"status": "ok", "value": "属地不一致（观察）",
            "evidence": {"resolver_ip": "8.8.8.8", "resolver_geo": "United States", "leak": True},
            "error": None, "duration_ms": 1},
    "unlock": {"status": "ok", "value": {
        "Netflix": child("ok"), "ChatGPT": child("unknown"), "Claude": child("negative"),
    }, "evidence": {}, "error": None, "duration_ms": 1},
    "ipv6": {"status": "unknown", "value": "无结论", "evidence": {}, "error": "探测失败", "duration_ms": 1},
    "latency": {"status": "ok", "value": {"Cloudflare": dict(child("ok"), value=12)},
                "evidence": {}, "error": None, "duration_ms": 1},
    "speed": {"status": "skipped", "value": None, "evidence": {}, "error": "--no-speed", "duration_ms": 0},
}
fake = {"host": "root@test", "status": "ok", "error_code": None, "error": None,
        "duration_ms": 1, "checks": fake_checks, "summary": ["机房 IP：适合建站/做节点。"]}
import io
from contextlib import redirect_stdout
try:
    with redirect_stdout(io.StringIO()):
        np.render_terminal(fake)
        np.render_compare([fake, dict(fake, host="root@test2")])
        md = np.render_share_md([fake], "0.4.1")
    t("render_terminal/render_compare/render_share_md 不崩", "203.0.113.10" in md)
except Exception as e:
    t(f"render_terminal/render_compare 不崩（{type(e).__name__}: {e}）", False)

print(f"\n{passed} 通过，{failed} 失败")
sys.exit(1 if failed else 0)
