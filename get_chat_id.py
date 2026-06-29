#!/usr/bin/env python3
"""텔레그램 chat_id 확인 도우미.

사용법:
  1) 텔레그램에서 내가 만든 봇과 대화를 시작하고 아무 메시지나 한 번 보낸다.
  2) python get_chat_id.py <BOT_TOKEN>
     (또는 TELEGRAM_BOT_TOKEN 환경변수를 설정한 뒤 인자 없이 실행)
  3) 출력된 chat id 를 GitHub Secret TELEGRAM_CHAT_ID 에 넣는다.
"""
import os
import sys

import requests


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if len(sys.argv) > 1:
        token = sys.argv[1]
    if not token:
        print("사용법: python get_chat_id.py <BOT_TOKEN>")
        sys.exit(1)

    r = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates", timeout=30
    )
    data = r.json()
    if not data.get("ok"):
        print(f"오류: {data}")
        sys.exit(1)

    results = data.get("result", [])
    if not results:
        print("받은 업데이트가 없습니다. 먼저 봇에게 메시지를 한 번 보낸 뒤 다시 실행하세요.")
        return

    seen = {}
    for upd in results:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id") is not None:
            seen[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name", "")

    print("발견된 chat id:")
    for cid, name in seen.items():
        print(f"  {cid}    ({name})")


if __name__ == "__main__":
    main()
