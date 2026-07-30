#!/usr/bin/env python3
"""정책금융기관 공지 실시간 알리미 (텔레그램).

감시 대상:
  - KDB 산업은행 공지사항 (JS 렌더링 → Playwright)
  - 한국성장금융 출자사업공고 (정적 HTML, EUC-KR)
  - 한국수출입은행 공지/입찰 (정적 HTML)

각 사이트에서 새 글을 감지하고, 제목이 키워드와 맞으면 텔레그램으로 알린다.
사이트별 마지막으로 본 글 ID는 state.json에 저장한다.
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import urllib3

# 수출입은행은 인증서 체인 문제로 verify=False 사용 → 경고 억제
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

KDB_BOARD = "https://www.kdb.co.kr/CHBIPR23N00.act?_mnuId=IHIHIR0087"
KGROWTH_BOARD = "https://www.kgrowth.or.kr/notice.asp"
KGROWTH_BASE = "https://www.kgrowth.or.kr/"
EXIM_BOARD = "https://www.koreaexim.go.kr/HPHKBI039M01"
EXIM_BASE = "https://www.koreaexim.go.kr"


# --------------------------------------------------------------------------
# 공통 유틸
# --------------------------------------------------------------------------
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


def clean_title(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*새글\s*$", "", text)  # 목록의 '새글' 배지 제거
    return text


def keyword_matches(title, keyword):
    """keyword의 모든 토큰이 title에 들어 있으면 True.

    - 영문/숫자 토큰(PE, PEF, GP 등)은 단어 경계로 매칭(‘Paperless’ 오탐 방지).
    - 한글 토큰은 '띄어쓰기를 무시하고' 부분 문자열로 매칭.
    - 공백으로 나뉜 여러 토큰은 모두 존재해야 매칭(순서 무관).
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


# --------------------------------------------------------------------------
# 사이트별 수집기  ->  [{uid:int, category, title, date, url, files:[{name,url}]}]
# --------------------------------------------------------------------------
def scrape_kdb(url=KDB_BOARD, timeout_ms=45000, pages=1):
    """KDB 산업은행: JS 렌더링이라 Playwright로 표를 읽는다.

    pages>1 이면 하단 페이저(#pagination a.page-link)를 클릭해 다음 쪽을 읽는다.
    한 쪽 = 10건 ≈ 7일치라(실측) 기간 조회(PEF 트랙)는 여러 쪽이 필요하다.
    알리미 본체는 기본값 1쪽 그대로다.
    """
    from playwright.sync_api import sync_playwright

    wait_rows = """() => {
        const rows = document.querySelectorAll('#tableList tbody tr');
        if (!rows.length) return false;
        const first = rows[0].querySelector('td');
        return first && /^\\d+$/.test(first.textContent.trim());
    }"""
    notices = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_function(wait_rows, timeout=timeout_ms)

        def read_rows():
            for row in page.query_selector_all("#tableList tbody tr"):
                cells = row.query_selector_all("td")
                if len(cells) < 5:
                    continue
                num_text = cells[0].inner_text().strip()
                if not num_text.isdigit():
                    continue
                title_el = cells[2].query_selector("a")
                raw = title_el.inner_text() if title_el else cells[2].inner_text()
                files = []
                for a in cells[3].query_selector_all("a[href]"):
                    href = a.get_attribute("href")
                    name = (a.get_attribute("title") or "첨부파일").strip()
                    if href:
                        files.append({"name": name, "url": href})
                notices.append(
                    {
                        "uid": int(num_text),
                        "category": cells[1].inner_text().strip(),
                        "title": clean_title(raw),
                        "date": cells[4].inner_text().strip(),
                        "url": url,
                        "files": files,
                    }
                )

        read_rows()
        for pno in range(2, pages + 1):
            link = page.query_selector(
                f"#pagination li.page-item a.page-link:text-is('{pno}')"
            )
            if not link:  # 게시글이 적어 페이저에 그 쪽이 없다
                break
            prev_top = page.eval_on_selector(
                "#tableList tbody tr td", "el => el.textContent.trim()"
            )
            link.click()
            page.wait_for_function(
                """(prev) => {
                    const rows = document.querySelectorAll('#tableList tbody tr');
                    if (!rows.length) return false;
                    const first = rows[0].querySelector('td');
                    return first && /^\\d+$/.test(first.textContent.trim())
                           && first.textContent.trim() !== prev;
                }""",
                arg=prev_top,
                timeout=timeout_ms,
            )
            read_rows()
        browser.close()
    return notices


def scrape_kgrowth(url=KGROWTH_BOARD, pages=1):
    """한국성장금융 출자사업공고: 정적 HTML(EUC-KR).

    pages>1 이면 ?page=N 으로 다음 쪽도 읽는다(실측: 2쪽 idx=1087~1078).
    상단 고정공지가 매 쪽 반복되지만 checked() 의 uid 중복 제거가 걸러낸다.
    """
    from bs4 import BeautifulSoup

    notices = []
    for pno in range(1, pages + 1):
        resp = requests.get(url, params={"page": pno} if pno > 1 else None,
                            headers=HEADERS, timeout=30)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        notices.extend(_parse_kgrowth(soup))
    return notices


