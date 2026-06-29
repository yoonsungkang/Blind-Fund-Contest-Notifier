# 정책금융기관 공지 실시간 알리미 (텔레그램)

아래 세 기관의 공지/공고를 주기적으로 확인해, **지정한 키워드가 제목에 들어간 새 글**이
올라오면 **텔레그램으로 알림**을 보냅니다. GitHub Actions에서 무료로 24시간 자동 실행됩니다.

| 기관 | 게시판 | 수집 방식 |
|---|---|---|
| KDB 산업은행 | [공지사항](https://www.kdb.co.kr/CHBIPR23N00.act?_mnuId=IHIHIR0087) | Playwright (JS 렌더링) |
| 한국성장금융 | [출자사업공고](https://www.kgrowth.or.kr/notice.asp) | 정적 HTML (EUC-KR) |
| 한국수출입은행 | [공지/입찰](https://www.koreaexim.go.kr/HPHKBI039M01) | 정적 HTML |

## 동작 방식

1. GitHub Actions가 약 15분마다 `check_notices.py` 실행
2. 각 사이트에서 목록을 읽어, 글 고유 ID로 **지난번 이후 새 글**을 골라냄
3. 제목이 `config.json`의 키워드와 맞으면 텔레그램으로 발송 (알림에 **출처 기관명** 표시)
4. 사이트별 마지막으로 본 ID를 `state.json`에 저장(자동 커밋)

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
- 각 사이트의 첫 페이지(약 10~20건)만 확인합니다. 폴링 간격(15분) 사이에 그보다 많이
  올라오면 일부를 놓칠 수 있으나, 정책금융기관 게시판 특성상 거의 발생하지 않습니다.
- GitHub 예약 실행은 부하에 따라 몇 분~십수 분 지연될 수 있습니다.
- 한 사이트 수집이 실패해도 나머지 사이트는 정상 처리됩니다(서로 독립).
