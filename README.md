# ShiPu WP — Termux Tool

AI-powered WhatsApp assistant that runs in Termux on Android.

## Features

- **Polished terminal UI** — responsive, animated, mobile-friendly
- **Firebase auth** — username/password accounts shared with the website
- **OpenRouter AI** — real replies with model failover and quota handling
- **WhatsApp listener bridge** — NDJSON event bus to an Android `NotificationListenerService`
- **Duplicate suppression** — never answers the same message twice
- **Server-authoritative billing** — local state can never grant Pro

## Install

```bash
pkg install python
pip install -r requirements.txt
cp .env.example .env   # fill in OPENROUTER_API_KEY
python start.py
```

## Configure

| Variable | Purpose |
| --- | --- |
| `SHIPU_API_URL` | Render backend URL. Empty = local-only mode. |
| `OPENROUTER_API_KEY` | AI key from https://openrouter.ai/keys |
| `OPENROUTER_MODEL` | Primary model |
| `OPENROUTER_FALLBACK_MODELS` | Comma-separated failover models |
| `SHIPU_FREE_DAILY_REPLIES` | Free-tier daily cap |
| `SHIPU_PRO_PRICE_CENTS` | Pro price in cents |
| `SHIPU_PRO_DURATION_DAYS` | Pro duration |

## Android listener

See `android/` in the main repo for the `NotificationListenerService` that
writes inbound messages to the bridge directory and types replies back.

Set `SHIPU_BRIDGE_DIR` on both sides to the same path.

## Security

- Server-only secrets (`FIREBASE_ADMIN_CREDENTIALS`, `PAYMENT_SECRET`,
  `WEBHOOK_SECRET`) are refused at startup.
- `plan`, `subscription`, `payments`, and `admins` are write-locked in
  Firebase rules — only the Admin SDK can change them.
- All ShiPu data lives under the `shipuwp/` namespace; the root `users/` and
  `bot/` nodes belong to another app and are never touched.

## Test

```bash
python -m pytest tests/
```
