#!/usr/bin/env python3
"""텔레그램 연결 테스트.

봇 토큰 / chat_id 가 제대로 설정됐는지, 실제로 메시지가 도착하는지
키워드·게시판과 무관하게 확인한다. API 응답 코드도 그대로 출력한다.

사용법 (PowerShell):
  $env:TELEGRAM_BOT_TOKEN="<봇토큰>"
  $env:TELEGRAM_CHAT_ID="6165322879"
  python send_test.py
"""
import os
import sys

import requests


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수를 먼저 설정하세요.")
        sys.exit(1)

    text = (
        "✅ <b>산업은행 공지 알리미</b> 연결 테스트입니다.\n"
        "이 메시지가 보이면 토큰과 chat_id 설정이 정상입니다."
    )
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    print("HTTP", resp.status_code)
    print(resp.text)
    if resp.ok and resp.json().get("ok"):
        print("\n성공: 텔레그램으로 테스트 메시지를 보냈습니다. 앱을 확인하세요.")
    else:
        print("\n실패: 위 응답을 확인하세요.")
        print("  - 401 Unauthorized      -> 봇 토큰이 틀림")
        print("  - 400 chat not found    -> chat_id가 틀렸거나, 봇에게 먼저 메시지를 안 보냄")


if __name__ == "__main__":
    main()
