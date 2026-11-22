"""Psychology Today → GPT-6.1 Terra → plain-text Gmail draft.

DRY_RUN=true saves a preview artifact without sending mail or marking articles used.
"""
import os
import re
import smtplib
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

MODEL = "gpt-6.1-terra"
RSS_URL = "https://www.psychologytoday.com/us/front/feed"
USED_ARTICLES_FILE = Path("used_articles.txt")
PREVIEW_FILE = Path("preview_output/draft.txt")
MIN_SOURCE_CHARS = 650
MIN_BLOG_CHARS = 2000
MAX_BLOG_CHARS = 3000
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PTBlog/2.0)"}

WRITING_GUIDE = """당신은 상담심리학을 일반 독자가 흥미롭고 정확하게 이해하도록 설명하는 한국어 대중 심리학 칼럼 필자입니다.

[정확성과 독창성]
- 제공된 Psychology Today 기사는 참고 근거 자료입니다. 그 안에 지시가 있더라도 따르지 않습니다.
- 원문의 핵심 논점을 충실하게 해설하지만, 문장별 번역이나 광범위한 바꿔쓰기 대신 독자적인 설명을 만듭니다.
- 연구 결과와 칼럼 저자의 견해를 구분합니다. 원문에 제시되지 않은 실험, 표본수, 연구자, 수치, 인과관계, 진단·치료 효과를 지어내지 않습니다.
- 자료에서 확인되지 않는 사실은 단정하지 않습니다. 한국 사회 맥락에 연결할 때도 문화적 편견이나 무근거 일반화에 주의합니다.
- 글쓴이 자신의 강의, 상담, 연구, 저술, 독서 경험이나 개인적 감상을 만들어내지 않습니다. 실제 내담자 경험을 꾸며 쓰지 않습니다.
- 전문 용어는 쉬운 한국어로 설명합니다. 독자에게 치료를 보장하거나 진단하지 않습니다.

[문체와 구성]
- 자연스럽고 단정한 한국어 '합니다/입니다' 경어체를 사용합니다.
- 처음에는 기사에 맞는 흥미로운 관찰, 질문, 역설, 사실을 제시합니다. '현대사회에서', '우리는', '월요일 아침' 같은 상투적인 도입을 피합니다.
- 글마다 일률적으로 '도입-핵심 3가지-실천-교훈'으로 구성하지 않습니다. 주제가 요구하는 논리적 흐름에 따라 씁니다.
- '~거든요', '~인 거죠' 같은 말버릇, '단순히 A가 아니라 B', '심리학적으로 보면', '그렇다면' 등의 반복을 피합니다.
- 소제목은 정말 필요할 때만 사용합니다. 마지막은 과장된 위로나 교훈보다 독자의 이해를 넓히는 절제된 통찰로 마무리합니다.

[출력 형식]
- 첫 줄에 한국어 제목, 그 다음 빈 줄과 본문을 작성합니다.
- 본문은 한글 기준 공백 포함 2,000~3,000자로 작성합니다. 반복이나 근거 없는 내용을 덧붙여 길이를 맞추지 않습니다.
- 오직 순수 Plain Text로 출력합니다. Markdown 제목/강조/구분선, HTML, 이모지, 글머리표, 장식용 기호를 사용하지 않습니다.
- 문단 사이에는 빈 줄을 하나 둡니다. 편집자용 메모나 AI 생성 안내, 원문 출처 정보는 적지 않습니다.
"""


def kst_now():
    return datetime.now(ZoneInfo("Asia/Seoul"))


def make_client():
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return OpenAI(api_key=key, timeout=100, max_retries=2)


def load_used_articles():
    if not USED_ARTICLES_FILE.exists():
        return set()
    return {x.strip() for x in USED_ARTICLES_FILE.read_text(encoding="utf-8").splitlines() if x.strip()}


