# NetPulse

> **The VPS inspector built for AI agents**: one command from your laptop, health-check your whole fleet, pipe results straight to an agent.

[English](README.md) · [中文](README_zh.md)

🌐 Website: https://genuifx.github.io/NetPulse/

![demo](asset/demo.svg)

## Why NetPulse: agent-friendly + fleet inspection

Other checkup scripts make you SSH into each box and run them. NetPulse flips it — **you launch from your laptop, it executes on the remote box, results come back to you**:

- **Launch locally**: `--host user@host` ships the script over SSH and runs it remotely; missing deps auto-install, zero manual steps on the remote
- **Fleet inspection**: repeat `--host` to check your whole fleet at once, with a multi-host comparison table
- **JSON output**: `--json` is machine-readable — pipe into `jq` or feed to an AI agent
- **Non-interactive**: no prompts, no color in JSON mode, clear errors + non-zero exit codes so agents never hang

```bash
# Inspect three boxes, hand the JSON to an agent
python3 netpulse.py --host root@vps-a --host root@vps-b --host root@vps-c --json | jq .
```

## One-liner

```bash
bash <(curl -sL https://raw.githubusercontent.com/Genuifx/NetPulse/main/install.sh)
```

Or manually:

```bash
git clone https://github.com/Genuifx/NetPulse.git && cd NetPulse
python3 netpulse.py
```

Only dependency is `requests` (auto-installed when missing).

## What it checks

| Category | Details |
|---|---|
| IP info | Egress IP, location, ASN, ISP |
| IP purity | Datacenter IP / residential IP / proxy traits |
| DNS leak | Resolver geo vs IP geo |
| Unlock tests | Netflix, Disney+, YouTube Premium, HBO Max, Hulu, Prime Video, TikTok, Spotify, ChatGPT, Claude, Gemini (11 targets) |
| Network quality | IPv6 egress, TCP latency to Cloudflare/Google/Baidu, download speed |

`--share` saves a Markdown report — handy for group chats or support tickets.

## The standard agent recipe

```bash
# Quick verdict: IP purity + AI reachability
python3 netpulse.py --host root@1.2.3.4 --json \
  | jq '.results[0] | {purity: .checks.purity.value, ai: [.checks.unlock.value | to_entries[] | select(["ChatGPT","Claude","Gemini"] | index(.key)) | {key, status: .value.status}]}'

# Inspect the whole fleet
python3 netpulse.py --host root@a --host root@b --host root@c --json \
  | jq '.results[] | {host, status, purity: .checks.purity.value}'
```

JSON is a fixed envelope: `{schema, tool, version, generated_at, results[]}` —
same shape for one host or many. Each result carries `status` (ok/error),
`error_code`, `checks` (each `{status, value, evidence, error, duration_ms}`,
status ∈ ok/negative/unknown/error/skipped) and `summary`. In `--json` mode,
stdout is pure JSON.

Exit codes: `0` all hosts fine, `1` some hosts failed, `2` all hosts failed.

Other flags: `--no-speed` skips speed tests (for metered boxes), `--timeout SEC`
sets the overall time budget (default 120s), `--share -o report.md` saves a
Markdown report.

## FAQ

**How accurate are the unlock tests?**
HTTP status only — a first pass (200 ≈ reachable, 403/451 ≈ likely geo-blocked). Use a dedicated script to double-check Netflix etc. We're the quick triage, not the final word.

**What does `--host` need?**
An `ssh` binary locally, `python3` on the remote, and key-based auth (`BatchMode=yes` — no interactive password prompts).

**Jump hosts / custom ports?**
`--host` is plain SSH — your `~/.ssh/config` applies as usual.

## Disclaimer

This tool inspects the network of **your own servers** only. It does not scan or track anyone else. Don't run it on machines you aren't authorized for.
