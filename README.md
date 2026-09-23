# 정책금융기관 공지 실시간 알리미 (텔레그램)

기관투자자 출자·위탁운용 공고를 주기적으로 확인해, **기관전용 PEF로 신청 가능한 새 글**이
올라오면 **텔레그램으로 알림**을 보냅니다. GitHub Actions에서 무료로 24시간 자동 실행됩니다.

| 기관 | 게시판 | 수집 방식 |
|---|---|---|
| KDB 산업은행 | [공지사항](https://www.kdb.co.kr/CHBIPR23N00.act?_mnuId=IHIHIR0087) | Playwright (JS 렌더링) |
| 한국성장금융 | [출자사업공고](https://www.kgrowth.or.kr/notice.asp) | 정적 HTML (EUC-KR) |
| 한국수출입은행 | [공지/입찰](https://www.koreaexim.go.kr/HPHKBI039M01) | 정적 HTML |
| 한국벤처투자(KVIC) | [출자사업공지](https://www.kvic.or.kr/notice/kvic-notice/investment-business-notice) | 공식 게시판 + 공고 PDF 비히클 판독 |
| 국민연금 | [거래기관 선정공고](https://fund.nps.or.kr/impa/dlnginstslctnpbanclist/getOHEF0017M0.do) + 보도자료 | 첨부 공고명으로 VC-only 제외 |
| 한국교직원공제회 | [공지사항](https://www.ktcu.or.kr/PPW-CSB-000101) | 정적 HTML |
| 대한지방행정공제회 | [공지사항](https://www.poba.or.kr/bbs/selectNttList?sechBbsSeq=10) | 공식 페이지 내장 JSON |
| 과학기술인공제회 | [공지사항](https://www.sema.or.kr/sema/bbs/B0000022/list.do?menuNo=200017&optn1=S) | 정적 HTML |
| 사학연금·공무원연금·경찰공제회 | 각 기관 공식 공지사항 | 정적 HTML |
| 중소기업중앙회·건설근로자공제회 | 각 기관 공식 공지/선정공고 | 정적 HTML |
| 신한·우리·삼성자산운용 | [신한](https://www.shinhanfund.com/ko/mobile/board/notice) · [우리](https://www.wooriam.kr/customer/notice-list) · [삼성](https://www.samsungfund.com/fund/lounge/notice.do) 공지사항 | 정적 HTML + PDF/HWP 비히클 판독 |
| 에너지인프라자산운용·우정사업본부·군인공제회 등 | [KVCA 출자공고](https://www.kvca.or.kr/Program/invest/list.html?a_cd=8&a_gb=board&a_item=0&sm=2_2_2) | 공식 직접 수집이 막힌 기관만 보완 |

## API 확인 결과 (2026-09-23)

- **KVIC API 있음**: `businessType(bType=1)`은 출자사업 펀드명·코드, `fundType`은 한국모태펀드
  결성 현황을 제공한다. 다만 신규 공고·접수현황·서류결과·최종선정 게시물과 첨부 공고문은
  응답 필드에 없어, 해당 이벤트는 공식 출자사업 게시판에서 수집한다.
- 산업은행·수출입은행·우정사업본부도 공개 API가 있으나 각각 금융상품/환율/우체국 찾기 등으로,
  출자사업 공고 이력 API는 아니다.
- 이번에 추가한 연기금·공제회는 기관 공식 문서와 공공데이터포털에서 출자사업 공고용 공개 API를
  확인하지 못했다. 따라서 **관련 API가 있는 데이터는 API 우선, 알림 이력이 없는 부분만 공식
  게시판**이라는 원칙으로 구현했다.
- 신한·우리·삼성자산운용도 출자사업 공고용 공개 API는 확인되지 않았고, 공식 게시판이 서버에서
  완성된 HTML을 제공하므로 해당 HTML을 직접 수집한다.

## 동작 방식

1. GitHub Actions가 약 5분마다 `check_notices.py` 실행
2. 각 사이트에서 목록을 읽어, 글 고유 ID로 **지난번 이후 새 글**을 골라냄
3. 신규 기관은 PEF 적격 필터를 먼저 통과시키고, `config.json` 키워드가 맞으면 텔레그램으로 발송
4. 사이트별 마지막으로 본 ID를 `state.json`에 저장(자동 커밋)

KVIC처럼 제목만으로 비히클을 알 수 없는 경우에는 출자계획 PDF의 `출자대상` 또는
`신청가능조합형태` 문맥에서 `기관전용 사모집합투자기구`를 확인한다. 판정 결과를 사업 단위로
`state.json`에 저장해 같은 사업의 접수현황·서류결과·최종선정에도 적용한다. VC/벤처 전용 공고는
제외한다. 신한·우리자산운용도 같은 방식으로 PDF/HWP 공고문의 표까지 판독한다.

> 키워드 매칭: 영문 약어(`PE` 등)는 단어 단위로, 한글은 띄어쓰기를 무시하고 비교합니다.
> 여러 단어 키워드(예: `위탁운용사 선정`)는 모든 단어가 들어가면 매칭됩니다.

## 설정 (config.json)

```json
{
  "keywords": ["블라인드펀드", "위탁운용사 선정", "PE"]
}
```
- GitHub에서 `config.json`을 열어 ✏️로 키워드만 고치고 **Commit** 하면 다음 실행부터 반영됩니다.
- 모든 새 글을 받고 싶으면 `"keywords": []`.

## 최초 설치

1. **텔레그램 봇 생성** (@BotFather → `/newbot` → 봇 토큰)
2. **chat_id 확인**: `python get_chat_id.py <봇토큰>`
3. **GitHub 저장소에 push** 후 **Settings → Secrets and variables → Actions**에 등록:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. **Actions 탭 → Run workflow**로 첫 실행 (사이트별 기준선만 잡고 알림은 미발송)

## 로컬 테스트
```bash
pip install -r requirements.txt
python -m playwright install chromium   # KDB용
# PowerShell
$env:TELEGRAM_BOT_TOKEN="..."; $env:TELEGRAM_CHAT_ID="..."; python check_notices.py --test
```
`--test`는 키워드·상태와 무관하게 **각 사이트의 최신글 1건씩**을 보내 배관을 점검합니다.

## 참고/한계
- 각 사이트의 첫 페이지(약 10~20건)만 확인합니다. 폴링 간격(5분) 사이에 그보다 많이
  올라오면 일부를 놓칠 수 있으나, 정책금융기관 게시판 특성상 거의 발생하지 않습니다.
- GitHub 예약 실행은 부하에 따라 몇 분~십수 분 지연될 수 있습니다.
- 한 사이트 수집이 실패해도 나머지 사이트는 정상 처리됩니다(서로 독립).
- 텍스트 추출이 안 되는 스캔 PDF/HWP만 있는 KVIC 공고는 오탐 방지를 위해 자동 알림하지 않고
  로그에 `PEF 판정 보류`를 남깁니다.