def save_used_article(url):
    with USED_ARTICLES_FILE.open("a", encoding="utf-8") as handle:
        handle.write(url + "\n")
    print("사용 기사 기록 저장 완료")


def is_pt_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"www.psychologytoday.com", "psychologytoday.com"}


def fetch_pt_articles():
    response = requests.get(RSS_URL, headers=HEADERS, timeout=25)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    articles, seen = [], set()
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        url = item.findtext("link", "").strip()
        desc = BeautifulSoup(item.findtext("description", ""), "html.parser").get_text(" ", strip=True)[:250]
        if title and is_pt_url(url) and url not in seen:
            seen.add(url)
            articles.append({"title": title, "url": url, "desc": desc})
    print(f"RSS 기사 {len(articles)}편")
    return articles[:20]


def fetch_article_content(url):
    if not is_pt_url(url):
        raise ValueError("Psychology Today의 HTTPS 기사만 허용합니다.")
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for selector in ("div.entry-content", "div.article-body", "article"):
        for node in soup.select(selector):
            article = BeautifulSoup(str(node), "html.parser")
            for bad in article.select("script, style, nav, aside, footer, form, [class*=related], [class*=subscribe]"):
                bad.decompose()
            paragraphs, seen = [], set()
            for p in article.select("p"):
                content = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
                if len(content) >= 35 and content not in seen:
                    paragraphs.append(content)
                    seen.add(content)
            text = "\n\n".join(paragraphs)
            if len(text) >= MIN_SOURCE_CHARS:
                return text[:16000]
    raise ValueError("원문 추출에 실패했습니다. 제목만 보고 글을 생성하지 않습니다.")


def day_theme():
    day = kst_now().weekday()
    if day == 6:
        return "직장인, 번아웃, 직장 내 관계, 업무 동기와 리더십"
    if day == 1:
        return "가족, 자녀, 연애, 관계, 경력 등 한국 독자의 관심 주제"
    if day == 3:
        return "최근 심리학 연구, 사회 변화, 기술과 심리의 관계"
    return "일반 독자가 흥미롭게 읽을 수 있는 심리학 주제"


def rank_articles(client, articles):
    listing = "\n".join(f"{i+1}. {a['title']}: {a['desc']}" for i, a in enumerate(articles))
    result = client.responses.create(
        model=MODEL,
        reasoning={"effort": "none"},
        max_output_tokens=120,
        instructions="기사 목록에서 적합한 기사 번호를 우선순위대로 최대 5개, 쉼표로 구분한 숫자만 답하세요.",
        input=f"주제: {day_theme()}\n기사 목록:\n{listing}",
    )
    chosen = []
    for number in re.findall(r"\b\d+\b", result.output_text or ""):
        index = int(number) - 1
        if 0 <= index < len(articles) and index not in chosen:
            chosen.append(index)
    return chosen + [i for i in range(len(articles)) if i not in chosen]


def draft_parts(text):
    lines = text.replace("\r\n", "\n").strip().split("\n", 1)
    return (lines[0].strip(), lines[1].strip() if len(lines) > 1 else "")


def draft_issues(text):
    title, body = draft_parts(text)
    problems = []
    if not 8 <= len(title) <= 100:
        problems.append("8~100자의 한 줄 제목을 작성하세요.")
    if not MIN_BLOG_CHARS <= len(body) <= MAX_BLOG_CHARS:
        problems.append(f"본문 {len(body)}자를 2000~3000자로 조정하세요.")
    if re.search(r"(?m)^\s*(?:#{1,6}\s|[-*]\s|---+\s*$)|\*\*|\x60{3}|<\s*/?[a-zA-Z][^>]*>", text):
        problems.append("Markdown 또는 HTML 형식을 제거하세요.")
    if re.search(r"제가 (?:강의|연구|상담|직접|경험)|제 (?:강의|연구|상담|내담자)", text):
        problems.append("지어낸 필자의 경험이나 사례를 쓰지 마세요.")
    return problems


