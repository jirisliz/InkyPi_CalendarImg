#!/usr/bin/env python3
"""
get_refresh_token.py — One-time helper to obtain a Google OAuth2 refresh token
for the Google Tasks API.

Run this ONCE on any machine (laptop, desktop, the Pi itself) that has a browser
or can copy-paste a URL. You do NOT need to re-run it unless you revoke access.

Prerequisites
─────────────
1. Go to https://console.cloud.google.com
2. Create a project (or select an existing one)
3. Enable the "Tasks API":
   APIs & Services → Library → search "Tasks API" → Enable
4. Create OAuth2 credentials:
   APIs & Services → Credentials → Create Credentials → OAuth client ID
   → Application type: Desktop app → name it "InkyPi" → Create
5. Download the JSON file — open it and note:
   - client_id   (looks like: 123456789-abc.apps.googleusercontent.com)
   - client_secret (looks like: GOCSPX-...)
6. Run this script:
   python3 get_refresh_token.py

Usage
─────
  python3 get_refresh_token.py
  # or pass credentials directly:
  python3 get_refresh_token.py --client-id YOUR_ID --client-secret YOUR_SECRET
"""

import sys
import json
import urllib.parse
import urllib.request
import argparse

AUTH_URL    = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL   = "https://oauth2.googleapis.com/token"
SCOPE       = "https://www.googleapis.com/auth/tasks.readonly"
REDIRECT    = "urn:ietf:wg:oauth:2.0:oob"   # copy-paste flow, no local server needed


def get_refresh_token(client_id: str, client_secret: str) -> None:
    # Step 1 — Build the authorisation URL
    params = {
        "client_id":     client_id,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "scope":         SCOPE,
        "access_type":   "offline",
        "prompt":        "consent",   # force refresh_token to be returned
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("\n" + "="*60)
    print("STEP 1 — Open this URL in your browser and sign in:")
    print("="*60)
    print(url)
    print()

    # Step 2 — User pastes the code
    code = input("STEP 2 — Paste the authorisation code shown by Google: ").strip()
    if not code:
        print("No code entered. Aborting.")
        sys.exit(1)

    # Step 3 — Exchange code for tokens
    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  REDIRECT,
        "grant_type":    "authorization_code",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"\nError exchanging code: {e.code} {e.reason}")
        print(body)
        sys.exit(1)

    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        print("\nNo refresh_token in response. Make sure you used prompt=consent.")
        print("Full response:", json.dumps(tokens, indent=2))
        sys.exit(1)

    print("\n" + "="*60)
    print("SUCCESS — copy these values into the InkyPi plugin settings:")
    print("="*60)
    print(f"  Client ID      : {client_id}")
    print(f"  Client Secret  : {client_secret}")
    print(f"  Refresh Token  : {refresh_token}")
    print()
    print("The access_token expires every hour but the refresh_token lasts")
    print("indefinitely (until you revoke it). The plugin refreshes it automatically.")
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Get Google OAuth2 refresh token for Tasks API")
    parser.add_argument("--client-id",     default="", help="OAuth2 client ID")
    parser.add_argument("--client-secret", default="", help="OAuth2 client secret")
    args = parser.parse_args()

    client_id     = args.client_id     or input("Client ID     : ").strip()
    client_secret = args.client_secret or input("Client Secret : ").strip()

    if not client_id or not client_secret:
        print("Both client_id and client_secret are required.")
        sys.exit(1)

    get_refresh_token(client_id, client_secret)


if __name__ == "__main__":
    main()
