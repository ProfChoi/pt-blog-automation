import os
import re
import smtplib
import requests
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
import anthropic

# 환경변수에서 설정값 로드
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

# Claude 클라이언트 설정
client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
MODEL = "claude-sonnet-4-6"

RSS_URL = "https://www.psychologytoday.com/us/front/feed"
USED_ARTICLES_FILE = "used_articles.txt"

STYLE_SAMPLES = """
[내 글쓰기 스타일 예시 1]
어느덧 겨울 방학도 다 지나고 다음주면 2026학년도 1학기가 개강합니다. 마찬가지로 대학 1~2학년생을 대상으로 하는 교양 과목 '자기이해를 위한 긍정심리학'을 강의하는데 강의 자료를 만든지도 좀 오래된 것 같고, 또 최근 매우 핫한 Claude Cowork를 이용하면 PPT를 잘 만들어 준다고 해서, 리모델링을 한 번 해봤습니다. AI덕분에 참 편해진 측면도 있지만 모든 일에는 명암이 있듯 이런 부정적인 측면을 어떻게 사회가 수용하고 다뤄나갈 것인지에 대한 논의도 무척이나 시급하단 생각입니다.

[내 글쓰기 스타일 예시 2]
지난 학기 AI와 상담심리학의 융합교과목을 기획해서 직접 한 학기 동안 운영을 했었고, 처음이라 좌충우돌했지만 그럼에도 수강생들의 긍정적인 피드백, 그리고 AI를 고려하지 않고서는 앞으로 성장해 나갈 수 없다는 확고한 생각에 더해서 더더욱 이 분야 연구와 공부를 해야겠다는 생각도 있었습니다.

[내 글쓰기 스타일 예시 3]
질적연구에서 가장 중요한 절차라 할 수 있는 '의미단위' 추출 및 '코딩'을 지원하는 GEMs입니다. 지난 학기 개념도 연구를 하는 지도학생의 연구에 활용하고자 개발했는데 방학 동안 프롬프트를 좀 더 보완해서 저작권 등록(제 C-2026-004621호)을 마쳤습니다. 저작권 등록을 한 이유는 이를 통해서 저작료나 사용료를 받아 볼 심산이 아니고, 뭔가 공식적인 기록을 남긴다는 차원에서 등록 신청을 해봤는데, 잘 통과가 되었습니다.
"""


def load_used_articles():
    """이미 사용한 기사 URL 목록 로드"""
    if os.path.exists(USED_ARTICLES_FILE):
        with open(USED_ARTICLES_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_used_article(url):
    """사용한 기사 URL 저장"""
    with open(USED_ARTICLES_FILE, "a") as f:
        f.write(url + "\n")
    print(f"✅ 사용 기록 저장: {url}")


def fetch_pt_articles():
    """Psychology Today RSS 피드로 최신 기사 가져오기"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.get(RSS_URL, headers=headers, timeout=15)
    root = ET.fromstring(response.content)

    articles = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        url = item.findtext("link", "").strip()
        desc = item.findtext("description", "").strip()
        desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)[:200]

        if title and url:
            articles.append({"title": title, "url": url, "desc": desc})

    print(f"RSS로 수집된 기사 수: {len(articles)}")
    return articles[:20]


def filter_unused_articles(articles, used_urls):
    """이미 사용한 기사 제외"""
    unused = [a for a in articles if a["url"] not in used_urls]
    print(f"미사용 기사 수: {len(unused)}")
    return unused


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
    """Claude로 기사 선택 및 블로그 글 작성"""

    article_list = "\n".join([
        f"{i+1}. {a['title']}\n   {a['desc']}"
        for i, a in enumerate(articles)
    ])

    # 기사 선택
    selection_response = client.messages.create(
        model=MODEL,
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": f"다음 Psychology Today 기사 목록에서 한국 독자(심리학·정신건강 관심층)에게 가장 유익하고 흥미로운 기사 번호 1개만 답하세요. 숫자만 답하세요.\n\n{article_list}"
        }]
    )

    try:
        nums = re.findall(r'\d+', selection_response.content[0].text.strip())
        selected_num = int(nums[0]) - 1
        if selected_num < 0 or selected_num >= len(articles):
            selected_num = 0
    except:
        selected_num = 0

    selected = articles[selected_num]
    print(f"선택된 기사: {selected['title']}")
    print(f"기사 URL: {selected['url']}")

    content = fetch_article_content(selected["url"])

    # 블로그 글 작성
    blog_response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        messages=[{
            "role": "user",
            "content": f"""아래는 제가 직접 쓴 글의 스타일 예시입니다. 이 문체와 어투를 최대한 살려서 블로그 글을 작성해주세요.

{STYLE_SAMPLES}

---

[원문 제목]
{selected['title']}

[원문 내용]
{content if content else "위 제목을 바탕으로 심리학적 내용을 작성해주세요."}

[작성 조건]
- 위 스타일 예시의 자연스러운 흐름은 유지하되, 심리학 전문가로서의 식견과 전문 용어를 적절히 녹여 신뢰감 있는 문체로 작성할 것
- 한국 독자의 일상과 연결되는 예시 포함
- 분량: 1200~1500자
- 구성: 도입 → 심리학적 배경 설명 → 핵심 내용 → 실생활 적용 → 마무리
- 마지막에 "출처: Psychology Today - {selected['title']}" 포함

블로그 글만 작성하세요."""
        }]
    )

    return {
        "title": selected["title"],
        "url": selected["url"],
        "blog_content": blog_response.content[0].text
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

* 이 글은 Psychology Today 기사를 바탕으로 Claude AI가 작성한 초안입니다.
* 내용을 검토하신 후 필요에 따라 수정하여 발행해주세요.
"""

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    print(f"✅ 이메일 발송 완료: {today}")


def main():
    print("🔍 Psychology Today RSS 기사 수집 중...")
    articles = fetch_pt_articles()

    print("📋 사용한 기사 목록 확인 중...")
    used_urls = load_used_articles()
    unused_articles = filter_unused_articles(articles, used_urls)

    if not unused_articles:
        print("⚠️ 새로운 기사가 없습니다. 다음 실행을 기다려주세요.")
        return

    print("✍️ Claude로 블로그 글 작성 중...")
    result = select_and_write_blog(unused_articles)

    print("💾 사용 기록 저장 중...")
    save_used_article(result["url"])

    print("📧 이메일 발송 중...")
    send_email(result)


if __name__ == "__main__":
    main()
