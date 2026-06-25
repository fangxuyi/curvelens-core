#!/usr/bin/env python
"""
E*TRADE OAuth 1.0a one-time authorization script.

Run this once to exchange your consumer key/secret for access tokens.
Tokens are saved to ~/.ccvm/etrade_tokens.json and auto-loaded by
ETradeOptionsCollector on subsequent runs.

Tokens expire at midnight ET — rerun this script after that if needed.

Usage:
    python scripts/auth_etrade.py

Prerequisites:
    Create an app at https://us.etrade.com/etx/hw/accountspref#/devpref
    Then set env vars:
        export ETRADE_CONSUMER_KEY=your_key
        export ETRADE_CONSUMER_SECRET=your_secret
    Or pass them as arguments:
        python scripts/auth_etrade.py --key YOUR_KEY --secret YOUR_SECRET
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import uuid
import webbrowser
from pathlib import Path

_TOKEN_FILE = Path.home() / ".ccvm" / "etrade_tokens.json"

_LIVE = "https://api.etrade.com"
_SANDBOX = "https://apisb.etrade.com"
_AUTH_URL = "https://us.etrade.com/e/t/etws/authorize?key={key}&token={token}"


def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _oauth1_header(url, method, consumer_key, consumer_secret, token="", token_secret="",
                   extra_params=None, callback="oob") -> str:
    params: dict[str, str] = {
        "oauth_callback": callback,
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        params["oauth_token"] = token

    all_params: dict[str, str] = {}
    all_params.update(extra_params or {})
    all_params.update(params)
    encoded = sorted((_pct(k), _pct(v)) for k, v in all_params.items())
    param_string = "&".join(f"{k}={v}" for k, v in encoded)
    sig_base = f"{method.upper()}&{_pct(url)}&{_pct(param_string)}"
    signing_key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    digest = hmac.new(signing_key.encode(), sig_base.encode(), hashlib.sha1).digest()
    params["oauth_signature"] = base64.b64encode(digest).decode()
    parts = ", ".join(f'{k}="{_pct(v)}"' for k, v in sorted(params.items()))
    return f"OAuth {parts}"


def main() -> None:
    parser = argparse.ArgumentParser(description="E*TRADE OAuth 1.0a authorization")
    parser.add_argument("--key", help="Consumer key (or set ETRADE_CONSUMER_KEY env var)")
    parser.add_argument("--secret", help="Consumer secret (or set ETRADE_CONSUMER_SECRET env var)")
    parser.add_argument("--sandbox", action="store_true", help="Use sandbox environment")
    args = parser.parse_args()

    consumer_key = args.key or os.environ.get("ETRADE_CONSUMER_KEY", "")
    consumer_secret = args.secret or os.environ.get("ETRADE_CONSUMER_SECRET", "")

    if not consumer_key or not consumer_secret:
        print("ERROR: provide --key and --secret or set ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET")
        raise SystemExit(1)

    base = _SANDBOX if args.sandbox else _LIVE

    # ---- Step 1: get request token ----
    import httpx

    req_token_url = f"{base}/v1/oauth/request_token"
    auth = _oauth1_header(req_token_url, "GET", consumer_key, consumer_secret, callback="oob")
    resp = httpx.get(req_token_url, headers={"Authorization": auth, "Accept": "application/json"})
    if resp.status_code != 200:
        print(f"ERROR getting request token: HTTP {resp.status_code} — {resp.text}")
        raise SystemExit(1)

    req_params = dict(urllib.parse.parse_qsl(resp.text))
    req_token = req_params.get("oauth_token", "")
    req_token_secret = req_params.get("oauth_token_secret", "")
    if not req_token:
        print(f"ERROR: no oauth_token in response: {resp.text}")
        raise SystemExit(1)

    # ---- Step 2: user authorizes ----
    auth_url = _AUTH_URL.format(key=consumer_key, token=req_token)
    print(f"\nOpening authorization URL in your browser:\n  {auth_url}\n")
    print("After clicking 'Accept', you will see a verifier code.")
    webbrowser.open(auth_url)
    verifier = input("Paste the verifier code here: ").strip()
    if not verifier:
        print("ERROR: no verifier provided")
        raise SystemExit(1)

    # ---- Step 3: exchange for access token ----
    access_token_url = f"{base}/v1/oauth/access_token"
    auth = _oauth1_header(
        access_token_url, "GET", consumer_key, consumer_secret,
        token=req_token, token_secret=req_token_secret,
        extra_params={"oauth_verifier": verifier},
    )
    resp = httpx.get(access_token_url, headers={"Authorization": auth})
    if resp.status_code != 200:
        print(f"ERROR getting access token: HTTP {resp.status_code} — {resp.text}")
        raise SystemExit(1)

    acc_params = dict(urllib.parse.parse_qsl(resp.text))
    access_token = acc_params.get("oauth_token", "")
    access_token_secret = acc_params.get("oauth_token_secret", "")
    if not access_token:
        print(f"ERROR: no access token in response: {resp.text}")
        raise SystemExit(1)

    # ---- Save tokens ----
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tokens = {
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "access_token": access_token,
        "access_token_secret": access_token_secret,
        "authorized_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": "sandbox" if args.sandbox else "live",
    }
    _TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    print(f"\nTokens saved to {_TOKEN_FILE}")
    print("You can now run:  python scripts/collect_day.py --date $(date +%F) --source etrade_options")
    print("\nTokens expire at midnight ET — rerun this script tomorrow if needed.")


if __name__ == "__main__":
    main()
