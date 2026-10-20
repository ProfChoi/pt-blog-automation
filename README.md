# Psychology Today 초안 자동화

Psychology Today RSS의 최신 20개 중 미사용 기사를 Claude가 선정하고, 기존 문체 예시와
요일별 주제에 맞춰 한국어 1,200~1,500자 초안을 작성하여 Gmail API로 전달합니다.
기존 모델(`claude-sonnet-4-6`), 선정 방식, 프롬프트, 원문 추출 및 제목 기반 대체 작성은 유지합니다.
일정은 **화·목·일 한국시간 08:30**입니다. 기존 cron은 그대로이며 잘못된 주석만 바로잡았습니다.

## 확인한 문제와 수정

- 기존 스크립트 끝의 `main()` 호출이 두 번 있어 성공한 실행에서 두 통 발송될 수 있었습니다.
- 제공된 실행 화면에서는 STARTTLS 성공 후 `server.login()`에서
  `SMTPServerDisconnected`가 발생했습니다. 이것만으로 비밀번호 오류나 Google 차단 원인을
  확정할 수 없습니다. 새 구현은 SMTP 465/587 및 앱 비밀번호 의존성을 없애고 HTTPS Gmail API를 사용합니다.
- 기존 코드는 이미 이메일 함수 반환 **후** 사용 기록을 추가했지만, Git push 전에 종료되거나
  별도 실행이 겹치면 중복 발송될 수 있었습니다.
- 이제 발송 전 상태를 원격 저장하고, Gmail의 성공 응답 후에만 사용 기록을 저장합니다.
  OAuth 인증부터 확인하므로 잘못된 Gmail 설정 때문에 Claude 호출 비용이 먼저 발생하지 않습니다.

## 최초 설정 (한 번)

기존 `CLAUDE_API_KEY`, `GMAIL_ADDRESS`, `RECIPIENT_EMAIL` Secrets는 유지합니다.
`GMAIL_APP_PASSWORD`는 더 이상 사용하지 않습니다. 값을 채팅이나 코드에 붙여 넣지 마세요.

