#!/usr/bin/env python3
"""정책금융기관 공지 실시간 알리미 (텔레그램).

감시 대상: 정책금융기관·연기금·공제회·민간 자산운용사 공식 게시판과 KVCA 보완 공고.

각 사이트에서 새 글을 감지하고, 제목이 키워드와 맞으면 텔레그램으로 알린다.
사이트별 마지막으로 본 글 ID는 state.json에 저장한다.
"""
import html
import json
import os
import re
import sys
import time
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

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
KVIC_BOARD = "https://www.kvic.or.kr/notice/kvic-notice/investment-business-notice"
NPS_BOARD = "https://fund.nps.or.kr/impa/dlnginstslctnpbanclist/getOHEF0017M0.do"
NPS_NEWS_BOARD = "https://www.nps.or.kr/pnsgdnc/nscvrgdata/getOHAE0002M0List.do?menuId=MN24000898"
KTCU_BOARD = "https://www.ktcu.or.kr/PPW-CSB-000101"
POBA_BOARD = "https://www.poba.or.kr/bbs/selectNttList?sechBbsSeq=10"
SEMA_BOARD = "https://www.sema.or.kr/sema/bbs/B0000022/list.do?menuNo=200017&optn1=S"
TP_BOARD = "https://www.tp.or.kr/tp-kr/bbs/i-151/list.do"
GEPS_BOARD = "https://www.geps.or.kr/notiCommunication_notice"
PMAA_BOARD = "https://www.pmaa.or.kr/www/1461126378922/bbs.do"
KBIZ_BOARD = "https://www.kbiz.or.kr/ko/contents/bbs/list.do?mnSeq=211"
CW_BOARD = "https://www.cw.or.kr/board.do?boardConfigNo=29&menuNo=248&boardCategoryNo=66"
KVCA_BOARD = "https://www.kvca.or.kr/Program/invest/list.html?a_cd=8&a_gb=board&a_item=0&sm=2_2_2"
SHINHAN_BOARD = "https://www.shinhanfund.com/ko/mobile/board/notice"
WOORI_BOARD = "https://www.wooriam.kr/customer/notice-list"
SAMSUNG_BOARD = "https://www.samsungfund.com/fund/lounge/notice.do"


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
    text = re.sub(r"\s*(새글|NEW)\s*$", "", text, flags=re.IGNORECASE)
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


def fetch_soup(url):
    from bs4 import BeautifulSoup

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    if (resp.encoding or "").lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return BeautifulSoup(resp.text, "html.parser")


def row_date(element):
    row = element.find_parent(["tr", "li"]) or element.parent
    match = re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", row.get_text(" ", strip=True))
    return match.group(0) if match else ""


def date_key(value):
    return re.sub(r"\D", "", value)[:8]


def scrape_static_board(
    url, selector, id_pattern, category, detail_url=None, title_selector=None
):
    """링크/onclick에 숫자 ID가 있는 정적 게시판 공통 수집기."""
    notices = []
    for element in fetch_soup(url).select(selector):
        marker = " ".join(
            filter(None, [element.get("href", ""), element.get("onclick", "")])
        )
        match = re.search(id_pattern, marker)
        title_element = element.select_one(title_selector) if title_selector else element
        title = clean_title(title_element.get_text(" ", strip=True)) if title_element else ""
        if not match or not title:
            continue
        detail = (
            detail_url(match, element)
            if detail_url
            else urljoin(url, element.get("href", ""))
        )
        notices.append(
            {
                "uid": int(match.group(1)),
                "category": category,
                "title": title,
                "date": row_date(element),
                "url": detail,
                "files": [],
            }
        )
    return notices


def pef_title(title):
    """제목만으로 기관전용 PEF가 명백히 가능한 공고인지 보수적으로 판정."""
    strong = re.search(
        r"(?<![A-Za-z])PEF?(?![A-Za-z])|기관전용\s*사모|사모투자|"
        r"바이아웃|buyout",
        title,
        re.IGNORECASE,
    )
    if strong:
        return True
    if re.search(r"(?<![A-Za-z])VC(?![A-Za-z])|벤처", title, re.IGNORECASE):
        return False
    return "블라인드" in title and bool(re.search(r"펀드|위탁운용|출자", title))