def _parse_kgrowth(soup):
    notices = []
    for a in soup.select('a[href*="notice_view.asp"]'):
        href = a.get("href", "")
        m = re.search(r"idx=(\d+)", href)
        if not m:
            continue
        uid = int(m.group(1))
        title = clean_title(a.get_text(" ", strip=True))
        if not title:
            continue
        # 같은 행(tr)에서 날짜(YYYY-MM-DD)를 찾는다.
        date = ""
        tr = a.find_parent("tr")
        if tr:
            dm = re.search(r"\d{4}-\d{2}-\d{2}", tr.get_text(" ", strip=True))
            if dm:
                date = dm.group(0)
        detail = KGROWTH_BASE + href.lstrip("/")
        notices.append(
            {
                "uid": uid,
                "category": "출자사업공고",
                "title": title,
                "date": date,
                "url": detail,
                "files": [],
            }
        )
    return notices


def scrape_koreaexim(url=EXIM_BOARD, pages=1):
    """한국수출입은행 공지/입찰: 정적 HTML.

    pages>1 이면 ?curPage=N 으로 다음 쪽도 읽는다(실측: 페이저가 ?curPage=N 링크).

    참고: koreaexim 서버가 중간 인증서 체인을 완전히 내려주지 않아
    GitHub Actions(Ubuntu) 러너에서 CERTIFICATE_VERIFY_FAILED가 발생.
    공지 목록 크롤링 용도이므로 이 요청에 한해 verify=False로 우회한다.
    또한 legacy TLS renegotiation을 요구해(OpenSSL 3.x 기본 거부 → ConnectionReset)
    OP_LEGACY_SERVER_CONNECT 컨텍스트의 세션으로 요청한다.
    """
    import ssl
    from bs4 import BeautifulSoup
    from requests.adapters import HTTPAdapter

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT

    class _LegacyAdapter(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["ssl_context"] = ctx
            return super().init_poolmanager(*a, **kw)

    sess = requests.Session()
    sess.mount("https://", _LegacyAdapter())
    notices = []
    for pno in range(1, pages + 1):
        resp = sess.get(url, params={"curPage": pno} if pno > 1 else None,
                        headers=HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        notices.extend(_parse_koreaexim(soup))
    return notices


def _parse_koreaexim(soup):
    notices = []
    for item in soup.select("div.notice-list-item"):
        subj = item.select_one("span.subject a[href]")
        if not subj:
            continue
        href = subj.get("href", "")
        m = re.search(r"/HPHKBI039M01/(\d+)", href)
        if not m:
            continue
        uid = int(m.group(1))
        title = clean_title(subj.get_text(" ", strip=True))
        category = ""
        date = ""
        for sp in item.find_all("span"):
            t = (sp.get("title") or "").strip()
            if t == "카테고리":
                category = sp.get_text(" ", strip=True)
            elif t == "작성일":
                date = sp.get_text(" ", strip=True)
        files = []
        for fa in item.select("span[title='첨부파일'] a[href]"):
            files.append({"name": "첨부파일", "url": EXIM_BASE + fa.get("href")})
        detail = href if href.startswith("http") else EXIM_BASE + href
        notices.append(
            {
                "uid": uid,
                "category": category or "공지",
                "title": title,
                "date": date,
                "url": detail,
                "files": files,
            }
        )
    return notices


def checked(scrape_fn):
    """수집기 공통 후처리. SOURCES 를 쓰는 모든 호출자(알리미·PEF 트랙)가 여기를 지난다.

    - uid 중복 제거: 성장금융은 상단 고정공지가 일반목록에도 같이 나와 같은 글이 두 번
      들어온다(2026-07 기준 idx=1088). 그대로 두면 텔레그램 2회 발송 / PEF 이중 기입.
    - 0건이면 예외: 세 게시판 모두 상시 게시글이 있어 정상적으로 0건이 될 수 없다.
      셀렉터 미스·차단을 '새 글 없음'으로 삼키지 않도록 실패로 올린다.
    """

    def wrapped(*args, **kwargs):
        seen, uniq = set(), []
        for n in scrape_fn(*args, **kwargs):
            if n["uid"] in seen:
                continue
            seen.add(n["uid"])
            uniq.append(n)
        if not uniq:
            raise RuntimeError(f"{scrape_fn.__name__}: 목록 0건 - 사이트 구조 변경/차단 의심")
        return uniq

    wrapped.__name__ = scrape_fn.__name__
    return wrapped


SOURCES = [
    {"key": "kdb", "name": "산업은행", "scrape": checked(scrape_kdb)},
    {"key": "kgrowth", "name": "한국성장금융", "scrape": checked(scrape_kgrowth)},
    {"key": "koreaexim", "name": "수출입은행", "scrape": checked(scrape_koreaexim)},
]


# --------------------------------------------------------------------------
# 텔레그램
# --------------------------------------------------------------------------
def send_telegram(token, chat_id, source_name, notice, keywords_hit):
    title = html.escape(notice["title"])
    cat = html.escape(notice.get("category", ""))
    date = html.escape(notice.get("date", ""))
    head = f"🔔 <b>{html.escape(source_name)}</b>"
    if cat:
        head += f"  ·  [{cat}]"
    if date:
        head += f"  {date}"
    lines = [head, "", f"<b>{title}</b>"]
    if notice.get("files"):
        parts = ", ".join(
            f'<a href="{html.escape(f["url"])}">{html.escape(f["name"])}</a>'
            for f in notice["files"]
        )
        lines.append(f"📎 {parts}")
    if notice.get("url"):
        lines.append(f'🔗 <a href="{html.escape(notice["url"])}">공고 보기</a>')
    if keywords_hit and keywords_hit != ["(전체)"]:
        lines.append(f"🔑 키워드: {html.escape(', '.join(keywords_hit))}")
    resp = requests.post(
        TELEGRAM_API.format(token=token),
        data={
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"텔레그램 HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"텔레그램 응답 실패: {resp.text[:300]}")
    return data


def scrape_with_retry(scrape_fn, tries=3):
    for attempt in range(tries):
        try:
            return scrape_fn()
        except Exception as e:  # noqa: BLE001
            print(f"  수집 실패(시도 {attempt + 1}/{tries}): {e}", file=sys.stderr)
            time.sleep(5)
    return None


# --------------------------------------------------------------------------
# 메인
# --------------------------------------------------------------------------
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    config = load_json(CONFIG_PATH, {})
    keywords = config.get("keywords", [])
    test_mode = "--test" in sys.argv

    state = load_json(STATE_PATH, {})
    # 구버전 state({"last_seen": N})를 kdb로 이관
    if "last_seen" in state and "kdb" not in state:
        state["kdb"] = state.pop("last_seen")

    total_sent = 0
    send_errors = 0
    for src in SOURCES:
        key, name = src["key"], src["name"]
        print(f"[{name}] 수집 중...")
        notices = scrape_with_retry(src["scrape"])
        if not notices:
            print(f"[{name}] 목록을 가져오지 못함 - 건너뜀", file=sys.stderr)
            continue

        max_uid = max(n["uid"] for n in notices)

        if test_mode:
            latest = max(notices, key=lambda n: n["uid"])
            send_telegram(token, chat_id, name, latest, ["(연결 테스트)"])
            print(f"[{name}] 테스트 발송: #{latest['uid']} {latest['title']}")
            continue

        last_seen = state.get(key, 0)
        if last_seen == 0:
            state[key] = max_uid
            print(f"[{name}] 최초 실행: 기준선 {max_uid} (알림 미발송)")
            continue

        new_items = sorted([n for n in notices if n["uid"] > last_seen], key=lambda x: x["uid"])
        if not new_items:
            print(f"[{name}] 새 글 없음 (last_seen={last_seen})")
            continue

        sent = 0
        highest_ok = last_seen  # 안전하게 저장 가능한 최대 uid (발송 성공/불일치까지만)
        for n in new_items:
            hits = matched_keywords(n["title"], keywords) if keywords else ["(전체)"]
            if not hits:
                highest_ok = n["uid"]  # 키워드 불일치 = 처리 완료, 통과 가능
                continue
            try:
                send_telegram(token, chat_id, name, n, hits)
                sent += 1
                highest_ok = n["uid"]
                print(f"[{name}] 알림: #{n['uid']} {n['title']} (키워드: {hits})")
                time.sleep(1)
            except Exception as e:  # noqa: BLE001
                print(f"[{name}] 발송 실패 #{n['uid']}: {e}", file=sys.stderr)
                send_errors += 1
                break  # 진행 멈춤 - state를 넘기지 않아 다음 실행에서 재시도(누락 방지)

        state[key] = highest_ok
        total_sent += sent
        print(f"[{name}] 새 글 {len(new_items)}건 중 {sent}건 발송, last_seen -> {highest_ok}")

    if not test_mode:
        save_state(state)
        print(f"완료: 총 {total_sent}건 발송. state={state}")
        if send_errors:
            print(
                f"ERROR: 텔레그램 발송 실패 {send_errors}건 - 토큰/chat_id(Secret) 확인 필요. "
                "다음 실행에서 재시도합니다.",
                file=sys.stderr,
            )
            sys.exit(1)


def demo():
    """python check_notices.py --selfcheck  (네트워크·텔레그램 접촉 없음)"""
    a, b = {"uid": 1088, "title": "고정공지"}, {"uid": 1097, "title": "일반"}
    assert checked(lambda: [a, b, a])() == [a, b]          # 중복 uid 1건으로
    assert checked(lambda: [a])() == [a]
    try:
        checked(lambda: [])()
    except RuntimeError:
        pass
    else:
        raise AssertionError("0건인데 예외가 안 났다")
    assert checked(lambda url="x": [{"uid": 1, "u": url}])(url="y")[0]["u"] == "y"  # 인자 통과
    print("ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        demo()
    else:
        main()
