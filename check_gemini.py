#!/usr/bin/env python3
"""Diagnostic script to verify Gemini API connectivity, key validity, and available models."""
from __future__ import annotations

import os
from pathlib import Path
import requests

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def main():
    load_dotenv(Path(__file__).parent / ".env")
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("ERROR: GEMINI_API_KEY not found in environment or .env file.")
        return

    print("Checking connection to Google AI Studio...")
    base = "https://generativelanguage.googleapis.com/v1beta"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    # 1. List models
    try:
        r = requests.get(f"{base}/models", headers=headers, timeout=20)
    except Exception as e:
        print(f"Cannot reach Google: {e}")
        print("-> Network problem. Try a different network, phone hotspot, or disable VPN.")
        return

    if r.status_code in (401, 403):
        print(f"Key rejected (HTTP {r.status_code}): {r.text[:200]}")
        print("-> The API key is invalid. Create a new one at aistudio.google.com/apikey.")
        return
    elif r.status_code != 200:
        print(f"Error listing models (HTTP {r.status_code}): {r.text[:200]}")
        return

    models_data = r.json().get("models", [])
    model_names = [m["name"].replace("models/", "") for m in models_data if "generateContent" in m.get("supportedGenerationMethods", [])]
    print(f"Success! Found {len(model_names)} available text generation models for your key.")
    
    # Pick a suitable model
    chosen = None
    candidates = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
    for c in candidates:
        if c in model_names:
            chosen = c
            break
    if not chosen and model_names:
        chosen = model_names[0]

    if not chosen:
        print("No generation models found available for this key.")
        return

    print(f"Testing text generation with model: {chosen} ...")
    test_url = f"{base}/models/{chosen}:generateContent"
    payload = {"contents": [{"parts": [{"text": "Reply with the exact word: Works"}]}]}
    
    try:
        r2 = requests.post(test_url, headers=headers, json=payload, timeout=30)
        if r2.status_code == 200:
            resp_json = r2.json()
            try:
                text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                print(f"Model response: {text}")
                print(f"\nWorks! Add this line to your .env file:\nGEMINI_MODEL={chosen}")
            except Exception:
                print(f"Works! (Raw response received). Add this line to your .env file:\nGEMINI_MODEL={chosen}")
        else:
            print(f"Generation test failed (HTTP {r2.status_code}): {r2.text[:300]}")
    except Exception as e:
        print(f"Generation request timed out or failed: {e}")

if __name__ == "__main__":
    main()