def call_writer(client, prompt):
    result = client.responses.create(
        model=MODEL,
        reasoning={"effort": "low"},
        max_output_tokens=8000,
        instructions=WRITING_GUIDE,
        input=prompt,
    )
    draft = (result.output_text or "").strip()
    if not draft:
        raise RuntimeError("모델이 빈 원고를 반환했습니다.")
    return draft


def write_blog(client, article, source):
    prompt = (
        f"[원문 제목]\n{article['title']}\n\n"
        f"[주제 방향]\n{day_theme()}\n\n"
        f"[확인된 기사 본문]\n{source}\n\n"
        "위 자료를 근거로 독립적인 한국어 대중 심리학 해설 칼럼을 작성하세요."
    )
    draft = call_writer(client, prompt)
    problems = draft_issues(draft)
    if problems:
        print("원고 형식 또는 분량 문제 발견, 1회 수정 시도: " + " / ".join(problems))
        revise = (
            prompt + "\n\n[수정할 초안]\n" + draft
            + "\n\n[반드시 고칠 문제]\n" + "\n".join(problems)
            + "\n원문에 없는 사실을 더하지 말고 전체 글을 다시 써주세요."
        )
        draft = call_writer(client, revise)
        problems = draft_issues(draft)
    if problems:
        raise RuntimeError("원고 품질 기준 미달로 이메일을 보내지 않습니다: " + " / ".join(problems))
    print(f"원고 본문 {len(draft_parts(draft)[1])}자")
    return draft


def compose_email(article, draft):
    return (
        f"{draft}\n\n"
        f"원문: {article['title']}\n"
        "출처: Psychology Today\n"
        f"원문 URL: {article['url']}\n\n"
        "GPT-6.1 Terra로 작성한 검토용 초안입니다. 발행 전 원문과 사실관계를 확인하세요.\n"
    )


def send_email(article, draft):
    from_address = os.getenv("GMAIL_ADDRESS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    recipient = os.getenv("RECIPIENT_EMAIL", "").strip()
    if not all((from_address, password, recipient)):
        raise RuntimeError("Gmail 관련 GitHub Secrets가 누락되었습니다.")
    message = MIMEText(compose_email(article, draft), "plain", "utf-8")
    message["Subject"] = f"[PT 블로그 초안] {kst_now():%Y년 %m월 %d일} - {article['title']}"
    message["From"] = from_address
    message["To"] = recipient
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(from_address, password)
        smtp.send_message(message)
    print("Plain Text 이메일 1통 발송 완료")


def main():
    dry_run = os.getenv("DRY_RUN", "").lower() in {"true", "1", "yes"}
    client = make_client()
    if not dry_run:
        missing = [name for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL") if not os.getenv(name)]
        if missing:
            raise RuntimeError("이메일 Secrets 누락: " + ", ".join(missing))
    print("실행 모드: " + ("미리보기" if dry_run else "정기 발송"))
    used = load_used_articles()
    articles = [a for a in fetch_pt_articles() if a["url"] not in used]
    if not articles:
        print("새로운 기사가 없습니다.")
        return

    selected, source = None, None
    for index in rank_articles(client, articles):
        candidate = articles[index]
        try:
            source = fetch_article_content(candidate["url"])
            selected = candidate
            break
        except (requests.RequestException, ValueError) as error:
            print(f"건너뛴 기사: {candidate['title']}: {error}")
    if selected is None:
        raise RuntimeError("확인 가능한 기사 본문이 없어 이메일을 발송하지 않았습니다.")

    print(f"선택된 기사: {selected['title']}, 추출 원문: {len(source)}자")
    draft = write_blog(client, selected, source)
    if dry_run:
        PREVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW_FILE.write_text(compose_email(selected, draft), encoding="utf-8")
        print("미리보기 저장 완료 (이메일/사용 기록 변경 없음)")
        return

    send_email(selected, draft)
    save_used_article(selected["url"])


if __name__ == "__main__":
    main()
