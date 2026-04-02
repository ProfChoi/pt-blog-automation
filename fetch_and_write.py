import os
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from bs4 import BeautifulSoup
import google.generativeai as genai

# 환경변수에서 설정값 로드
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")


def fetch_pt_articles():
    """Psychology Today 최신 기사 목록 가져오기"""
    url = "https://www.psychologytoday.com/us/latest"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    response = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")
    
    articles = []
    for item in soup.select("article")[:10]:
        title_tag = item.select_one("h2, h3")
        link_tag = item.select_one("a")
        desc_tag = item.select_one("p")
        
        if title_tag and link_tag:
            href = link_tag.get("href", "")
            if href.startswith("/"):
                href = "https://www.psychologytoday.com" + href
            articles.append({
                "title": title_tag.get_text(strip=True),
                "url": href,
                "desc": desc_tag.get_text(strip=True) if desc_tag else ""
            })
    
    return articles


def fetch_article_content(url):
    """기사 본문 가져오기"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    response = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")
    
    content = ""
    for tag in soup.select("div.entry-content p, article p"):
        content += tag.get_text(strip=True) + "\n"
    
    return content[:3000]  # 너무 길면 잘라냄


def select_and_write_blog(articles):
    """Gemini로 기사 선택 및 블로그 글 작성"""
    
    article_list = "\n".join([
        f"{i+1}. {a['title']}\n   {a['desc']}"
        for i, a in enumerate(articles[:10])
    ])
    
    # 한국 독자에게 적합한 기사 선택
    selection_prompt = f"""
다음은 Psychology Today의 최신 기사 목록입니다.
한국 독자, 특히 심리학과 정신건강에 관심 있는 분들에게 가장 유익하고 흥미로울 기사 1개를 선택해주세요.
번호만 답해주세요.

{article_list}
"""
    
    result = model.generate_content(selection_prompt)
    try:
        selected_num = int(result.text.strip()[0]) - 1
    except:
        selected_num = 0
    
    selected = articles[selected_num]
    
    # 기사 본문 가져오기
    content = fetch_article_content(selected["url"])
    
    # 블로그 글 작성
    blog_prompt = f"""
당신은 따뜻하고 친근한 심리학 블로거입니다.
아래 Psychology Today 기사를 바탕으로 한국 독자를 위한 블로그 글을 작성해주세요.

[원문 제목]
{selected['title']}

[원문 내용 요약]
{content}

[작성 조건]
- 따뜻하고 친근한 문체 (독자에게 말 걸듯이)
- 한국 독자의 일상과 연결되는 예시 포함
- 분량: 600~800자
- 구성: 도입 → 핵심 내용 → 실생활 적용 → 마무리
- 출처 표기: 마지막에 "출처: Psychology Today - {selected['title']}" 포함

블로그 글만 작성하고 다른 말은 하지 마세요.
"""
    
    blog_result = model.generate_content(blog_prompt)
    
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
    
    body = f"""
안녕하세요, 교수님! 오늘의 블로그 초안입니다. 😊

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