1. [Google Cloud Console](https://console.cloud.google.com/)에서 본인 소유 프로젝트를 만들거나
   선택하고 **Gmail API**를 사용 설정합니다.
2. Google Auth Platform의 Branding/Audience/Data Access를 설정합니다. 개인 Gmail은 External로
   구성하고 본인을 테스트 사용자로 추가합니다. 요청 범위는
   `https://www.googleapis.com/auth/gmail.send` 하나입니다.
3. 정기 운영 전 Audience의 게시 상태를 **In production**으로 전환하고 그 후 인증하세요.
   External/Testing 상태의 Gmail refresh token은 보통 **7일 후 만료**됩니다.
   개인 사용 앱의 검증 예외 및 Workspace 관리자 정책은 계정에 따라 다르므로
   Google 화면의 안내를 따르세요. Production도 영구 토큰을 보장하지 않습니다.
4. OAuth client를 **Desktop app**으로 생성하고 JSON을 내려받습니다.
   저장소 밖에 보관하거나 `client_secret*.json` 이름을 사용하세요(버전 관리 제외).
5. 자신의 PC에서 GitHub CLI(`gh`)로 저장소 수정 권한이 있는 계정에 로그인하고 실행합니다.

   ```powershell
   gh auth login
   python -m venv .venv
   .venv\Scripts\python -m pip install "google-auth-oauthlib>=1.2,<2"
   .venv\Scripts\python tools/setup_gmail_oauth.py --client-secrets "C:\path\client_secret.json"
   ```

   브라우저에서 반드시 **GMAIL_ADDRESS에 해당하는 발신 계정**을 선택하세요.
   Linux/macOS에서는 `.venv\Scripts\python` 대신 `.venv/bin/python`을 사용합니다.
   도구는 토큰을 화면이나 파일에 출력하지 않고 아래 세 값을 GitHub Actions Secrets에
   표준입력으로 직접 저장합니다. 일부 업로드가 실패하면 GitHub 권한을 확인하고 다시 실행하세요.

   | Secret | 내용 |
   | --- | --- |
   | `GMAIL_CLIENT_ID` | Desktop OAuth client ID |
   | `GMAIL_CLIENT_SECRET` | 해당 client secret |
   | `GMAIL_REFRESH_TOKEN` | 발신 Gmail 사용자가 승인한 offline refresh token |

6. 저장소 Settings → Actions → General에서 워크플로의 `contents: write` 권한이 허용되는지
   확인하세요. 기본 브랜치 보호 규칙이 Actions의 상태 커밋을 금지하면 발송 전 안전하게
   실패합니다. 조직 정책에 맞게 상태 커밋 권한을 마련한 뒤 실행하세요.
7. 수정본을 기본 브랜치에 반영한 뒤 Actions → **Daily PT Blog → Run workflow**를 한 번 실행합니다.
   성공 시 Gmail 보낸편지함, 수신함, `delivery_state.json`의 `sent`,
   `used_articles.txt`의 해당 URL을 확인하세요. 이는 실제 초안 한 통을 발송합니다.
   같은 날 다시 실행하면 추가 발송하지 않습니다. OAuth 설정만으로 실제 수신 성공이 검증되지는 않습니다.

## 중복 방지와 실패 복구

모든 발송은 기본 브랜치의 이 워크플로에서 실행하세요. 동일 concurrency 그룹으로 직렬 실행하고,
대기 후 최신 기본 브랜치를 체크아웃합니다. 로컬 직접 발송은 원격 상태 저장 단계에서 차단합니다.
Git 충돌은 강제 push나 자동 재발송으로 덮지 않습니다.

| 상태 | 의미 | 다음 실행 |
| --- | --- | --- |
| `prepared` | 선정 완료, 아직 발송 안 됨 또는 Google의 명시적 거절 | 같은 기사로 초안을 다시 작성하여 시도 |
| `sending` | 발송 직전 원격 저장 완료, 결과 미확정 | 모든 자동 발송 중단, 수동 확인 필요 |
| `sent` | Gmail 성공 응답과 ID를 확인했거나 운영자가 발송 확인 | 기사 사용 처리, 당일 추가 발송 차단 |

초안 본문, 이메일 주소, 인증값은 공개 저장소에 저장하지 않습니다. 저장되는 것은 원문 기사
메타데이터, 날짜, 상태, Message-ID 및 Gmail 접수 ID입니다. 실패 후 재작성 문장은 달라질 수 있지만
선택한 기사는 유지합니다. 전날 미완료 건을 오늘 성공하면 오늘의 한 통으로 계산합니다.
Gmail 접수 성공은 수신함 도착 보장이 아니므로 반송/스팸함은 별도 확인합니다.

### `sending` 상태에서 멈춘 경우

Gmail의 전송 API는 이 구현에서 사용할 수 있는 멱등 키를 제공하지 않습니다. 동일 Message-ID도
자동 중복 제거를 보장하지 않습니다. 응답 유실·강제 취소·발송 후 Git push 실패는 성공인지
실패인지 단정할 수 없으므로 **자동 재시도하지 않습니다**.

1. 실행 중인 워크플로가 없는지 확인합니다. 기본 브랜치의 `delivery_state.json`에서
   해당 `message_id`, 기사와 날짜를 확인합니다.
2. 발신 Gmail에서 `in:sent rfc822msgid:pt-여기에ID@pt-blog-automation.invalid`로 검색합니다
   (상태 파일의 `< >`는 제외). 검색 색인 지연이 있을 수 있으므로 잠시 기다리고,
   제목/기사 링크와 보낸편지함도 직접 확인하세요.
3. 보낸 메일을 **확인한 경우에만**, 해당 항목의 `status`를 `sent`로 바꾸고
   `sent_date`에 실제 발송일의 한국 날짜(`YYYY-MM-DD`)를 기록하여 커밋합니다.
   다음 실행은 사용 기록을 보완합니다. 과거 날짜이면 오늘의 신규 초안도 보낼 수 있습니다.
4. **발송되지 않았음이 확실할 때만** `status`를 `prepared`로 바꾸어 커밋하고 다시 실행합니다.
   단순히 검색 결과가 없다는 이유만으로 재시도하면 중복 위험이 있습니다.
   불확실하다면 `sending`을 유지하세요.

OAuth 실패는 Google API 활성화, client ID/secret 조합, 계정 승인 및 refresh token 만료/취소를
확인한 뒤 최초 인증 도구를 다시 실행합니다. HTTP 400/401/403/404/413/429는 명시적 거절로 처리하며
다음 워크플로에서 재시도합니다. 5xx, 시간 초과, 비정상 응답은 미확정 상태로 남깁니다.
실패 로그는 오류 종류만 출력하고 제공자 응답 본문·토큰은 출력하지 않습니다.

기존 `used_articles.txt`는 유지합니다. 이력 전체를 삭제하면 과거 기사 중복 가능성이 생깁니다.
새 ledger 도입 이전의 발송 성공 여부를 소급하여 알 수는 없습니다.

## 테스트

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -v
```

테스트는 Gmail/Claude를 모의 처리하여 실제 메일을 보내거나 API 비용을 발생시키지 않습니다.
성공 후 기록, 당일 중복 방지, 인증 실패, 명시적 거절, 응답 유실, 발송 전후 원격 저장 실패,
이전 날짜 재시도, 기존 선정·작성 조건과 한글 MIME을 검증합니다.
PR의 **Tests** 워크플로는 비밀값 없이 같은 검증을 수행합니다.

공식 문서:
[Gmail 메시지 발송](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send),
[Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[refresh token 만료 조건](https://developers.google.com/identity/protocols/oauth2#expiration).
