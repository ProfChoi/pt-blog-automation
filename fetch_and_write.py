import os
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

# 환경변수에서 설정값 로드
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

# Gemini 설정
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(api_version="v1beta")
)
MODEL = "gemini-2.5-flash"


def fetch_pt_articles():
    """Psychology Today 최신 기사 목록 가져오기"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    articles = []

    # 방법 1: /us/latest 페이지
    try:
        response = requests.get("https://www.psychologytoday.com/us/latest", headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # 다양한 셀렉터 시도
        for selector in ["h2 a", "h3 a", ".field-title a", ".entry-title a", "a[href*='/us/blog'], a[href*='/us/basics']"]:
            for tag in soup.select(selector):
                href = tag.get("href", "")
                title = tag.get_text(strip=True)
                if title and len(title) > 15:
                    if href.startswith("/"):
                        href = "https://www.psychologytoday.com" + href
                    if href not in [a["url"] for a in articles]:
                        articles.append({"title": title, "url": href, "desc": ""})
            if len(articles) >= 5:
                break
    except Exception as e:
        print(f"latest 페이지 오류: {e}")

    # 방법 2: /us/blog 페이지 (백업)
    if len(articles) < 3:
        try:
            response = requests.get("https://www.psychologytoday.com/us/blog", headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup.select("h2 a, h3 a, .field-title a"):
                href = tag.get("href", "")
                title = tag.get_text(strip=True)
                if title and len(title) > 15:
                    if href.startswith("/"):
                        href = "https://www.psychologytoday.com" + href
                    if href not in [a["url"] for a in articles]:
                        articles.append({"title": title, "url": href, "desc": ""})
            if len(articles) >= 5:
                pass
        except Exception as e:
            print(f"blog 페이지 오류: {e}")

    # 방법 3: 크롤링 실패 시 기본 주제 목록 사용
    if len(articles) < 3:
        print("크롤링 실패 - 기본 주제로 글 작성")
        articles = [
            {"title": "How to Build Emotional Resilience in Daily Life", "url": "https://www.psychologytoday.com", "desc": ""},
            {"title": "The Science of Happiness: What Research Tells Us", "url": "https://www.psychologytoday.com", "desc": ""},
            {"title": "Managing Anxiety in a Busy World", "url": "https://www.psychologytoday.com", "desc": ""},
        ]

    print(f"수집된 기사 수: {len(articles)}")
    return articles[:10]


def fetch_article_content(url):
    """기사 본문 가져오기"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        content = ""
        for tag in soup.select("div.entry-content p, article p, .article-body p"):
            content += tag.get_text(strip=True) + "\n"
        return content[:3000]
    except:
        return ""


def select_and_write_blog(articles):
    """Gemini로 기사 선택 및 블로그 글 작성"""

    article_list = "\n".join([
        f"{i+1}. {a['title']}"
        for i, a in enumerate(articles)
    ])

    # 한국 독자에게 적합한 기사 선택
    selection_prompt = f"""다음 Psychology Today 기사 목록에서 한국 독자에게 가장 유익한 기사 번호 1개만 답하세요. 숫자만 답하세요.

{article_list}"""

    result = client.models.generate_content(model=MODEL, contents=selection_prompt)

    try:
        # 숫자만 추출
        import re
        nums = re.findall(r'\d+', result.text.strip())
        selected_num = int(nums[0]) - 1
        if selected_num < 0 or selected_num >= len(articles):
            selected_num = 0
    except:
        selected_num = 0

    selected = articles[selected_num]
    print(f"선택된 기사: {selected['title']}")

    # 기사 본문 가져오기
    content = fetch_article_content(selected["url"])

    # 블로그 글 작성
    blog_prompt = f"""당신은 따뜻하고 친근한 심리학 블로거입니다.
아래 Psychology Today 기사를 바탕으로 한국 독자를 위한 블로그 글을 작성해주세요.

[원문 제목]
{selected['title']}

[원문 내용]
{content if content else "위 제목을 바탕으로 심리학적 내용을 작성해주세요."}

[작성 조건]
- 따뜻하고 친근한 문체 (독자에게 말 걸듯이)
- 한국 독자의 일상과 연결되는 예시 포함
- 분량: 600~800자
- 구성: 도입 → 핵심 내용 → 실생활 적용 → 마무리
- 마지막에 "출처: Psychology Today - {selected['title']}" 포함

블로그 글만 작성하세요."""

    blog_result = client.models.generate_content(model=MODEL, contents=blog_prompt)

    return {
        "title": selected["title"],
        "url": selected["url"],
        "blog_content": blog_result.text
    }


def send_email(result):
    """Gmail로 블로그 초안 발송"""
    today = datetime.now().strftime("%Y년 %m월 %d일")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[PT 블로그 초안] {today} - {result['title']}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL

    body = f"""안녕하세요, 교수님! 오늘의 블로그 초안입니다. 😊

📰 원문 기사: {result['title']}
🔗 원문 링크: {result['url']}

{'='*50}

{result['blog_content']}

{'='*50}

* 이 글은 Psychology Today 기사를 바탕으로 Gemini AI가 작성한 초안입니다.
* 내용을 검토하신 후 필요에 따라 수정하여 발행해주세요.
"""

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    print(f"✅ 이메일 발송 완료: {today}")


def main():
    print("🔍 Psychology Today 기사 수집 중...")
    articles = fetch_pt_articles()

    print("✍️ Gemini로 블로그 글 작성 중...")
    result = select_and_write_blog(articles)

    print("📧 이메일 발송 중...")
    send_email(result)


if __name__ == "__main__":
    main()
