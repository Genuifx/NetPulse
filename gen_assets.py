#!/usr/bin/env python3
# 生成 README 用的 banner.svg 和 demo.svg（demo.svg 内容来自真实运行输出）
import subprocess
import html
import os
import re

os.makedirs("asset", exist_ok=True)

banner = '''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="200">
<rect width="800" height="200" rx="16" fill="#0d1117"/>
<text x="40" y="95" font-family="monospace" font-size="44" font-weight="bold" fill="#22d3ee">NetPulse</text>
<text x="40" y="140" font-family="monospace" font-size="20" fill="#8b949e">一条命令，给你的服务器做一次全面体检</text>
<text x="40" y="170" font-family="monospace" font-size="14" fill="#484f58">IP 纯净度 · 流媒体解锁 · 速度测试 · 一键报告</text>
</svg>'''
with open("asset/banner.svg", "w", encoding="utf-8") as f:
    f.write(banner)

out = subprocess.run(["python3", "netpulse.py", "--no-color"],
                     capture_output=True, text=True).stdout
# 脱敏：真实出口 IP 绝不能进公开截图，统一换成文档保留地址（RFC 5737 TEST-NET-3）
out = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "203.0.113.10", out)
lines = [html.escape(l) for l in out.splitlines()]

W, FS, LH, PAD = 880, 13, 19, 24
H = PAD * 2 + 40 + len(lines) * LH
texts = "".join(
    f'<text x="{PAD}" y="{PAD + 40 + i * LH}" font-family="monospace" '
    f'font-size="{FS}" fill="#c9d1d9">{l or " "}</text>'
    for i, l in enumerate(lines))

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">
<rect width="{W}" height="{H}" rx="12" fill="#0d1117"/>
<circle cx="28" cy="22" r="7" fill="#ff5f57"/><circle cx="52" cy="22" r="7" fill="#febc2e"/><circle cx="76" cy="22" r="7" fill="#28c840"/>
<text x="110" y="27" font-family="monospace" font-size="14" fill="#8b949e">netpulse — 一键服务器网络体检（真实运行截图）</text>
{texts}
</svg>'''
with open("asset/demo.svg", "w", encoding="utf-8") as f:
    f.write(svg)

print(f"assets ok: {len(lines)} lines -> asset/demo.svg")