def vc_only(text):
    has_vc = bool(re.search(r"(?<![A-Za-z])VC(?![A-Za-z])|벤처", text, re.IGNORECASE))
    has_pe = bool(
        re.search(r"(?<![A-Za-z])PEF?(?![A-Za-z])|기관전용\s*사모", text, re.IGNORECASE)
    )
    return has_vc and not has_pe


def pef_notice(notice, _state=None):
    return pef_title(notice["title"])


def kvic_program_key(title):
    compact = re.sub(r"\s+", "", title)
    compact = re.sub(r"20\d{2}년", "", compact)
    return re.split(r"출자사업|선정공고|접수현황|서류심사|최종선정", compact, maxsplit=1)[0]


def kvic_pdf_allows_pef(text):
    """공고문의 '출자대상/신청가능조합형태' 표 안에 기관전용 PEF가 있는지 본다."""
    compact = re.sub(r"\s+", "", text)
    headings = [m.start() for m in re.finditer(r"출자대상|신청가능조합형태", compact)]
    if not headings:
        return None
    target = "기관전용사모집합투자기구"
    return any(target in compact[pos : pos + 1200] for pos in headings)


def document_text(content, name):
    """PDF/HWP 공고문을 표 내용까지 포함한 텍스트로 변환한다."""
    lower = name.lower()
    if content.startswith(b"%PDF") or lower.endswith(".pdf"):
        from pypdf import PdfReader

        return "\n".join(
            page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages
        )
    if content.startswith(b"\xd0\xcf\x11\xe0") or lower.endswith(".hwp"):
        from bs4 import BeautifulSoup
        from hwp5.hwp5html import HTMLTransform
        from hwp5.xmlmodel import Hwp5File

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "notice.hwp"
            path.write_bytes(content)
            output = BytesIO()
            with closing(Hwp5File(str(path))) as hwp:
                HTMLTransform().transform_hwp5_to_xhtml(hwp, output)
            return BeautifulSoup(output.getvalue(), "html.parser").get_text(
                " ", strip=True
            )
    return None


