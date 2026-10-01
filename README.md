# ShiPu WP — Termux Tool

AI-powered WhatsApp assistant that runs in Termux on Android.

## Install

```bash
pkg install python
pip install -r requirements.txt
python start.py
```

No `.env` file needed — all config is built in.

## Features

- Polished responsive terminal UI
- Firebase auth (accounts shared with the website)
- OpenRouter AI with model failover
- WhatsApp listener bridge (NDJSON event bus)
- Duplicate suppression
- Server-authoritative billing — local state can never grant Pro

## Configure

Edit `tool/config/settings.py` to change:

| Setting | Default |
| --- | --- |
| `OPENROUTER_MODEL` | `liquid/lfm-2.5-26b:free` |
| `OPENROUTER_FALLBACK_MODELS` | `dots-studio/dots-3-note-preview:free,...` |
| `SHIPU_FREE_DAILY_REPLIES` | `25` |
| `SHIPU_PRO_PRICE_CENTS` | `1200` |
| `SHIPU_PRO_DURATION_DAYS` | `30` |

## Android listener

Set `SHIPU_BRIDGE_DIR` on both the Android app and Termux to the same path.

## Security

- Server-only secrets are refused at startup.
- `plan`, `subscription`, `payments`, `admins` are write-locked in Firebase.
- All data lives under `shipuwp/` — the shared project's root nodes are never touched.

## Test

```bash
python -m pytest tests/
```
