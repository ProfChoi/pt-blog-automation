"""Authorize Gmail locally and upload OAuth secrets without printing them."""
import argparse
import subprocess


def upload_secrets(credentials, repo):
    values = {
        "GMAIL_CLIENT_ID": credentials.client_id,
        "GMAIL_CLIENT_SECRET": credentials.client_secret,
        "GMAIL_REFRESH_TOKEN": credentials.refresh_token,
    }
    if not all(values.values()):
        raise RuntimeError("Incomplete OAuth credentials")
    for name, value in values.items():
        # stdin avoids secrets in shell history and process command-line arguments.
        result = subprocess.run(
            ["gh", "secret", "set", name, "--repo", repo],
            input=value, text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"Could not store {name}; rerun after checking GitHub access")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secrets", required=True, help="Downloaded Desktop OAuth client JSON")
    parser.add_argument("--repo", default="ProfChoi/pt-blog-automation")
    args = parser.parse_args()
    # Check GitHub login before asking the user to authorize Google.
    subprocess.run(["gh", "auth", "status"], check=True, capture_output=True)
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(
        args.client_secrets, scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    credentials = flow.run_local_server(
        host="localhost", port=0, open_browser=True, prompt="consent",
        authorization_prompt_message="브라우저에서 발신 Gmail 계정으로 로그인하세요.",
        success_message="인증이 완료되었습니다. 창을 닫고 터미널로 돌아가세요.",
        timeout_seconds=300,
    )
    upload_secrets(credentials, args.repo)
    print("Gmail OAuth 비밀값 3개를 GitHub Actions Secrets에 저장했습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print provider errors, redirect URLs, authorization codes or tokens.
        print(f"설정 실패 ({type(exc).__name__}). GitHub 로그인과 Google OAuth 설정을 확인하세요.")
        raise SystemExit(1)