def kvic_document_allows_pef(notice):
    """KVIC 출자계획 PDF의 신청 가능 비히클을 확인하고 첨부 링크도 채운다."""
    from bs4 import BeautifulSoup
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(notice["url"], timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    pdfs = []
    files = []
    for anchor in soup.select('a[href*="fileDown"]'):
        href = urljoin(KVIC_BOARD, anchor.get("href", ""))
        parent = anchor.parent.get_text(" ", strip=True)
        name = re.sub(r"\s*(바로보기|내려받기)\s*", " ", parent).strip()
        if not href or any(f["url"] == href for f in files):
            continue
        files.append({"name": name or "첨부파일", "url": href})
        if ".pdf" in name.lower() and re.search(r"공고|계획", name):
            pdfs.append(href)
    notice["files"] = files

    decisions = []
    for pdf_url in pdfs[:2]:
        pdf = session.get(pdf_url, headers={"Referer": notice["url"]}, timeout=30)
        pdf.raise_for_status()
        if not pdf.content.startswith(b"%PDF"):
            continue
        try:
            text = document_text(pdf.content, "notice.pdf")
        except Exception as exc:  # noqa: BLE001
            print(f"  KVIC PDF 판독 실패: {exc}", file=sys.stderr)
            continue
        if text is None:
            continue
        decision = kvic_pdf_allows_pef(text)
        if decision is not None:
            decisions.append(decision)
    if True in decisions:
        return True
    return False if decisions else None


def kvic_pef_notice(notice, state):
    cache = state.setdefault("_kvic_pef_programs", {})
    key = kvic_program_key(notice["title"])
    is_plan = "출자계획" in notice.get("category", "")

    if pef_title(notice["title"]):
        if is_plan and key:
            cache[key] = True
        return True
    if not is_plan:
        return cache.get(key, False)

    decision = kvic_document_allows_pef(notice)
    if decision is not None and key:
        cache[key] = decision
    if decision is None:
        # ponytail: OCR/HWP-only 공고는 오탐 방지를 위해 보류; 실제 누락이 생기면 OCR을 추가한다.
        print(f"[한국벤처투자] PEF 판정 보류(PDF 텍스트 없음): {notice['title']}")
    return decision is True


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


def scrape_kvic(url=KVIC_BOARD):
    notices = []
    for anchor in fetch_soup(url).select('a[href*="board_view"]'):
        match = re.search(r"board_view\((\d+)\)", anchor.get("href", ""))
        row = anchor.find_parent("tr")
        if not match or not row:
            continue
        cells = row.find_all("td")
        category = cells[1].get_text(" ", strip=True).strip("[] ") if len(cells) > 1 else "출자사업"
        notices.append(
            {
                "uid": int(match.group(1)),
                "category": category,
                "title": clean_title(anchor.get_text(" ", strip=True)),
                "date": row_date(anchor),
                "url": f"{url}?id={match.group(1)}",
                "files": [],
            }
        )
    return notices


def scrape_nps(url=NPS_BOARD):
    return scrape_static_board(
        url,
        'a[href*="fnc_goBbsDetail"]',
        r"fnc_goBbsDetail\('ZZ(\d+)',\s*'([^']+)'",
        "거래기관 선정",
        lambda m, _e: (
            "https://fund.nps.or.kr/impa/dlnginstslctnpbancdtl/getOHEF0018M0.do"
            f"?pstId=ZZ{m.group(1)}&hmpgBbsCd={m.group(2)}"
        ),
    )


def nps_pef_notice(notice, _state=None):
    """NPS의 '국내 사모투자'가 실제로는 벤처펀드인 경우를 첨부 공고명으로 제외."""
    soup = fetch_soup(notice["url"])
    files = []
    file_names = []
    for item in soup.select("div.file-item"):
        name_el = item.select_one("p.a-file")
        down = item.select_one('a[href*="fncAtchFileDownload"]')
        if not name_el or not down:
            continue
        match = re.search(r"fncAtchFileDownload\('([^']+)',\s*'(\d+)'", down.get("href", ""))
        if not match:
            continue
        name = re.sub(r"\s*\([\d.]+\s*MB\)\s*$", "", clean_title(name_el.get_text(" ", strip=True)))
        file_names.append(name)
        files.append(
            {
                "name": name,
                "url": (
                    "https://fund.nps.or.kr/fileDown.do"
                    f"?atchFileId={match.group(1)}&atchFileSn={match.group(2)}"
                ),
            }
        )
    notice["files"] = files
    evidence = " ".join(file_names)
    if evidence and vc_only(evidence):
        return False
    return pef_title(notice["title"]) or pef_title(evidence)


def scrape_nps_news(url=NPS_NEWS_BOARD):
    return scrape_static_board(
        url,
        'a[href*="getOHAE0002M1.do"][href*="pstId=ZZ"]',
        r"pstId=ZZ(\d+)",
        "보도자료",
    )


def scrape_ktcu(url=KTCU_BOARD):
    return scrape_static_board(
        url,
        'a[href*="fn_view"]',
        r"fn_view\('(\d+)'\)",
        "공지",
        lambda m, _e: f"{url}/{m.group(1)}",
    )


def scrape_poba(url=POBA_BOARD):
    """행정공제회 페이지가 자체 렌더링에 쓰는 내장 JSON을 읽는다."""
    soup = fetch_soup(url)
    match = re.search(r"var bbsMap = (\{.*?\});", str(soup), re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group(1))["bbsDvo"]["nttDvoList"]
    return [
        {
            "uid": int(item["bbstSeq"]),
            "category": "공지",
            "title": clean_title(item["bbstSj"]),
            "date": item.get("rgstDt", ""),
            "url": (
                "https://www.poba.or.kr/bbs/selectNttDetail?sechBbsSeq=10"
                f"&sechBbstSeq={item['bbstSeq']}"
            ),
            "files": [],
        }
        for item in data
        if item.get("bbstSeq") and item.get("bbstSj")
    ]


def scrape_sema(url=SEMA_BOARD):
    return scrape_static_board(
        url,
        'a[href*="/sema/bbs/B0000022/view.do?nttId="]',
        r"nttId=(\d+)",
        "공지",
    )


def scrape_teachers_pension(url=TP_BOARD):
    return scrape_static_board(
        url,
        'a[href*="detail.do?ntt_sn="]',
        r"ntt_sn=(\d+)",
        "공지",
    )


def scrape_geps(url=GEPS_BOARD):
    return scrape_static_board(
        url,
        'a[href*="/notiCommunication_notice_center/"]',
        r"notice_center/(\d+)",
        "공지",
    )


def scrape_pmaa(url=PMAA_BOARD):
    return scrape_static_board(
        url,
        'a[onclick*="fn_view"]',
        r"fn_view\('(\d+)'\)",
        "공지",
        lambda m, _e: f"{url}?bbsIdx={m.group(1)}&type=view",
    )


def scrape_kbiz(url=KBIZ_BOARD):
    return scrape_static_board(
        url,
        'span[onclick*="goView"]',
        r"goView\((\d+),",
        "공지",
        lambda m, _e: (
            "https://www.kbiz.or.kr/ko/contents/bbs/view.do?mnSeq=211"
            f"&seq={m.group(1)}"
        ),
    )


def scrape_cw(url=CW_BOARD):
    return scrape_static_board(
        url,
        'a[href*="goView"]',
        r"goView\('29','248','view','(\d+)'",
        "선정공고(자산운용)",
        lambda m, _e: (
            "https://www.cw.or.kr/board.do?boardConfigNo=29&menuNo=248"
            f"&action=view&boardNo={m.group(1)}"
        ),
    )


def scrape_shinhan(url=SHINHAN_BOARD):
    return scrape_static_board(
        url,
        'a[href*="/ko/mobile/board/noticeView?no="]',
        r"no=(\d+)",
        "공지",
        title_selector=".tb-subj",
    )


def scrape_woori(url=WOORI_BOARD):
    rows = []
    for anchor in fetch_soup(url).select(
        'a[href*="goView"][href*="/customer/notice-view"]'
    ):
        match = re.search(
            r"goView\('[^']+',\s*'([^']+)'", anchor.get("href", "")
        )
        date = row_date(anchor)
        digits = re.sub(r"\D", "", date)
        if not match or len(digits) != 8:
            continue
        rows.append(
            {
                "category": "공지",
                "title": clean_title(anchor.get_text(" ", strip=True)),
                "date": date,
                "url": f"https://www.wooriam.kr/customer/notice-view/{match.group(1)}",
                "files": [],
            }
        )

    counts = {}
    # ponytail: 하루 게시물이 첫 화면(10건) 미만이라는 전제. 넘으면 문자열 cursor로 바꾼다.
    for notice in reversed(rows):
        day = re.sub(r"\D", "", notice["date"])
        counts[day] = counts.get(day, 0) + 1
        notice["uid"] = int(day) * 100 + counts[day]
    return rows


def scrape_samsung(url=SAMSUNG_BOARD):
    return scrape_static_board(
        url,
        'a[href*="notice-view.do?no="]',
        r"no=(\d+)",
        "공지",
        title_selector=".tit",
    )


def private_program_key(title):
    compact = re.sub(r"\s+", "", title)
    compact = re.sub(r"20\d{2}년(?:도)?", "", compact)
    compact = re.sub(r"\(?재공고\)?", "", compact)
    compact = re.sub(
        r"위탁운용사|(?<!투)자펀드|출자사업|선정계획|제안서|서류심사|최종|접수|선정|결과|공고|FAQ|게시",
        "",
        compact,
    )
    return re.sub(r"[^0-9A-Za-z가-힣]", "", compact)


def detail_documents_allow_pef(notice, selector):
    soup = fetch_soup(notice["url"])
    documents, files = [], []
    for anchor in soup.select(selector):
        name = clean_title(anchor.get_text(" ", strip=True))
        href = urljoin(notice["url"], anchor.get("href", ""))
        if not name or not href or any(item["url"] == href for item in files):
            continue
        files.append({"name": name, "url": href})
        if re.search(r"\.(?:pdf|hwp)\b", name, re.IGNORECASE):
            documents.append((name, href))
    notice["files"] = files

    decided = False
    for name, url in documents[:2]:
        resp = requests.get(
            url, headers={**HEADERS, "Referer": notice["url"]}, timeout=30
        )
        resp.raise_for_status()
        text = document_text(resp.content, name)
        if text:
            decision = kvic_pdf_allows_pef(text)
            if decision is True:
                return True
            decided = decided or decision is False
    return False if decided else None


def private_am_pef_notice(notice, state, cache_name, file_selector):
    cache = state.setdefault(cache_name, {})
    title = notice["title"]
    key = private_program_key(title)
    is_plan = not re.search(r"결과|FAQ", title) and bool(
        re.search(r"선정\s*계획|선정\s*공고|출자사업.*공고", title)
    )

    if pef_title(title):
        if is_plan and key:
            cache[key] = True
        return True
    if not is_plan:
        return cache.get(key, False)

    decision = detail_documents_allow_pef(notice, file_selector)
    if decision is not None and key:
        cache[key] = decision
    if decision is None:
        print(f"PEF 판정 보류(PDF/HWP 텍스트 없음): {title}", file=sys.stderr)
    return decision is True


def shinhan_pef_notice(notice, state):
    return private_am_pef_notice(
        notice, state, "_shinhan_pef_programs", "a.atc-link[href]"
    )


def woori_pef_notice(notice, state):
    return private_am_pef_notice(
        notice, state, "_woori_pef_programs", "a.layout-board-view__download[href]"
    )


def scrape_kvca(url=KVCA_BOARD):
    notices = []
    for row in fetch_soup(url).select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        anchor = cells[2].select_one('a[href*="po_no="]')
        if not anchor:
            continue
        match = re.search(r"po_no=(\d+)", anchor.get("href", ""))
        if not match:
            continue
        lp = clean_title(cells[1].get_text(" ", strip=True))
        notices.append(
            {
                "uid": int(match.group(1)),
                "category": f"출자공고 · {lp}",
                "title": clean_title(anchor.get_text(" ", strip=True)),
                "date": row_date(anchor),
                "url": urljoin(url, anchor.get("href", "")),
                "files": [],
                "lp": lp,
            }
        )
    return notices


def kvca_fallback_notice(notice, _state=None):
    direct_lps = (
        "한국벤처투자",
        "한국성장금융",
        "산업은행",
        "수출입은행",
        "국민연금",
        "교직원공제회",
        "행정공제회",
        "과학기술인공제회",
        "사학연금",
        "공무원연금",
        "경찰공제회",
        "중소기업중앙회",
        "건설근로자공제회",
        "신한자산운용",
        "우리자산운용",
        "삼성자산운용",
    )
    if any(name in notice.get("lp", "") for name in direct_lps):
        return False
    if pef_title(notice["title"]):
        return True
    detail = re.sub(r"\s+", "", fetch_soup(notice["url"]).get_text(" ", strip=True))
    return "기관전용사모집합투자기구" in detail


def checked(scrape_fn):
    """수집기 공통 후처리. SOURCES 를 쓰는 모든 호출자(알리미·PEF 트랙)가 여기를 지난다.

    - uid 중복 제거: 성장금융은 상단 고정공지가 일반목록에도 같이 나와 같은 글이 두 번
      들어온다(2026-07 기준 idx=1088). 그대로 두면 텔레그램 2회 발송 / PEF 이중 기입.
    - 0건이면 예외: 감시 게시판은 모두 상시 게시글이 있어 정상적으로 0건이 될 수 없다.
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
    {
        "key": "kvic",
        "name": "한국벤처투자",
        "scrape": checked(scrape_kvic),
        "eligible": kvic_pef_notice,
    },
    {
        "key": "nps",
        "name": "국민연금",
        "scrape": checked(scrape_nps),
        "eligible": nps_pef_notice,
    },
    {
        "key": "nps_news",
        "name": "국민연금 보도자료",
        "scrape": checked(scrape_nps_news),
        "eligible": pef_notice,
    },
    {
        "key": "ktcu",
        "name": "한국교직원공제회",
        "scrape": checked(scrape_ktcu),
        "eligible": pef_notice,
    },
    {
        "key": "poba",
        "name": "대한지방행정공제회",
        "scrape": checked(scrape_poba),
        "eligible": pef_notice,
    },
    {
        "key": "sema",
        "name": "과학기술인공제회",
        "scrape": checked(scrape_sema),
        "eligible": pef_notice,
    },
    {
        "key": "teachers_pension",
        "name": "사학연금",
        "scrape": checked(scrape_teachers_pension),
        "eligible": pef_notice,
    },
    {"key": "geps", "name": "공무원연금", "scrape": checked(scrape_geps), "eligible": pef_notice},
    {
        "key": "pmaa",
        "name": "경찰공제회",
        "scrape": checked(scrape_pmaa),
        "eligible": pef_notice,
    },
    {
        "key": "kbiz",
        "name": "중소기업중앙회",
        "scrape": checked(scrape_kbiz),
        "eligible": pef_notice,
    },
    {
        "key": "cw",
        "name": "건설근로자공제회",
        "scrape": checked(scrape_cw),
        "eligible": pef_notice,
    },
    {
        "key": "shinhan_am",
        "name": "신한자산운용",
        "scrape": checked(scrape_shinhan),
        "eligible": shinhan_pef_notice,
    },
    {
        "key": "woori_am",
        "name": "우리자산운용",
        "scrape": checked(scrape_woori),
        "eligible": woori_pef_notice,
    },
    {
        "key": "samsung_am",
        "name": "삼성자산운용",
        "scrape": checked(scrape_samsung),
        "eligible": pef_notice,
    },
    {
        "key": "kvca_fallback",
        "name": "KVCA 출자공고(보완)",
        "scrape": checked(scrape_kvca),
        "eligible": kvca_fallback_notice,
    },
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
            # 현재 첫 화면의 KVIC 출자계획을 미리 읽어 이후 접수/심사/선정 결과와 연결한다.
            if key == "kvic":
                for notice in notices:
                    if "출자계획" not in notice.get("category", ""):
                        continue
                    try:
                        kvic_pef_notice(notice, state)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[한국벤처투자] 기준선 문서 판독 실패: {exc}", file=sys.stderr)
            elif key in {"shinhan_am", "woori_am"}:
                for notice in sorted(notices, key=lambda item: item["uid"]):
                    try:
                        src["eligible"](notice, state)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[{name}] 기준선 문서 판독 실패: {exc}", file=sys.stderr)
            today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
            today_items = [n for n in notices if date_key(n.get("date", "")) == today]
            if not today_items:
                state[key] = max_uid
                print(f"[{name}] 최초 실행: 기준선 {max_uid} (알림 미발송)")
                continue
            first_today = min(n["uid"] for n in today_items)
            last_seen = max(
                (n["uid"] for n in notices if n["uid"] < first_today),
                default=first_today - 1,
            )
            state[key] = last_seen
            print(f"[{name}] 최초 실행: 기준선 {last_seen}, 오늘 글 {len(today_items)}건 처리")

        new_items = sorted([n for n in notices if n["uid"] > last_seen], key=lambda x: x["uid"])
        if not new_items:
            print(f"[{name}] 새 글 없음 (last_seen={last_seen})")
            continue

        sent = 0
        highest_ok = last_seen  # 안전하게 저장 가능한 최대 uid (발송 성공/불일치까지만)
        for n in new_items:
            eligible = src.get("eligible")
            if eligible:
                try:
                    if not eligible(n, state):
                        highest_ok = n["uid"]
                        continue
                except Exception as exc:  # noqa: BLE001
                    print(f"[{name}] PEF 적격성 판독 실패 #{n['uid']}: {exc}", file=sys.stderr)
                    break  # 다음 실행에서 같은 글부터 다시 판독
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
    assert pef_title("2026년 국내 PE·VC 블라인드펀드 위탁운용사 선정")
    assert pef_title("블라인드 펀드 위탁운용사 선정 공고")
    assert not pef_title("2026년 VC 블라인드펀드 출자사업")
    assert vc_only("국민연금기금 벤처펀드 국내사모투자 위탁운용사")
    assert not vc_only("국내 PE·VC 블라인드 펀드")
    assert kvic_pdf_allows_pef("출자 대상: 기관전용 사모집합투자기구") is True
    assert kvic_pdf_allows_pef("출자 대상: 벤처투자조합") is False
    assert kvic_pdf_allows_pef("일반 참고자료") is None
    assert date_key("2026.09.23") == "20260923"
    assert kvic_program_key("모태펀드(문화) 2026년 9월 출자사업 계획 공고") == kvic_program_key(
        "모태펀드(문화) 2026년 9월 출자사업 최종 선정 결과"
    )
    assert private_program_key(
        "(재공고) 과학기술혁신펀드 2026년 위탁운용사 선정계획 공고"
    ) == private_program_key(
        "과학기술혁신펀드 2026년 위탁운용사 최종 선정 결과"
    )
    assert private_program_key(
        "『국민성장펀드 초장기기술투자펀드』 위탁운용사 선정계획 공고"
    ) == "국민성장펀드초장기기술투자펀드"
    assert private_program_key(
        "『국민성장펀드 초장기기술투자펀드』 위탁운용사 선정계획 공고"
    ) == private_program_key(
        "『국민성장펀드 초장기기술투자펀드』 위탁운용사 선정 서류심사 결과"
    )
    print("ok")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        demo()
    else:
        main()
