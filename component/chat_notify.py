"""Google Chat 결과 전송 (사용자 OAuth).

이미지 첨부(media.upload)는 서비스계정(앱 인증)을 지원하지 않고 사용자 OAuth 만
허용된다. 따라서 1회 브라우저 동의로 refresh token 을 발급받아
~/.config/gchat_credentials.json 에 저장하고 이후 자동 갱신한다.

최초 1회 로그인:
    python stb-rpa/component/chat_notify.py login \
        --client-id "....apps.googleusercontent.com" --client-secret "GOCSPX-..."

전송 (Default behavior.py 가 자동 호출):
    GOOGLE_CHAT_SPACE=spaces/XXXX 환경변수 필요.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/chat.messages.create"]
TOKEN_PATH = Path.home() / ".config" / "gchat_credentials.json"
CLIENT_PATHS = [
    Path.home() / ".config" / "gws" / "chat_client_secret.json",
    Path(__file__).resolve().parent / "chat_client_secret.json",
]
PROJECT_ID = os.environ.get("GCHAT_OAUTH_PROJECT_ID", "powerful-balm-457605-k8")


def _desktop_client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id.strip(),
            "project_id": PROJECT_ID,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret.strip(),
            "redirect_uris": ["http://localhost"],
        }
    }


def _resolve_client_config(client_id: str = "", client_secret: str = "") -> dict:
    client_id = client_id or os.environ.get("GCHAT_OAUTH_CLIENT_ID", "")
    client_secret = client_secret or os.environ.get("GCHAT_OAUTH_CLIENT_SECRET", "")
    if client_id and client_secret:
        return _desktop_client_config(client_id, client_secret)
    for path in CLIENT_PATHS:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        "Chat OAuth client 미설정. --client-id/--client-secret 또는 "
        "GCHAT_OAUTH_CLIENT_ID/SECRET 환경변수, 혹은 "
        f"{CLIENT_PATHS[0]} 파일이 필요합니다."
    )


def _load_creds():
    """저장된 토큰 로드 + 만료 시 갱신. 없으면 None."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not TOKEN_PATH.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds
    return None


def _is_chat_media(path: str) -> bool:
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("image/") or mime.startswith("video/")


def _upload_attachments(service, space, paths):
    from googleapiclient.http import MediaFileUpload

    attachments = []
    for path in paths:
        if not (path and os.path.isfile(path)):
            continue
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        ref = (
            service.media()
            .upload(
                parent=space,
                body={"filename": os.path.basename(path)},
                media_body=MediaFileUpload(path, mimetype=mime),
            )
            .execute()
        )
        data_ref = ref.get("attachmentDataRef")
        if data_ref:
            attachments.append({"attachmentDataRef": data_ref})
    return attachments


def _post_message(service, space, text, attachment_paths=None) -> None:
    attachments = _upload_attachments(service, space, attachment_paths or [])
    body = {"text": text}
    if attachments:
        body["attachment"] = attachments
    service.spaces().messages().create(parent=space, body=body).execute()


def send_report(space: str, text: str, image_paths=None) -> None:
    """space(spaces/XXXX)에 text + 첨부 메시지 생성.

  Chat API: 이미지/동영상 여러 개는 한 메시지 가능, zip 등 파일은 별도 메시지로 분리.
    """
    from googleapiclient.discovery import build

    creds = _load_creds()
    if creds is None:
        raise RuntimeError(
            f"Chat OAuth 토큰 없음/만료 — 최초 로그인 필요: {TOKEN_PATH}"
        )

    service = build("chat", "v1", credentials=creds, cache_discovery=False)

    paths = [p for p in (image_paths or []) if p and os.path.isfile(p)]
    media_paths = [p for p in paths if _is_chat_media(p)]
    file_paths = [p for p in paths if not _is_chat_media(p)]

    _post_message(service, space, text, media_paths)
    for path in file_paths:
        _post_message(
            service,
            space,
            f"📎 원본 logcat: {os.path.basename(path)}",
            [path],
        )


def _login(args) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    config = _resolve_client_config(args.client_id, args.client_secret)
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"토큰 저장: {TOKEN_PATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Chat 결과 전송")
    sub = parser.add_subparsers(dest="cmd")
    p_login = sub.add_parser("login", help="최초 1회 OAuth 로그인")
    p_login.add_argument("--client-id", default="")
    p_login.add_argument("--client-secret", default="")
    p_test = sub.add_parser("test", help="테스트 메시지 전송")
    p_test.add_argument("--space", default=os.environ.get("GOOGLE_CHAT_SPACE", ""))
    p_test.add_argument("--text", default="STB QA Chat 연동 테스트")
    p_test.add_argument("--image", default="")
    args = parser.parse_args()

    if args.cmd == "login":
        return _login(args)
    if args.cmd == "test":
        if not args.space:
            print("--space spaces/XXXX 또는 GOOGLE_CHAT_SPACE 필요", file=sys.stderr)
            return 1
        imgs = [args.image] if args.image else []
        send_report(args.space, args.text, imgs)
        print("전송 완료")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
