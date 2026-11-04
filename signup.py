# signup.py
# ─────────────────────────────────────────────────────────────
# 無料公開（年内無料）の登録受付。v3.36 で追加。
#
#   GET  /signup           登録フォーム（開いた時刻の署名付きトークンを埋め込んで返す）
#   GET  /signup/complete  登録完了画面（キーはブラウザの sessionStorage から表示する）
#   POST /signup           登録の受付。メールアドレス1つにつき有効なキーは1つ。
#
# 【main.py に足す2行】ファイル末尾に置く。
#     from signup import router as signup_router
#     app.include_router(signup_router)
#
# 【Renderに設定する環境変数】
#   SIGNUP_SECRET    必須。フォームのトークンに署名する秘密の文字列（長いランダム文字列）。
#                    未設定でも起動はするが、再起動のたびに変わるため、
#                    再起動前に開いていたフォームは「再読み込みしてください」になる。
#   SIGNUP_ENABLED   任意。0 / false / off にすると受付を止める（デプロイ不要の停止スイッチ）。
#                    既定は受付中。
#   FREE_PERIOD_END  任意。無料ライセンスの有効期限（YYYY-MM-DD）。既定 2026-12-31。
#                    延長するときは、ここを変えると「これから登録する人」の期限が変わる。
#                    登録済みの人の期限は変わらない（別途まとめて延長する）。
#
# 【決めごと（2026-11-04 Koji）】
#   ・利用者の使いやすさを優先する。新規登録のキーは画面にも表示する
#     （メールが届かなかったときに受け取れなくなるのを防ぐため）。
#   ・登録済みのアドレスなら、新しいキーは作らず、キーも表示も再送もしない。
#     「登録済みです。キーをなくした場合はサポートへ」と案内する。
#     キーを画面に出すと、他人のアドレスを入れるだけでその人のキーが見えてしまうため。
#   ・IPアドレスによる制限と1日の登録数の上限は入れない。
#     代わりに SIGNUP_ENABLED でいつでも受付を止められるようにしてある。
#   ・機械による登録は2つの仕掛けで防ぐ（どちらも何も保存しない）。
#       見えない入力欄（website）… 人には見えない欄。中身があれば受け付けない。
#       送信までの時間 … 開いてから3秒未満、または24時間超は受け付けない。
#   ・保存する個人情報はメールアドレスだけ。IPアドレスやブラウザ情報は保存しない。
#     ログにもメールアドレスは一部を伏せて出す。
# ─────────────────────────────────────────────────────────────
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("signup")
router = APIRouter()

FREE_PLAN = "free"
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "moonpicker_support@doublemoon.biz")

MIN_SECONDS = 3            # 開いてからこれより早い送信は機械とみなす
MAX_AGE_SECONDS = 86400    # 24時間。これより古いフォームは再読み込みしてもらう

_SECRET = os.environ.get("SIGNUP_SECRET", "")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    log.warning("SIGNUP_SECRET is not set; using a temporary secret "
                "(open sign-up forms will expire on restart). Set it in Render.")

