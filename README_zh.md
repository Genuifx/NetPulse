# NetPulse

> 一条命令，给你的服务器做一次全面体检。
>
> [English](README.md) · 中文

![demo](asset/demo.svg)

## 它能查什么

| 检查项 | 说明 |
|---|---|
| 🌐 IP 信息 | IP、地理位置、ASN、运营商 |
| 🧼 IP 纯净度 | 机房 / 家宽 / 代理特征，并用一句话告诉你这意味着什么 |
| 🎬 流媒体 / AI 解锁 | Netflix、Disney+、YouTube Premium、TikTok、ChatGPT、Gemini（状态码初步判断） |
| 🚀 速度测试 | 多节点下载测速 |
| 📋 一键报告 | `--share` 生成 Markdown 报告，方便发群里问"这机器怎么样" |

## 安装

**一键运行（推荐）：**
```bash
bash <(curl -sL https://raw.githubusercontent.com/Genuifx/NetPulse/main/install.sh)
```

**手动运行：**
```bash
git clone https://github.com/Genuifx/NetPulse.git
cd NetPulse
pip install -r requirements.txt
python3 netpulse.py          # 体检
python3 netpulse.py --share  # 体检 + 生成可分享的报告
```

## Agent 友好

专为 AI agent / 自动化脚本设计：

- `--json`：输出机器可读的 JSON，无彩色、无交互，直接管道给 `jq` 解析
- `--host user@host`：本地发起，经 SSH 在远端执行并取回结果（远端缺 `requests` 会自动安装）
- 非交互运行，失败时返回非零退出码并输出清晰错误

```bash
# 本地发起，检测远端 VPS，结果以 JSON 输出
python3 netpulse.py --host root@1.2.3.4 --json | jq '{purity: .purity, unlock: .unlock}'

# agent 批量巡检多台机器
for h in root@a root@b; do python3 netpulse.py --host $h --json; done | jq -s .
```

## 常见问题

**Q: 一定要下载到 VPS 上才能测吗？**
A: 不一定。NetPulse 测的是**目标机器**的网络出口，有两种用法：在 VPS 上直接跑，或者本地用 `--host user@host` 发起（经 SSH 在远端执行，结果拿回本地）。想测某个代理节点，就在本地终端挂上该节点后再跑。

**Q: 解锁检测准吗？**
A: 基于 HTTP 状态码的初步判断。Netflix 等建议用专项脚本二次确认。

**Q: 需要 root 吗？**
A: 不需要。纯 Python，只依赖 `requests`。

## 免责声明

仅用于检测你拥有或有权检测的服务器 / 网络。作者不对检测结果的使用承担责任。

## License

MIT — 欢迎 fork 二创，保留原作者链接即可。
