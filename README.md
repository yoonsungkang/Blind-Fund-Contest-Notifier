# 산업은행(KDB) 공지사항 실시간 알리미

한국산업은행 [공지사항 게시판](https://www.kdb.co.kr/CHBIPR23N00.act?_mnuId=IHIHIR0087)을
주기적으로 확인해, **내가 지정한 키워드가 제목에 들어간 새 공지**가 올라오면
**텔레그램으로 알림**을 보냅니다. GitHub Actions에서 무료로 24시간 자동 실행됩니다.

## 동작 방식

1. GitHub Actions가 약 15분마다 `check_notices.py` 실행
2. Playwright(헤드리스 크롬)로 공지 게시판을 열어 목록을 읽음
3. 글 번호(`번호` 칼럼)로 지난번 이후 올라온 **새 글**을 골라냄 (`state.json`)
4. 제목이 `config.json`의 키워드와 맞으면 텔레그램으로 발송
5. 마지막으로 본 번호를 `state.json`에 저장(자동 커밋)

> 키워드 매칭: 영문 약어(`PE` 등)는 단어 단위로, 한글은 부분 일치로 비교합니다.
> 여러 단어로 된 키워드(예: `위탁 운용사 선정`)는 세 단어가 **모두** 들어가면 매칭됩니다.

---

## 설치 순서

### 1) 텔레그램 봇 만들기
1. 텔레그램에서 **@BotFather** 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름과 아이디 지정
3. 받은 **봇 토큰**(`1234567:ABC...` 형태)을 복사
4. 방금 만든 봇과 대화를 시작하고 아무 메시지나 한 번 전송

### 2) chat_id 확인
로컬에서 한 번만 실행:
```bash
pip install requests
python get_chat_id.py <봇토큰>
```
출력된 숫자(예: `123456789`)가 **chat_id** 입니다.
(그룹/채널로 받으려면 봇을 그 방에 초대하고 메시지를 보낸 뒤 다시 실행)

### 3) GitHub 저장소에 올리기
1. GitHub에서 **private 저장소** 새로 생성
2. 이 폴더 전체를 그 저장소에 push
   ```bash
   git init
   git add .
   git commit -m "init KDB notice notifier"
   git branch -M main
   git remote add origin https://github.com/<사용자>/<저장소>.git
   git push -u origin main
   ```

### 4) 토큰을 GitHub Secret으로 등록
저장소 → **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` = 봇 토큰
- `TELEGRAM_CHAT_ID` = 위에서 확인한 chat_id

### 5) Actions 활성화 & 첫 실행
1. 저장소 **Actions** 탭 → 워크플로 활성화(처음엔 버튼 한 번 눌러야 할 수 있음)
2. **KDB 공지 알리미** → **Run workflow**로 수동 1회 실행
   - 첫 실행은 **기준선만 설정**하고 알림을 보내지 않습니다(과거 글 폭탄 방지)
   - 이후부터 새 글이 키워드와 맞으면 자동 알림

---

## 키워드 수정
`config.json`의 `keywords` 배열만 고치면 됩니다.
```json
{
  "keywords": ["블라인드 펀드", "위탁 운용사 선정", "PE", "출자", "위탁운용사"],
  "categories": []
}
```
- `categories`를 비워두면 모든 카테고리(안내/입찰공고/채용/약관개정/신상품)에서 키워드를 찾습니다.
  특정 카테고리만 보려면 예: `["입찰공고"]`.
- 모든 새 글을 받고 싶으면 `keywords`를 `[]`(빈 배열)로 두세요.

수정 후 commit & push 하면 다음 실행부터 반영됩니다.

## 로컬 테스트
```bash
pip install -r requirements.txt
python -m playwright install chromium
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN="..."; $env:TELEGRAM_CHAT_ID="..."; python check_notices.py
```
첫 실행은 기준선만 잡으므로, 알림 테스트를 하려면 `state.json`의 `last_seen`을
현재보다 작은 값(예: `1020`)으로 바꾼 뒤 실행하세요.

## 참고/한계
- 한 번에 첫 페이지 10건만 확인합니다. 15분 사이 11건 이상 올라오면 일부를 놓칠 수 있어
  로그에 경고가 남습니다(은행 게시판 특성상 거의 발생하지 않음).
- GitHub의 예약 실행(schedule)은 부하에 따라 몇 분~십수 분 지연될 수 있습니다.
  더 촘촘히 원하면 `check.yml`의 `*/15`를 `*/5`로 바꾸세요(최소 5분).
- 예약 실행은 저장소 **기본 브랜치(main)** 에서만 동작합니다.