# 実用上十分な形式チェック。厳密な判定はメールが届くかどうかで決まる。
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ── 設定 ────────────────────────────────────────────────
def signup_enabled() -> bool:
    return os.environ.get("SIGNUP_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def free_period_end() -> date:
    raw = os.environ.get("FREE_PERIOD_END", "2026-12-31").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        log.error("FREE_PERIOD_END is not a valid date (%r); falling back to 2026-12-31", raw)
        return date(2026, 12, 31)


# ── フォームのトークン（開いた時刻＋署名。何も保存しない）────────
def _sign(ts: str) -> str:
    return hmac.new(_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()


def issue_form_token(now: float | None = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    return f"{ts}.{_sign(ts)}"


def check_form_token(token: str, now: float | None = None) -> str | None:
    """問題なければ None、だめなら理由（"too_fast" / "expired" / "invalid"）を返す。"""
    try:
        ts, sig = (token or "").split(".", 1)
        issued = int(ts)
    except ValueError:
        return "invalid"
    if not hmac.compare_digest(sig, _sign(ts)):
        return "invalid"
    age = (now if now is not None else time.time()) - issued
    if age < MIN_SECONDS:
        return "too_fast"
    if age > MAX_AGE_SECONDS:
        return "expired"
    return None


# ── 補助 ────────────────────────────────────────────────
def normalize_email(raw: str) -> str | None:
    email = (raw or "").strip()
    if not email or len(email) > 254 or not _EMAIL_RE.match(email):
        return None
    # 大文字小文字の違いで「別の人」にならないよう、小文字にそろえて保存する
    return email.lower()


def mask_email(addr: str) -> str:
    local, sep, domain = (addr or "").partition("@")
    return f"{local[:1]}***@{domain}" if sep else "***"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": "error", "code": code, "message": message})


# 利用者向けの文言（英語）。理由ごとに「次に何をすればよいか」を書く。
MSG = {
    "disabled":  "Registration is paused at the moment. Please try again later, "
                 f"or contact {SUPPORT_EMAIL}.",
    "bad_request": "Your request could not be processed. Please reload the page and try again.",
    "honeypot":  "Your request could not be processed. Please reload the page and try again.",
    "invalid":   "This page has expired. Please reload the page and try again.",
    "expired":   "This page has expired. Please reload the page and try again.",
    "too_fast":  "Please wait a moment, then press the button again.",
    "email":     "Please enter a valid email address.",
    "server":    "Something went wrong on our side. Please try again in a few minutes, "
                 f"or contact {SUPPORT_EMAIL}.",
    "exists":    "This email address is already registered. If you have lost your licence key, "
                 f"please contact {SUPPORT_EMAIL}.",
}


# ── 画面 ────────────────────────────────────────────────
def _read(path_candidates):
    for p in path_candidates:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    return None


@router.get("/signup", response_class=HTMLResponse)
async def signup_page():
    html = _read(("frontend/signup.html", "signup.html"))
    if html is None:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    # トークンは英数字とドットだけなので、HTML属性にそのまま入れてよい
    return HTMLResponse(html.replace("{{FORM_TOKEN}}", issue_form_token()))


@router.get("/signup/complete", response_class=HTMLResponse)
async def signup_complete_page():
    html = _read(("frontend/signup_complete.html", "signup_complete.html"))
    if html is None:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    return HTMLResponse(html)


# ── 受付 ────────────────────────────────────────────────
@router.post("/signup")
async def signup_submit(request: Request):
    if not signup_enabled():
        return _error(503, "disabled", MSG["disabled"])

    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return _error(400, "bad_request", MSG["bad_request"])

    # ① 見えない入力欄。人は入力しない。
    if str(body.get("website") or "").strip():
        log.info("signup rejected: honeypot filled")
        return _error(400, "honeypot", MSG["honeypot"])

    # ② 送信までの時間
    reason = check_form_token(str(body.get("token") or ""))
    if reason:
        log.info("signup rejected: token %s", reason)
        return _error(400, reason, MSG[reason])

    # ③ メールアドレス
    email = normalize_email(str(body.get("email") or ""))
    if not email:
        return _error(400, "email", MSG["email"])

    # ここから先はDBを使う。テストで差し替えられるよう、関数の中で読み込む。
    from database import create_license
    from db_redesign import (find_active_license_by_email, referral_exists,
                             set_license_ref, set_mail_status)
    import mailer

    try:
        # ④ 1メールにつき有効なキーは1つ。登録済みならキーは出さない。
        if find_active_license_by_email(email):
            log.info("signup: already registered %s", mask_email(email))
            return {"status": "exists", "message": MSG["exists"]}

        expires = free_period_end()
        res = create_license(email=email, plan=FREE_PLAN, note="free signup",
                             expires_at=expires)
        key = res["license_key"]
    except Exception as e:
        log.error("signup failed for %s: %s", mask_email(email), e)
        return _error(500, "server", MSG["server"])

    # ⑤ 紹介コード（登録済みのコードだけ紐づける。失敗しても登録は成功扱い）
    ref = str(body.get("ref") or "").strip()
    if ref and _REF_RE.match(ref):
        try:
            if referral_exists(ref):
                set_license_ref(key, ref)
        except Exception as e:
            log.error("could not link referral for %s: %s", key, e)

    # ⑥ メール。失敗しても画面にキーを出すので登録は成功扱い。
    #    ライセンス一覧で「未送信」（赤）になり、スタッフが「キー再送」で送れる。
    mail_sent = False
    try:
        mail_sent = mailer.send_license_key(email, key, FREE_PLAN, res.get("expires_at"))
        set_mail_status(key, "sent" if mail_sent else "failed",
                        None if mail_sent else "send_license_key returned false")
    except Exception as e:
        log.error("signup mail failed for %s: %s", key, e)
        try:
            set_mail_status(key, "failed", str(e))
        except Exception:
            pass

    log.info("signup: issued %s to %s (mail %s)", key, mask_email(email),
             "sent" if mail_sent else "FAILED")
    return {
        "status": "created",
        "license_key": key,
        "expires_at": res.get("expires_at"),
        "plan_label": mailer.plan_text_en(FREE_PLAN),   # 顧客向け表記はmailerが単一情報源
        "mail_sent": mail_sent,
    }
