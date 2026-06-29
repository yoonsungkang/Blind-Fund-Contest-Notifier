#!/usr/bin/env python3
"""산업은행(KDB) 공지사항 실시간 알리미.

공지사항 게시판(CHBIPR23N00.act)을 읽어 새 글을 감지하고,
지정한 키워드가 제목에 포함된 경우에만 텔레그램으로 알림을 보낸다.
마지막으로 확인한 글 번호는 state.json에 저장해 다음 실행과 비교한다.
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
DEFAULT_BOARD = "https://www.kdb.co.kr/CHBIPR23N00.act?_mnuId=IHIHIR0087"


def load_json(path, default):
    # utf-8-sig: PowerShell 등이 붙이는 BOM이 있어도 안전하게 읽는다.
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    return default


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def keyword_matches(title, keyword):
    """keyword의 모든 토큰이 title에 들어 있으면 True.

    - 영문/숫자 토큰(PE, PEF, GP 등)은 단어 경계로 매칭해 'Paperless' 같은
      단어 안에 우연히 포함되는 오탐을 막는다.
    - 한글 토큰은 '띄어쓰기를 무시하고' 부분 문자열로 매칭한다.
      예) 키워드 '위탁운용사선정' 또는 '위탁 운용사 선정' 모두
          제목 '위탁운용사 선정 공고'와 매칭된다.
    - 공백으로 나뉜 여러 토큰은 모두 존재해야 매칭(순서 무관).
      예) '위탁 운용사 선정' -> 위탁 AND 운용사 AND 선정
    """
    title_nospace = re.sub(r"\s+", "", title)
    tokens = keyword.split()
    if not tokens:
        return False
    for tok in tokens:
        if re.fullmatch(r"[A-Za-z0-9]+", tok):
            if not re.search(r"\b" + re.escape(tok) + r"\b", title, re.IGNORECASE):
                return False
        else:
            if tok not in title_nospace:
                return False
    return True


def matched_keywords(title, keywords):
    return [k for k in keywords if keyword_matches(title, k)]


def scrape_notices(board_url, timeout_ms=45000):
    """게시판 첫 페이지 공지 목록을 반환.

    각 항목: {num, category, title, date, files:[{name,url}]}
    """
    notices = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
        # 표의 첫 칸이 숫자인 행이 나타날 때까지 대기 (AJAX 로딩 완료 신호)
        page.wait_for_function(
            """() => {
                const rows = document.querySelectorAll('#tableList tbody tr');
                if (!rows.length) return false;
                const first = rows[0].querySelector('td');
                return first && /^\\d+$/.test(first.textContent.trim());
            }""",
            timeout=timeout_ms,
        )
        rows = page.query_selector_all("#tableList tbody tr")
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 5:
                continue
            num_text = cells[0].inner_text().strip()
            if not num_text.isdigit():
                continue
            title_el = cells[2].query_selector("a")
            raw_title = title_el.inner_text() if title_el else cells[2].inner_text()
            title = re.sub(r"\s+", " ", raw_title).strip()
            title = re.sub(r"\s*새글\s*$", "", title)  # 목록의 '새글' 배지 제거
            files = []
            for a in cells[3].query_selector_all("a[href]"):
                href = a.get_attribute("href")
                name = (a.get_attribute("title") or "첨부파일").strip()
                if href:
                    files.append({"name": name, "url": href})
            notices.append(
                {
                    "num": int(num_text),
                    "category": cells[1].inner_text().strip(),
                    "title": title,
                    "date": cells[4].inner_text().strip(),
                    "files": files,
                }
            )
        browser.close()
    return notices


def send_telegram(token, chat_id, notice, keywords_hit, board_url):
    title = html.escape(notice["title"])
    cat = html.escape(notice["category"])
    date = html.escape(notice["date"])
    lines = [
        f"🔔 <b>산업은행 공지</b>  ·  [{cat}]  {date}",
        "",
        f"<b>{title}</b>",
    ]
    if notice["files"]:
        parts = ", ".join(
            f'<a href="{html.escape(f["url"])}">{html.escape(f["name"])}</a>'
            for f in notice["files"]
        )
        lines.append(f"📎 {parts}")
    lines.append(f'🔗 <a href="{html.escape(board_url)}">공지사항 게시판 열기</a>')
    if keywords_hit and keywords_hit != ["(전체)"]:
        lines.append(f"🔑 키워드: {html.escape(', '.join(keywords_hit))}")
    text = "\n".join(lines)
    resp = requests.post(
        TELEGRAM_API.format(token=token),
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(
            "ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_json(CONFIG_PATH, {})
    board_url = config.get("board_url", DEFAULT_BOARD)
    keywords = config.get("keywords", [])
    categories = config.get("categories", [])  # 비어 있으면 전체 카테고리 허용

    state = load_json(STATE_PATH, {})
    last_seen = state.get("last_seen", 0)

    notices = []
    for attempt in range(3):
        try:
            notices = scrape_notices(board_url)
            break
        except Exception as e:  # noqa: BLE001 - 네트워크/렌더 일시 오류는 재시도
            print(f"수집 실패(시도 {attempt + 1}/3): {e}", file=sys.stderr)
            time.sleep(5)
    if not notices:
        print("ERROR: 공지 목록을 가져오지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    # --test: 키워드/상태와 무관하게 최신 공지 1건을 보내 파이프라인을 점검한다.
    if "--test" in sys.argv:
        latest = max(notices, key=lambda n: n["num"])
        send_telegram(token, chat_id, latest, ["(연결 테스트)"], board_url)
        print(f"테스트 알림 발송: #{latest['num']} {latest['title']}")
        print("(state.json은 변경하지 않았습니다)")
        return

    max_num = max(n["num"] for n in notices)

    # 최초 실행: 기준선만 잡고 알림은 보내지 않음 (과거 글 폭탄 방지)
    if last_seen == 0:
        save_state({"last_seen": max_num})
        print(f"최초 실행: 기준선 설정 last_seen={max_num} (알림 미발송)")
        return

    # 새 글 = 번호가 last_seen 보다 큰 것, 오래된 것부터 처리
    new_notices = sorted(
        [n for n in notices if n["num"] > last_seen], key=lambda x: x["num"]
    )
    if not new_notices:
        print(f"새 공지 없음 (last_seen={last_seen})")
        return

    if len(new_notices) == len(notices):
        print(
            "주의: 첫 페이지가 모두 새 글입니다. 폴링 간격 사이 누락 가능성 있음.",
            file=sys.stderr,
        )

    sent = 0
    for n in new_notices:
        if categories and n["category"] not in categories:
            continue
        hits = matched_keywords(n["title"], keywords) if keywords else ["(전체)"]
        if not hits:
            continue
        try:
            send_telegram(token, chat_id, n, hits, board_url)
            sent += 1
            print(f"알림 발송: #{n['num']} [{n['category']}] {n['title']} (키워드: {hits})")
            time.sleep(1)  # 텔레그램 rate limit 여유
        except Exception as e:  # noqa: BLE001
            print(f"텔레그램 발송 실패 #{n['num']}: {e}", file=sys.stderr)

    save_state({"last_seen": max_num})
    print(f"완료: 새 글 {len(new_notices)}건 중 {sent}건 알림 발송. last_seen -> {max_num}")


if __name__ == "__main__":
    main()
