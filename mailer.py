# mailer.py
# ─────────────────────────────────────────────────────────────
# ライセンスキーのメール送付。さくらインターネットのSMTPを使用する。
# （外部メール配信サービスを増やさない方針のため、標準ライブラリのみで実装）
#
# 【設計方針】
#  ・送信に失敗しても例外を外に出さない。Webhookは 200 を返し切る必要があるため
#    （Polar側がエラーとみなして再送 → 二重処理を招く）。失敗はログに残し、
#    運営者が管理画面からライセンスを確認して手動送付する。
#  ・パスワードはコードに書かない。Renderの環境変数から読む。
#  ・環境変数が未設定の場合は「送信せずログのみ」で静かに終了する
#    （ローカル起動やテスト時にエラーで落ちないようにするため）。
#
# 【Renderに設定する環境変数】
#   SMTP_HOST      doublemoon.sakura.ne.jp
#   SMTP_PORT      587          （STARTTLS。465にするとSSL接続になる）
#   SMTP_USER      js_license@doublemoon.biz
#   SMTP_PASSWORD  （さくらのメールパスワード）
#   MAIL_FROM      js_license@doublemoon.biz     ※省略時は SMTP_USER
#   MAIL_FROM_NAME JobSearch                     ※省略可
#   SUPPORT_EMAIL  jobsearch_support@doublemoon.biz  ※省略可
#   BASE_URL       https://jobsearch.doublemoon.biz  ※既存
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

log = logging.getLogger("mailer")

SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM      = os.environ.get("MAIL_FROM", "") or SMTP_USER
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "JobSearch")
SUPPORT_EMAIL  = os.environ.get("SUPPORT_EMAIL", "jobsearch_support@doublemoon.biz")
BASE_URL       = os.environ.get("BASE_URL", "https://jobsearch.doublemoon.biz")

SMTP_TIMEOUT = 20  # 秒。Webhookの応答が遅れすぎないように短めにする。

# 顧客向け表示は英語。plans.py の label は管理画面用（日本語）のため使わない。
PLAN_TEXT_EN = {
    "1month":     ("Basic", 100),
    "1month_pro": ("Pro",   300),
}


def is_configured() -> bool:
    """送信に必要な設定が揃っているか。"""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and MAIL_FROM)


def _plan_text(plan: str) -> str:
    name, cap = PLAN_TEXT_EN.get(plan, ("Standard", 100))
    return f"{name} — {cap} job evaluations per month"


def _send(to_email: str, subject: str, body: str) -> bool:
    """1通送る。成功なら True。例外は出さず False を返す。"""
    if not is_configured():
        log.warning("mailer not configured; skipped sending to %s", to_email)
        return False
    if not to_email:
        log.warning("no recipient address; skipped")
        return False

    msg = EmailMessage()
    msg["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=MAIL_FROM.split("@")[-1])
    msg["Reply-To"] = SUPPORT_EMAIL
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        if SMTP_PORT == 465:
            # SSL接続（さくらは465も利用可）
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT,
                                  context=context, timeout=SMTP_TIMEOUT) as smtp:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(msg)
        else:
            # STARTTLS（587。さくらの推奨）
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(msg)
        log.info("mail sent to %s (%s)", to_email, subject)
        return True
    except Exception as e:
        # 送信失敗でWebhookを落とさない。運営者が管理画面から手動送付する。
        log.error("mail send failed to %s: %s", to_email, e)
        return False


# ── ライセンスキーの送付（新規発行時） ─────────────────────
def send_license_key(to_email: str, license_key: str, plan: str,
                     expires_at: str | None = None) -> bool:
    subject = "Your JobSearch licence key"
    lines = [
        "Thank you for subscribing to JobSearch.",
        "",
        "Your licence key:",
        f"    {license_key}",
        "",
        f"Plan: {_plan_text(plan)}",
    ]
    if expires_at:
        lines.append(f"Valid until: {expires_at} (renews automatically)")
    lines += [
        "",
        "How to start",
        f"  1. Open {BASE_URL}/app/upwork",
        "  2. Paste the licence key on the sign-in screen and accept the privacy policy.",
        "  3. Fill in your profile (skills, target rate, keywords). It is stored in your",
        "     own browser, not on our servers.",
        "  4. Copy your job search results, paste them in, and every listing comes back",
        "     scored 0-100 with a reason.",
        "",
        "Notes",
        "  - Keep this email. The key is how you sign in on a new device or browser.",
        "  - Billing, invoices and cancellation are handled in your Polar customer",
        "    portal. The link is in your payment receipt.",
        f"  - Questions? Just reply to this email, or write to {SUPPORT_EMAIL}",
        "",
        "DoubleMoonTrading Co.",
        BASE_URL,
    ]
    return _send(to_email, subject, "\n".join(lines))


# ── 更新完了の通知（任意。既定では payments.py から呼んでいない） ──
def send_renewal_notice(to_email: str, license_key: str, plan: str,
                        expires_at: str | None = None) -> bool:
    subject = "Your JobSearch subscription has been renewed"
    lines = [
        "Your JobSearch subscription has been renewed. Thank you.",
        "",
        f"Licence key: {license_key}",
        f"Plan: {_plan_text(plan)}",
    ]
    if expires_at:
        lines.append(f"Valid until: {expires_at}")
    lines += [
        "",
        "Your existing key continues to work — there is nothing to do.",
        "",
        f"Questions? Write to {SUPPORT_EMAIL}",
        "",
        "DoubleMoonTrading Co.",
        BASE_URL,
    ]
    return _send(to_email, subject, "\n".join(lines))


# ── 動作確認用（Renderのシェルから実行できる） ──────────────
# python -c "import mailer; mailer.send_license_key('宛先@example.com','DMJS-TEST-TEST-TEST','1month')"
