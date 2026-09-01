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
#   SMTP_USER      mp_license@doublemoon.biz
#   SMTP_PASSWORD  （さくらのメールパスワード）
#   MAIL_FROM      mp_license@doublemoon.biz     ※省略時は SMTP_USER
#   MAIL_FROM_NAME MOONpicker                    ※省略可
#   SUPPORT_EMAIL  moonpicker_support@doublemoon.biz  ※省略可
#
#   【2026-09-01 実測】Render には MAIL_FROM / MAIL_FROM_NAME を設定していない。
#   差出人アドレスは SMTP_USER が流用され、差出人名はこのファイルの既定値が効く。
#   SUPPORT_EMAIL は 2026-09-01 に Render へ追加済み。
#   ★環境変数はDBのバックアップに含まれない（対応予定 No.25）。
#     Renderを作り直すと未設定に戻るため、既定値も新アドレスに揃えてある。
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
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "MOONpicker")
SUPPORT_EMAIL  = os.environ.get("SUPPORT_EMAIL", "moonpicker_support@doublemoon.biz")
BASE_URL       = os.environ.get("BASE_URL", "https://jobsearch.doublemoon.biz")

SMTP_TIMEOUT = 20  # 秒。Webhookの応答が遅れすぎないように短めにする。

# 顧客向け表示は英語。plans.py の label は管理画面用（日本語）のため使わない。
PLAN_TEXT_EN = {
    "1month":     ("Basic", 100),
    "1month_pro": ("Pro",   300),
    # 無料トライアル（v3.33）。顧客向け表記は「Trial」。
    "trial":      ("Trial",  10),
}


def is_configured() -> bool:
    """送信に必要な設定が揃っているか。"""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and MAIL_FROM)


def _plan_text(plan: str) -> str:
    name, cap = PLAN_TEXT_EN.get(plan, ("Standard", 100))
    # トライアルで「per month」と書くと、毎月10回もらえるように読める。
    # 実際は1か月の試用期間中に通算10回なので、期間の表現を落とす。
    if plan == "trial":
        return f"{name} — {cap} job evaluations"
    return f"{name} — {cap} job evaluations per month"


# ── 顧客向けプラン表記（メール・購入完了ページ共通の単一情報源）─────
# plans.py の label は管理画面用の日本語（例：「Standard（月100回）」）。
# 顧客向けの画面・メールは英語のため、必ずこちらを使うこと。
# 混在させると、同じライセンスなのにメールと画面で表記が食い違う。
def plan_text_en(plan: str) -> str:
    """例: "Basic — 100 job evaluations per month" """
    return _plan_text(plan)


def plan_name_en(plan: str) -> str:
    """商品名だけ。例: "Basic" """
    return PLAN_TEXT_EN.get(plan, ("Standard", 100))[0]


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


# ── トライアル用の案内（v3.33）───────────────────────────
# 【日付を書かない理由】2026-08-30 決定。
#   こちらが持っているのは trial_end を日付単位に丸めた値で、Polar が実際に
#   課金する瞬間（時刻・UTC）とは1日ずれ得る。またカード決済が失敗すると
#   Polar 側だけが最長21日後ろへずれる。「書いた日付に課金されなかった」は
#   金額が小さくても信用を損なうため、正確な日付は Polar の確認メールに任せる。
def _trial_body(license_key: str) -> str:
    lines = [
        "Thanks for trying MOONpicker.",
        "",
        "Your licence key:",
        f"    {license_key}",
        "",
        "Your trial includes 10 evaluations over one month.",
        "",
        "One evaluation scores a whole batch of listings at once, so paste in",
        "everything you are considering rather than one job at a time. Ten batches",
        "is enough to see whether the scores match your own judgement.",
        "",
        "Fill in your profile first — skills, target rate, keywords. The scores are",
        "only as good as what you tell it about you. Your profile stays in your own",
        "browser, not on our servers.",
        "",
        "After the trial, MOONpicker Basic continues at $9 a month with 100",
        "evaluations a month. Polar has emailed you the exact date, and you can",
        "cancel at any time before then.",
        "",
        "How to start",
        f"  1. Open {BASE_URL}/app/upwork",
        "  2. Paste the licence key on the sign-in screen and accept the privacy policy.",
        "  3. Fill in your profile, then paste in your job search results.",
        "",
        "Keep this email. The key is how you sign in on a new device or browser.",
        "",
        f"Questions? Just reply to this email, or write to {SUPPORT_EMAIL}",
        "",
        "DoubleMoonTrading Co.",
        BASE_URL,
    ]
    return "\n".join(lines)


# ── ライセンスキーの送付（新規発行時） ─────────────────────
def send_license_key(to_email: str, license_key: str, plan: str,
                     expires_at: str | None = None) -> bool:
    # トライアルは本文を丸ごと入れ替える（v3.33）。
    # 通常の文面に分岐を混ぜると、どちらも読みにくくなるため分けている。
    if plan == "trial":
        return _send(to_email, "Your MOONpicker trial is ready",
                     _trial_body(license_key))

    subject = "Your MOONpicker licence key"
    lines = [
        "Thank you for subscribing to MOONpicker.",
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
    subject = "Your MOONpicker subscription has been renewed"
    lines = [
        "Your MOONpicker subscription has been renewed. Thank you.",
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


# ── 送信失敗の通知（運営者宛て） ───────────────────────────
def send_failure_alert(customer_email: str, license_key: str, plan: str,
                       reason: str = "") -> bool:
    """顧客へのキー送付に失敗したことを運営者へ知らせる。

    【限界】SMTPそのものが落ちている場合、この通知も届かない。
    そのため管理画面の「キー送付＝未送信（赤）」表示と併用する前提。
    この関数の戻り値は記録しない（通知の成否まで追うと切りがないため）。
    """
    if not SUPPORT_EMAIL:
        return False
    subject = f"[MOONpicker] ライセンスキーの送付に失敗しました（{customer_email}）"
    lines = [
        "決済は完了しましたが、購入者へのライセンスキー送付に失敗しました。",
        "手動での対応が必要です。",
        "",
        f"  宛先        : {customer_email}",
        f"  ライセンスキー: {license_key}",
        f"  プラン      : {plan_text_en(plan)}",
        f"  失敗の内容  : {reason or '(不明)'}",
        "",
        "対応方法",
        f"  1. {BASE_URL}/admin/licenses を開く",
        "  2. 該当のライセンス（キー送付が「未送信」と赤く表示されている行）を探す",
        "  3. 「キー再送」ボタンを押す",
        "",
        "  ボタンで送れない場合は、SMTPの設定（さくらのパスワード変更など）を",
        "  確認してください。復旧後に再送すれば、状態は「送信済」に戻ります。",
        "",
        "  なお購入者は、購入直後であれば購入完了ページでキーを確認できます",
        "  （発行から30分以内）。",
        "",
        "MOONpicker システム通知",
    ]
    return _send(SUPPORT_EMAIL, subject, "\n".join(lines))


# ── 動作確認用（Renderのシェルから実行できる） ──────────────
# python -c "import mailer; mailer.send_license_key('宛先@example.com','DMJS-TEST-TEST-TEST','1month')"
