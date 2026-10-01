# NetPulse

> One command. A full health check for your server's network.
>
> English · [中文](README_zh.md)

![demo](asset/demo.svg)

## What it checks

| Check | Description |
|---|---|
| 🌐 IP info | IP, geolocation, ASN, ISP |
| 🧼 IP purity | Datacenter / residential / proxy traits, with a plain-language explanation |
| 🎬 Streaming / AI unlock | Netflix, Disney+, YouTube Premium, TikTok, ChatGPT, Gemini (HTTP-status based, preliminary) |
| 🚀 Speed test | Multi-endpoint download test |
| 📋 Shareable report | `--share` generates a Markdown report |

## Install

**One-liner (recommended):**
```bash
bash <(curl -sL https://raw.githubusercontent.com/Genuifx/NetPulse/main/install.sh)
```

**Manual:**
```bash
git clone https://github.com/Genuifx/NetPulse.git
cd NetPulse
pip install -r requirements.txt
python3 netpulse.py --share
```

## Agent-friendly

Built for AI agents / automation scripts:

- `--json`: machine-readable JSON output, no colors, no interaction — pipe straight into `jq`
- `--host user@host`: initiate locally, execute on the remote host over SSH, results come back (missing `requests` on the remote is auto-installed)
- Non-interactive, non-zero exit code with a clear error message on failure

```bash
# Initiate locally, check a remote VPS, JSON output
python3 netpulse.py --host root@1.2.3.4 --json | jq '{purity: .purity, unlock: .unlock}'

# Agent batch-checks multiple machines
for h in root@a root@b; do python3 netpulse.py --host $h --json; done | jq -s .
```

## FAQ

**Q: Does it have to run on the VPS?**
A: Not necessarily. NetPulse tests the **target machine's** network egress: either run it on the VPS directly, or initiate locally with `--host user@host` (executes remotely over SSH, results come back). To test a proxy node, run it locally behind that node.

**Q: How accurate is the unlock check?**
A: It's a preliminary check based on HTTP status codes. Use a dedicated script to double-check services like Netflix.

**Q: Does it need root?**
A: No. Pure Python, only depends on `requests`.

## Disclaimer

Only use on servers / networks you own or are authorized to test.

## License

MIT — forks and remixes welcome, keep the author link.
