# Psychology Today 블로그 자동화

Psychology Today RSS에서 미사용 기사를 선별하고, 확인된 기사 본문을 GPT-6.2 Sol로 심리학 에세이 형식으로 집필한 뒤 한국어 블로그 초안을 이메일로 보냅니다.

## 운영 개요
- 기사 선별 모델: OpenAI Responses API / gpt-6.1-terra (reasoning none)
- 에세이 작성 모델: OpenAI Responses API / gpt-6.2-sol (reasoning medium)
- 원고: 한국어 심리학 에세이형, 본문 공백 포함 900~1,200자(권장 1,000~1,100자)
- 이메일: 순수 텍스트(text/plain; UTF-8)만 발송, 첨부파일 없음, 1회 실행당 최대 한 통
- 이미지 API를 호출하지 않아 이미지 생성 비용이 발생하지 않음
- 정기 실행: 한국시간 일요일·화요일·목요일 오전 08:30 (UTC 토·월·수 23:30)
- 사용한 기사: used_articles.txt 파일로 중복 방지 (기존 기록 보존)

## GitHub Secrets
다음 Secrets를 저장소 Settings > Secrets and variables > Actions에 등록하세요.
- OPENAI_API_KEY: OpenAI API Key
- GMAIL_ADDRESS: Gmail 발신자 주소
- GMAIL_APP_PASSWORD: Gmail 앱 비밀번호
- RECIPIENT_EMAIL: 초안을 받는 주소

Claude API는 더 이상 사용하지 않습니다. CLAUDE_API_KEY가 남아 있다면 저장소 관리자 본인이 GitHub Secrets에서 삭제하고, 더 이상 필요 없을 경우 Anthropic 콘솔에서도 키를 폐기하세요. 이 저장소에는 키의 실제 값을 작성하지 마세요.

## 안전한 첫 테스트
1. Actions에서 Daily PT Blog (GPT-6.2 Sol Essays)를 선택합니다.
2. Run workflow를 클릭합니다. dry_run은 기본 false이므로 새로운 기사 한 편의 초안이 이메일로 발송됩니다.
3. 이메일을 보내지 않고 확인만 하려면 dry_run을 체크(true)한 뒤 실행합니다. 이때 실행 기록의 Artifacts > pt-blog-preview에서 draft.txt를 확인할 수 있습니다.

미리보기는 이메일을 보내거나 used_articles.txt를 바꾸지 않습니다. 정기 실행과 이메일 발송 수동 실행은 한 편의 이메일 발송이 성공한 다음에만 사용 기록을 추가하며 GitHub Actions가 이를 커밋합니다. 메일 전송은 성공했으나 Git 커밋이 실패하면 다음 실행 시 중복 발송될 가능성이 있어 Actions 실행 상태를 확인해야 합니다.

## 품질·사실 검증
- 기사 본문 추출 실패 시 제목만으로 글을 생성하지 않고 해당 기사를 건너뜁니다.
- 원문에 없는 숫자, 실험, 사실, 글쓴이 경험을 만들어내지 않도록 프롬프트에서 금지합니다.
- 기본 분량, Markdown/HTML 흔적, 일부 1인칭 경험 문구를 자동 점검합니다.
- 점검 기준에 어긋나면 1회 수정 요청 후에도 해결되지 않을 경우 이메일을 발송하지 않습니다.
- 자동 점검은 사실 확인을 완전히 대체하지 않습니다. 외부 게시 전에 원문과 근거를 반드시 확인하세요.
- 기사 원문을 무단으로 길게 복제하지 않고 독립적인 해설을 작성하며, 메일 끝에 원문 제목과 링크를 표시합니다.

## 개발자용
- 파이프라인: fetch_and_write.py
- 오프라인 검사: python -m unittest discover -s tests -v
- 필요 패키지: openai (v2), requests, beautifulsoup4
- 모델 설정은 fetch_and_write.py의 SELECTION_MODEL 및 WRITING_MODEL에 정의되어 있습니다.
- 텍스트 API 사용량(기사 선별 + 에세이 작성 + 필요시 재작성)은 모델별로 GitHub Actions 로그에서 확인할 수 있습니다. 실제 비용은 https://platform.openai.com/usage 에서 확인하세요.
- `OPENAI_API_KEY`는 모델별로 별도 발급받지 않습니다. 같은 프로젝트 키로 Terra와 Sol을 호출합니다. 프로젝트의 모델 접근 권한 및 API 사용 한도를 확인하세요.
