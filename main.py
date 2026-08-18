"""
main.py - JobSearch 本番サーバー v3.8（マルチ求人サイト対応）
ライセンス認証＋プロンプト/セレクター配信型
"""
import os
import re
import io
import csv
import json
import logging
import secrets as sec_module
from datetime import datetime, timedelta

# アプリ側の log.info(...) を Render のログに出す。
# 未設定だとルートロガーが WARNING のままで、決済Webhookの
# "license issued ..." などが一切表示されず、障害調査ができない。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")

from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse, Response, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import (
    init_db,
    create_license, extend_license,
    get_all_licenses, export_licenses_csv,
    get_license_stats, search_licenses, delete_license, delete_expired_licenses,
    get_license_with_config,
    get_all_prompts, create_prompt, update_prompt, activate_prompt,
    get_latest_version,
    upload_file, get_active_file, get_all_files, activate_file, delete_file,
)

# ── リデザイン追加分 ──
from db_redesign import (                                  # DBマイグレーション＋広告欄＋プラン変更
    migrate, get_promo, save_promo, apply_plan_change,
    find_license_by_checkout, set_mail_status,             # /thanks でのキー表示・メール送付状態
    get_license_row,                                       # キー再送で使用
    record_referral_visit, referral_exists,                # 紹介リンク（SNS流入計測）
    list_referrals, create_referral, set_referral_active,
    referral_stats, referral_detail_rows, referral_site,
    list_staff, create_staff, set_staff_active, set_staff_password,  # 担当者マスタ
    active_staff_names,
    verify_staff,                                          # スタッフのログイン照合
)
from plans import (                                      # プラン定義（単一情報源）
    PLANS, plan_label, is_valid_plan, plan_price_usd,
)
from sites import (                                      # 対応求人サイト定義（単一情報源）
    SITES, DEFAULT_SITE, get_site, is_valid_site, site_label, enabled_sites,
)
from evaluate import router as evaluate_router  # 採点API: POST /evaluate
from payments import router as payments_router  # 決済Webhook: POST /webhook/{provider}

# ══════════════════════════════════════════
# 初期化
# ══════════════════════════════════════════
app = FastAPI(title="JobSearch API", version="3.8.0")
init_db()
migrate()   # リデザイン: subscription_id/provider 列・usage_tracking 表を用意（既適用でも安全）

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://jobsearch.doublemoon.biz",   # 新・正式ドメイン
        "https://upwork.doublemoon.biz",      # 旧ドメイン（リダイレクト移行期間中のみ。落ち着いたら削除可）
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# リデザイン: 採点API と 決済Webhook を登録
app.include_router(evaluate_router)   # POST /evaluate
app.include_router(payments_router)   # POST /webhook/{provider}  (例: /webhook/polar)

# 環境変数
ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
BASE_URL       = os.environ.get("BASE_URL",       "https://jobsearch.doublemoon.biz")

# 旧ドメイン。アクセスが来たら BASE_URL 側へ恒久リダイレクトする。
# サイト別サブドメインを廃止し、1オリジン＋パス分割に統一したため。
LEGACY_HOSTS = {
    "upwork.doublemoon.biz": "/for/upwork",   # 旧トップ → Upwork向けLPへ
}

# 管理画面からアップロードできる配布ファイルの種類と Content-Type。
# （Chrome拡張はリデザインで廃止したため excel のみ）
ALLOWED_FILE_COMPONENTS = {
    "excel": "application/vnd.ms-excel.sheet.macroEnabled.12",
}

security = HTTPBasic()


# ── 管理画面の表示文言（日本語／英語）────────────────────
# スタッフは日本語話者とは限らないため、スタッフとしてログインした場合だけ
# 英語で表示する。画面そのものは複製せず1枚のまま言語を差し替える。
# （同じ機能の画面を2枚持つと、以後の修正が常に2箇所になるため）
#
# ここに入れているのは「画面に書かれている文字」だけ。
# サーバーが返すエラー文（database.py などの戻り値）は日本語のままなので、
# 異常時のメッセージは日本語で出る。まず画面文言だけを対象とする判断。
UI_TEXT = {
    "ja": {
        "role_admin": "システム管理者", "role_staff": "スタッフ",
        # ライセンス一覧
        "lic_title": "ライセンス一覧", "lic_h1": "📋 ライセンス一覧",
        "back_admin": "← 管理画面に戻る", "back_staff": "← スタッフ画面",
        "link_referrals": "紹介リンク管理", "link_licenses": "ライセンス一覧",
        "st_total": "総数", "st_active": "有効",
        "st_expired": "期限切れ", "st_inactive": "無効化",
        "badge_inactive": "無効化", "badge_expired": "期限切れ", "badge_active": "有効",
        "kind_paid": "決済", "kind_manual": "手動",
        "mail_sent": "送信済", "mail_failed": "未送信", "mail_manual": "手動発行",
        "plan_legacy": "（旧）",
        "btn_extend": "+1ヶ月", "btn_plan": "プラン変更",
        "btn_resend": "キー再送", "btn_delete": "削除",
        "empty_licenses": "該当するライセンスはありません",
        "pg_prev": "← 前へ", "pg_next": "次へ →",
        "search_h2": "🔍 検索・絞り込み", "kw_label": "キーワード",
        "kw_ph": "ライセンスキー / メール", "status_label": "状態",
        "opt_all": "すべて", "btn_search": "検索", "link_clear": "クリア",
        "list_h2": "📋 一覧",
        "th_id": "ID", "th_key": "ライセンスキー", "th_email": "メール",
        "th_plan": "プラン", "th_kind": "種別", "th_mail": "キー送付",
        "th_status": "状態", "th_expires": "有効期限", "th_ops": "操作",
        "bulk_h2": "🗑 期限切れライセンスの一括削除",
        "bulk_warn_1": "<b>削除すると元に戻せません。</b>実行前に",
        "bulk_warn_link": "CSVバックアップ",
        "bulk_warn_2": "を取得してください。",
        "bulk_warn_3": ("決済に紐づくライセンスは、失効から30日経過するまで削除されません"
                        "（支払いリトライ中に削除すると、更新時に別のキーが発行されてしまうため）。"
                        "条件を満たさないものは自動的にスキップされます。"),
        "bulk_since": "失効から", "btn_bulk_delete": "まとめて削除",
        # ライセンス一覧のJS
        "js_resend_confirm": "このライセンスキーをメールで送信しますか？",
        "js_resend_to": "宛先: ", "js_sent": "送信しました: ",
        "js_error": "エラー: ", "js_unknown": "不明なエラー",
        "js_extend_confirm": " を1ヶ月延長しますか？",
        "js_extended": "延長完了。新しい有効期限: ",
        "js_plan_confirm_1": " のプランを「", "js_plan_confirm_2": "」に変更しますか？",
        "js_plan_note": "・有効期限は変わりません\\n・上位プランへの変更時は、旧プランの残り回数を繰り越します",
        "js_plan_done": "プランを変更しました：", "js_plan_keep": "有効期限は変更していません。",
        "js_plan_carry_1": "旧プランの残り ", "js_plan_carry_2": " 回を繰り越しました。",
        "js_plan_left_1": "今月の残り回数：", "js_plan_left_2": " 回（使用済み ",
        "js_plan_left_3": " 回）",
        "js_del_confirm": "このライセンスを削除しますか？",
        "js_del_warn": "削除すると元に戻せません。",
        "js_deleted": "削除しました：", "js_del_failed": "削除できませんでした",
        "js_bulk_1": "失効から", "js_bulk_2": "日以上経過したライセンスをまとめて削除します。",
        "js_bulk_3": "元に戻せません。CSVバックアップは取得済みですか？",
        "js_bulk_final": "本当に実行しますか？（最終確認）",
        "js_deleting": "削除中...", "js_bulk_done_1": " 件を削除しました。",
        "js_bulk_skip_1": "（条件を満たさない ", "js_bulk_skip_2": " 件はスキップ）",
        "js_generic_error": "エラーが発生しました",
        # 紹介リンク管理
        "ref_title": "紹介リンク管理", "ref_h1": "🔗 紹介リンク管理",
        "back_admin_top": "← 管理トップ",
        "ref_reg_h2": "コードを登録",
        "ref_owner_blank": "担当者を選択",
        "ref_site_blank": "着地先を選択",
        "rth_site": "着地先",
        "js_ref_need_site": "着地先を選択してください",
        "ref_no_staff": "担当者マスタに有効な担当者がいないため登録できません。"
                        "管理画面の「担当者マスタ」から登録してください。",
        "js_ref_need_owner": "担当者を選択してください",
        "ref_opt_channel": "種別", "ref_ph_note": "メモ（任意）",
        "ref_btn_register": "登録",
        "ref_hint_1": "付け方：<code>担当者-種別-年月日+英字</code>（例 <code>{sample}</code>）。"
                      " <b>投稿1本につき1コード</b>を作ってください（使い回すと、どの投稿が効いたか分けられません）。<br>"
                      "日付は<b>年月日の6桁</b>。年を入れないと翌年の同じ日付と重複します。"
                      " 末尾は<b>必ず英字1文字</b>で、1本目から <code>a</code> を付けます。"
                      "同じ日に同じSNSへ複数回投稿する場合は <code>b</code> <code>c</code> と続けます。<br>"
                      " 外部のインフルエンサーは <code>infl-tanaka</code> のように日付なしにすると使い回せます。<br>"
                      "<b>着地先</b>は、そのリンクを踏んだ人が見るLPです。投稿で紹介する求人サイトに合わせてください。",
        "ref_hint_2": "登録すると <code>{url}</code> が使えるようになります。",
        "ref_stats_h2": "成績",
        "tab_all": "全期間", "tab_this_month": "今月",
        "tab_last_month": "先月", "tab_30d": "過去30日",
        "rth_code": "コード", "rth_channel": "種別", "rth_owner": "担当者",
        "rth_visits": "訪問", "rth_purchases": "購入", "rth_cvr": "転換率",
        "rth_active": "継続中", "rth_mrr": "MRR", "rth_state": "状態",
        "rth_note": "メモ", "rth_ops": "操作",
        "ref_state_active": "有効", "ref_state_stopped": "停止中",
        "ref_btn_stop": "停止", "ref_btn_resume": "再開", "ref_btn_copy": "URLコピー",
        "ref_empty": "紹介コードがまだ登録されていません",
        "ref_note_1": "<b>訪問・購入</b>は選択した期間内の件数。<b>継続中・MRR</b>は期間に関係なく「現時点」の値です。",
        "ref_note_2": "ロボットと判定したアクセスは訪問数から除いています（括弧内が除外数）。",
        "ref_note_3": "スマホで踏んでPCで購入した場合などは追跡できないため、実際の貢献はこの数字より多くなります。"
                      " 傾向の比較には使えますが、絶対値として信用しすぎないでください。",
        "ref_csv_h2": "CSVダウンロード",
        "ref_csv_sum": "集計CSV（この期間）", "ref_csv_detail": "明細CSV（ライセンス1件ごと）",
        "js_ref_need_code": "コードを入力してください",
        "js_ref_toggle_failed": "変更できませんでした",
        "js_ref_copied": "コピーしました: ",
    },
    "en": {
        "role_admin": "Administrator", "role_staff": "Staff",
        "lic_title": "License list", "lic_h1": "📋 License list",
        "back_admin": "← Back to admin", "back_staff": "← Staff Console",
        "link_referrals": "Referral links", "link_licenses": "License list",
        "st_total": "Total", "st_active": "Active",
        "st_expired": "Expired", "st_inactive": "Deactivated",
        "badge_inactive": "Deactivated", "badge_expired": "Expired", "badge_active": "Active",
        "kind_paid": "Paid", "kind_manual": "Manual",
        "mail_sent": "Sent", "mail_failed": "Not sent", "mail_manual": "Issued manually",
        "plan_legacy": " (legacy)",
        "btn_extend": "+1 month", "btn_plan": "Change plan",
        "btn_resend": "Resend key", "btn_delete": "Delete",
        "empty_licenses": "No licenses match your search",
        "pg_prev": "← Prev", "pg_next": "Next →",
        "search_h2": "🔍 Search", "kw_label": "Keyword",
        "kw_ph": "license key / email", "status_label": "Status",
        "opt_all": "All", "btn_search": "Search", "link_clear": "Clear",
        "list_h2": "📋 Licenses",
        "th_id": "ID", "th_key": "License key", "th_email": "Email",
        "th_plan": "Plan", "th_kind": "Source", "th_mail": "Key delivery",
        "th_status": "Status", "th_expires": "Expires", "th_ops": "Actions",
        "bulk_h2": "🗑 Bulk delete expired licenses",
        "bulk_warn_1": "<b>This cannot be undone.</b> Before you run it, download a",
        "bulk_warn_link": "CSV backup",
        "bulk_warn_2": ".",
        "bulk_warn_3": ("Licenses tied to a payment are kept for 30 days after they expire "
                        "(deleting one mid-retry would issue a different key on renewal). "
                        "Anything that doesn't qualify is skipped automatically."),
        "bulk_since": "Expired for", "btn_bulk_delete": "Delete them",
        "js_resend_confirm": "Email this license key?",
        "js_resend_to": "To: ", "js_sent": "Sent to: ",
        "js_error": "Error: ", "js_unknown": "Unknown error",
        "js_extend_confirm": " — extend by one month?",
        "js_extended": "Extended. New expiry date: ",
        "js_plan_confirm_1": " — change the plan to ", "js_plan_confirm_2": "?",
        "js_plan_note": "- The expiry date stays the same\\n- Unused evaluations carry over when moving to a higher plan",
        "js_plan_done": "Plan changed: ", "js_plan_keep": "The expiry date was not changed.",
        "js_plan_carry_1": "Carried over ", "js_plan_carry_2": " evaluations from the old plan.",
        "js_plan_left_1": "Remaining this month: ", "js_plan_left_2": " (used ",
        "js_plan_left_3": ")",
        "js_del_confirm": "Delete this license?",
        "js_del_warn": "This cannot be undone.",
        "js_deleted": "Deleted: ", "js_del_failed": "Could not delete",
        "js_bulk_1": "Delete every license that expired more than ", "js_bulk_2": " days ago.",
        "js_bulk_3": "This cannot be undone. Have you downloaded the CSV backup?",
        "js_bulk_final": "Run it now? (final confirmation)",
        "js_deleting": "Deleting...", "js_bulk_done_1": " deleted.",
        "js_bulk_skip_1": " (", "js_bulk_skip_2": " skipped — they did not qualify)",
        "js_generic_error": "Something went wrong",
        "ref_title": "Referral links", "ref_h1": "🔗 Referral links",
        "back_admin_top": "← Admin home",
        "ref_reg_h2": "Add a code",
        "ref_owner_blank": "Select owner",
        "ref_site_blank": "Select landing page",
        "rth_site": "Landing",
        "js_ref_need_site": "Please select a landing page.",
        "ref_no_staff": "No active owners are registered yet, so codes cannot be added. "
                        "Ask the administrator to add one.",
        "js_ref_need_owner": "Please select an owner.",
        "ref_opt_channel": "Channel", "ref_ph_note": "note (optional)",
        "ref_btn_register": "Add",
        "ref_hint_1": "Format: <code>owner-channel-date+letter</code> (e.g. <code>{sample}</code>)."
                      " <b>Create one code per post</b> — reusing a code makes it impossible to tell which post worked.<br>"
                      "Use a <b>six-digit date</b>; without the year, next year's dates would clash with this year's."
                      " Always end with <b>a letter</b> — start with <code>a</code> on the first post, then"
                      " <code>b</code>, <code>c</code> for further posts to the same platform on the same day.<br>"
                      " For outside influencers, drop the date — <code>infl-tanaka</code> — so the code can be reused.<br>"
                      "<b>Landing</b> is the page people see when they follow your link."
                      " Match it to the job board you are posting about.",
        "ref_hint_2": "Once added, <code>{url}</code> becomes available.",
        "ref_stats_h2": "Results",
        "tab_all": "All time", "tab_this_month": "This month",
        "tab_last_month": "Last month", "tab_30d": "Last 30 days",
        "rth_code": "Code", "rth_channel": "Channel", "rth_owner": "Owner",
        "rth_visits": "Visits", "rth_purchases": "Purchases", "rth_cvr": "Conv.",
        "rth_active": "Still active", "rth_mrr": "MRR", "rth_state": "State",
        "rth_note": "Note", "rth_ops": "Actions",
        "ref_state_active": "Active", "ref_state_stopped": "Stopped",
        "ref_btn_stop": "Stop", "ref_btn_resume": "Resume", "ref_btn_copy": "Copy URL",
        "ref_empty": "No referral codes yet",
        "ref_note_1": "<b>Visits and purchases</b> cover the period you picked."
                      " <b>Still active and MRR</b> are current figures, whatever period is selected.",
        "ref_note_2": "Traffic identified as bots is excluded from the visit count (the number in brackets).",
        "ref_note_3": "A visit on a phone followed by a purchase on a laptop can't be tracked, so the real"
                      " contribution is higher than what you see. Use these numbers to compare trends,"
                      " not as exact totals.",
        "ref_csv_h2": "Download CSV",
        "ref_csv_sum": "Summary (selected period)", "ref_csv_detail": "Detail (one row per license)",
        "js_ref_need_code": "Please enter a code.",
        "js_ref_toggle_failed": "Could not change it.",
        "js_ref_copied": "Copied: ",
    },
}


def ui_text(who: dict) -> dict:
    """ログインしている人に合わせて画面文言の辞書を返す。"""
    return UI_TEXT["ja"] if who.get("role") == "admin" else UI_TEXT["en"]


def plan_label_ui(info: dict, en: bool) -> str:
    """プラン名を画面に出す形にする。英語では PLANS の label（日本語）から
    名称部分だけを取り出し、回数を英語で添える。プランが増えても
    ここを直さずに済むよう、定義から組み立てる。"""
    if not en:
        return info["label"]
    return f'{info["label"].split("（")[0]} ({info["monthly_cap"]}/month)'


def esc(v) -> str:
    """管理画面HTMLへ値を埋め込む際のエスケープ（属性値・テキスト共用）。"""
    return (
        str("" if v is None else v)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def _identify(credentials: HTTPBasicCredentials) -> dict | None:
    """Basic認証の資格情報から「誰か」を判定する。判定できなければ None。

    管理者は環境変数、スタッフは staff_members テーブルと照合する。
    フォーム認証やセッションは導入していない（画面もCookieも不要で、
    既存の全ルートを書き換えずに済むため）。その代わりログアウトはできない。
    """
    try:
        ok_user = sec_module.compare_digest(credentials.username, ADMIN_USER)
        ok_pass = sec_module.compare_digest(credentials.password, ADMIN_PASSWORD)
    except TypeError:
        # compare_digest は非ASCIIの str を受け付けない。
        # 管理者IDは英数字なので、この時点で管理者ではないと判断してよい。
        ok_user = ok_pass = False
    if ok_user and ok_pass:
        return {"role": "admin", "login_id": credentials.username,
                "display_name": credentials.username}

    staff = verify_staff(credentials.username, credentials.password)
    if staff:
        return {"role": "staff", **staff}
    return None


def _unauthorized():
    raise HTTPException(
        status_code=401, detail="認証失敗",
        headers={"WWW-Authenticate": "Basic"},
    )


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """システム管理者だけが通れる。スタッフの資格情報では 403 で弾く。

    401 ではなく 403 を返すのは、401 だとブラウザが再度パスワードを尋ね、
    スタッフが何度入れ直しても入れない状態になるため。403 なら
    「権限がない」ことが本人に伝わる。
    """
    who = _identify(credentials)
    if not who:
        _unauthorized()
    if who["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="この画面はシステム管理者のみ利用できます",
        )
    return who["login_id"]


def verify_any(credentials: HTTPBasicCredentials = Depends(security)) -> dict:
    """管理者・スタッフのどちらでも通れる。誰であるかを dict で返す。

    返り値の role / display_name を使って、画面の表示内容や
    紹介リンクの担当者を切り替える。
    """
    who = _identify(credentials)
    if not who:
        _unauthorized()
    return who


# ══════════════════════════════════════════
# 基本エンドポイント
# ══════════════════════════════════════════
@app.middleware("http")
async def redirect_legacy_hosts(request: Request, call_next):
    """旧サブドメイン(upwork.doublemoon.biz等)へのアクセスを新ドメインへ転送。
    既存のブックマーク・SNS投稿・外部リンクを切らさないための措置。"""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in LEGACY_HOSTS:
        path = request.url.path
        # 旧トップ("/")だけは、そのサイト向けLPへ振り替える
        target = LEGACY_HOSTS[host] if path == "/" else path
        url = f"{BASE_URL}{target}"
        if request.url.query:
            url += f"?{request.url.query}"
        # GET/HEAD は301。それ以外はメソッドとボディを保つ308を使う。
        code = 301 if request.method in ("GET", "HEAD") else 308
        return RedirectResponse(url, status_code=code)
    return await call_next(request)


def _serve_html(*candidates: str, fallback: str = "<h1>Not found</h1>",
                status: int = 404) -> HTMLResponse:
    """候補パスを順に探して最初に見つかったHTMLを返す。見つからなければ fallback。"""
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content=fallback, status_code=status)


@app.get("/", response_class=HTMLResponse)
async def root():
    # トップページ: 全サービス共通のハブ。各ジョブサイト向けLPへの入口。
    return _serve_html(
        "frontend/hub.html", "hub.html",
        fallback="<h1>JobSearch</h1><p>hub.html not found.</p>",
        status=200,
    )


@app.get("/r/{code}")
async def referral_redirect(code: str, request: Request):
    """紹介リンク。訪問を記録してLPへ送る。

    SNSやインフルエンサーに配る短縮URL。/r/ という名前空間に置くことで、
    将来 /for/{site} などのパスと衝突しないようにしている。

    未登録のコードでも404にはせず、普通にLPを表示する。
    宣伝リンクを踏んだ人にエラー画面を見せる方が損失が大きいため
    （記録だけが行われない）。
    """
    code = (code or "").strip()
    # コードの形式を制限（英数字・ハイフン・アンダースコアのみ）
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
        try:
            record_referral_visit(code, request.headers.get("user-agent", ""))
        except Exception as e:
            # 記録に失敗してもリダイレクトは続ける。計測は付加機能であり、
            # ここで止めると宣伝リンクそのものが機能しなくなる。
            log.error("could not record referral visit (%s): %s", code, e)

        # 着地先はコードごとに持つ。Freelancer.com の投稿からUpworkのLPへ
        # 飛ばすと内容が噛み合わず、購入につながらないため。
        # 何があってもLPには着地させる（宣伝リンクの入口であり、
        # エラー画面を見せる方が損失が大きい）。次のいずれも既定サイトへ送る：
        #   ・未登録のコード ・DB参照に失敗 ・記録された着地先が後で無効化された
        site = DEFAULT_SITE
        try:
            recorded = referral_site(code)
            if recorded and is_valid_site(recorded):
                site = recorded
        except Exception as e:
            log.error("could not resolve referral site (%s): %s", code, e)
        return RedirectResponse(f"/for/{site}?ref={code}", status_code=302)
    return RedirectResponse("/", status_code=302)


@app.get("/for/{site}", response_class=HTMLResponse)
async def landing_page(site: str):
    """ジョブサイト別のLP。frontend/landing_{site}.html を配信する。
    新サイト追加時はHTMLを1枚置くだけでよく、コード変更は不要。"""
    # パストラバーサル防止（英小文字・数字・ハイフンのみ許可）
    if not re.fullmatch(r"[a-z0-9-]{1,32}", site):
        return HTMLResponse(content="<h1>Not found</h1>", status_code=404)
    return _serve_html(
        f"frontend/landing_{site}.html", f"landing_{site}.html",
        fallback="<h1>Not found</h1>", status=404,
    )


@app.get("/app")
async def app_root():
    """サイト未指定のアプリURL。ハブへ戻し、そこで対応サイトを選んでもらう。
    旧 /app のブックマークを切らさないための恒久リダイレクト。"""
    return RedirectResponse("/", status_code=301)


@app.get("/app/{site}", response_class=HTMLResponse)
async def app_page(site: str):
    """求人サイト別のアプリ本体。HTMLは1枚を共用し、
    サイト固有の文言だけ sites.py の定義を差し込む。
    1ライセンスで全対応サイトを利用できる点は従来どおり。"""
    site = site.lower()
    if not re.fullmatch(r"[a-z0-9-]{1,32}", site) or not is_valid_site(site):
        return HTMLResponse(content="<h1>Not found</h1>", status_code=404)

    conf = get_site(site)
    for path in ("frontend/index.html", "index.html"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                html = f.read()
            # サイト固有文言の差し込み。値はHTML属性にも入るためエスケープする。
            for key, value in (
                ("SITE_ID",            site),
                ("SITE_LABEL",         conf["label"]),
                ("PASTE_HEADING",      conf["paste_heading"]),
                ("PASTE_PLACEHOLDER",  conf["paste_placeholder"]),
                ("PASTE_TIP",          conf.get("paste_tip", "")),
                ("CSV_FILENAME",       conf["csv_filename"]),
            ):
                html = html.replace("{{" + key + "}}", esc(value))
            return HTMLResponse(content=html)

    return HTMLResponse(
        content="<h1>JobSearch</h1><p>frontend (index.html) not found.</p>",
        status_code=200,
    )


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    # プライバシーポリシー
    for path in ("frontend/privacy.html", "privacy.html"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Not found</h1>", status_code=404)


@app.get("/campaign", response_class=HTMLResponse)
async def campaign_page():
    # SNS拡散キャンペーンの申請ページ
    for path in ("frontend/campaign.html", "campaign.html"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Not found</h1>", status_code=404)


@app.get("/thanks", response_class=HTMLResponse)
async def thanks_page():
    # 決済完了ページ。Polar の Success URL の遷移先。
    # ライセンスキーはメールで送付するため、この画面ではキーを表示しない
    # （Webhookの到着を待つポーリングは行わない方針）。
    for path in ("frontend/thanks.html", "thanks.html"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Not found</h1>", status_code=404)


@app.get("/health")
async def health():
    return {"service": "JobSearch API", "version": "3.8.0", "status": "running"}


# ── 広告欄 ──
@app.get("/promo")
async def promo_get():
    """フロントが起動時に取得。未設定・OFFなら表示されない。"""
    return get_promo()


@app.post("/admin/promo/save")
async def admin_promo_save(request: Request, username: str = Depends(verify_admin)):
    data = await request.json()
    return save_promo(
        data.get("title", ""), data.get("body", ""),
        data.get("url", ""), bool(data.get("enabled")),
    )


# ── 担当者マスタ（システム管理者のみ）──────────────────────
@app.post("/admin/staff/create")
async def admin_staff_create(request: Request, username: str = Depends(verify_admin)):
    """担当者を登録する。パスワードはハッシュ化して保存される。"""
    data = await request.json()
    return create_staff(
        login_id     = data.get("login_id", ""),
        display_name = data.get("display_name", ""),
        password     = data.get("password", ""),
        note         = data.get("note", ""),
    )


@app.post("/admin/staff/{staff_id}/active")
async def admin_staff_active(staff_id: int, request: Request,
                             username: str = Depends(verify_admin)):
    """有効／停止の切り替え。削除は用意していない（実績が追えなくなるため）。"""
    data = await request.json()
    return set_staff_active(staff_id, bool(data.get("active")))


@app.post("/admin/staff/{staff_id}/password")
async def admin_staff_password(staff_id: int, request: Request,
                               username: str = Depends(verify_admin)):
    """パスワードの再設定。本人からは変更できないため、管理者が行う。"""
    data = await request.json()
    return set_staff_password(staff_id, data.get("password", ""))


@app.get("/ping")
async def ping():
    return {
        "status":      "ok",
        "server_time": datetime.utcnow().isoformat() + "Z",
    }


# ══════════════════════════════════════════
# ライセンス認証＋設定一括取得API（メイン）
# ══════════════════════════════════════════
@app.post("/license/validate")
async def license_validate(request: Request):
    """
    ライセンスキーの認証＋プロンプト・セレクター・除外リスト・AI設定を一括返却
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "リクエスト形式が不正です"})

    license_key = body.get("license_key", "").strip()
    if not license_key:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "license_keyが未送信です"})

    result = get_license_with_config(license_key)

    if result.get("status") != "valid":
        return JSONResponse(status_code=403, content=result)

    return result


# ══════════════════════════════════════════
# 購入完了ページ用：チェックアウトIDからライセンスキーを引く
# ══════════════════════════════════════════
# /thanks が checkout_id を持って問い合わせてくる。メール送付は従来どおり行い、
# こちらは「画面にも出す」ための二重化。メールが届かなくても購入者が
# その場でキーを受け取れるようにするのが目的。
#
# 【安全側の設計】
#  ・発行から一定時間（db_redesign.CHECKOUT_LOOKUP_WINDOW_MINUTES）だけ返す。
#  ・該当なし・時間切れ・無効化済みは、すべて同じ応答（found=false）にする。
#    区別するとIDの推測に手がかりを与えるため。
#  ・常に200で返す。/thanks 側はキーが取れなければメール案内のまま表示する。
@app.get("/license/by-checkout/{checkout_id}")
async def license_by_checkout(checkout_id: str):
    checkout_id = (checkout_id or "").strip()
    # PolarのチェックアウトIDはUUID形式。極端な長さの入力はここで弾く。
    if not checkout_id or len(checkout_id) > 100:
        return {"found": False}
    try:
        lic = find_license_by_checkout(checkout_id)
    except Exception as e:
        log.error("checkout lookup failed: %s", e)
        return {"found": False}
    if not lic:
        return {"found": False}
    # プラン表記は顧客向けの英語を使う。plans.py の label は管理画面用の
    # 日本語（例：「Standard（月100回）」）なので、ここで使うとメール本文と
    # 表記が食い違う。mailer.py を単一の情報源にする。
    try:
        import mailer
        plan_label = mailer.plan_text_en(lic["plan"])
    except Exception:
        plan_label = ""
    return {
        "found": True,
        "license_key": lic["license_key"],
        "plan": lic["plan"],
        "plan_label": plan_label,
        "expires_at": str(lic["expires_at"]),
    }


# ══════════════════════════════════════════
# バージョン確認API
# ══════════════════════════════════════════
@app.get("/version/{component}")
async def get_version(component: str):
    ver = get_latest_version(component)
    if not ver:
        return JSONResponse(status_code=404,
            content={"message": f"コンポーネント '{component}' が見つかりません"})
    return ver


# ══════════════════════════════════════════
# マイページ（廃止）
# ══════════════════════════════════════════
# v3.0 で Excel/Chrome拡張を廃止したため、/mypage の内容
# （Excelビューアの配布・旧手順の説明）は現行仕様と矛盾する。
# ページ自体を削除するとURL直打ちで404になるため、ハブへ恒久リダイレクトする。
# ライセンスの残日数はアプリ本体のサインイン後に表示される。
@app.get("/mypage")
async def mypage():
    """旧マイページ。ハブ（/）へ恒久リダイレクト。"""
    return RedirectResponse("/", status_code=301)


# ══════════════════════════════════════════
# ファイルダウンロード（DBに保存されたファイルを配信）
# ══════════════════════════════════════════
@app.get("/download/excel")
async def download_excel():
    f = get_active_file("excel")
    if not f:
        return JSONResponse(status_code=404,
            content={"message": "ファイルが準備中です"})
    return Response(
        content=bytes(f["file_data"]),
        media_type=f["content_type"],
        headers={"Content-Disposition": f'attachment; filename={f["filename"]}'},
    )


# （リデザインで Chrome拡張は廃止したため /download/extension は削除）


# ══════════════════════════════════════════
# 管理者画面
# ══════════════════════════════════════════
# ══════════════════════════════════════════
# スタッフ用画面（英語表記）
# ══════════════════════════════════════════
@app.get("/staff", response_class=HTMLResponse)
async def staff_page(who: dict = Depends(verify_any)):
    """スタッフ用のトップ。管理者も開ける（動作確認のため）。

    英語表記なのは、スタッフが日本語話者とは限らないため。
    プロンプト・広告欄・配布ファイル・担当者マスタは置いていない
    （システム管理者のみが扱う項目）。
    """
    stats = get_license_stats()

    # プラン名を英語で出す。PLANS の label は日本語なので、
    # 名称部分だけを取り出して回数を英語で添える。
    # プランが増えてもここを直さずに済むよう、定義から組み立てる。
    plan_options = "".join(
        f'<option value="{esc(code)}">'
        f'{esc(info["label"].split("（")[0])} ({info["monthly_cap"]}/month)'
        f'</option>'
        for code, info in PLANS.items()
    )

    role_en = "Administrator" if who.get("role") == "admin" else "Staff"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Staff Console - JobSearch</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A; font-size: 13px; }}
    .header {{ background: #1A2B4A; color: white; padding: 14px 24px;
               display: flex; align-items: center; justify-content: space-between; }}
    .header h1 {{ font-size: 16px; }}
    .container {{ max-width: 1000px; margin: 24px auto; padding: 0 16px; }}
    .card {{ background: white; border-radius: 8px; padding: 20px;
             margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    .card h2 {{ font-size: 14px; color: #1A2B4A; margin-bottom: 14px;
                padding-bottom: 6px; border-bottom: 2px solid #EBF3FB; }}
    .form-row {{ display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }}
    .form-row label {{ font-size: 12px; color: #555; min-width: 60px; }}
    .form-row input, .form-row select {{
      padding: 7px 10px; border: 1px solid #BFCFDF; border-radius: 5px; font-size: 12px; }}
    .btn {{ padding: 8px 16px; border: none; border-radius: 5px;
            font-size: 12px; font-weight: bold; cursor: pointer; }}
    .btn-primary {{ background: #C55A11; color: white; }}
    .btn-blue    {{ background: #2E75B6; color: white; }}
    .msg {{ padding: 10px 14px; border-radius: 6px; margin-top: 10px;
            display: none; font-size: 12px; }}
    .msg.ok    {{ background: #E2EFDA; color: #375623; display: block; }}
    .msg.error {{ background: #FCE4D6; color: #843C0C; display: block; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }}
    .stat-card {{ background: #F0F6FC; border-radius: 6px; padding: 14px; text-align: center; }}
    .stat-num  {{ font-size: 22px; font-weight: bold; color: #1A2B4A; }}
    .stat-label{{ font-size: 11px; color: #777; margin-top: 4px; }}
    .note {{ font-size: 11px; color: #777; margin-top: 8px; line-height: 1.7; }}
  </style>
</head>
<body>
<div class="header">
  <h1>⚡ JobSearch — Staff Console</h1>
  <span style="font-size:11px;opacity:0.8">
    {esc(who.get('display_name') or '')} ({role_en})
  </span>
</div>

<div class="container">

  <div class="card">
    <h2>📊 Licenses at a glance</h2>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-num">{stats['total']}</div>
        <div class="stat-label">Total</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{stats['active']}</div>
        <div class="stat-label">Active</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" style="color:#843C0C">{stats['expired']}</div>
        <div class="stat-label">Expired</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>🔑 Issue a license</h2>
    <div class="form-row">
      <label>Email</label>
      <input type="email" id="new-email" placeholder="user@example.com" style="min-width:220px">
      <label>Plan</label>
      <select id="new-plan">{plan_options}</select>
      <label>Note</label>
      <input type="text" id="new-note" placeholder="optional" style="min-width:150px">
      <button class="btn btn-primary" onclick="issueLicense()">Issue</button>
    </div>
    <div id="issue-msg" class="msg"></div>
    <div class="note">
      The key is shown here after you issue it — <b>no email is sent automatically</b>.
      Copy it to the customer, or open the license list and use <b>Resend key</b> to email it.
    </div>
  </div>

  <div class="card">
    <h2>📋 License list</h2>
    <p style="font-size:12px;color:#777;margin-bottom:12px">
      Search, extend, change plan, resend a key, or remove a license.
    </p>
    <a href="/admin/licenses" class="btn btn-blue"
       style="text-decoration:none;display:inline-block">Open license list →</a>
  </div>

  <div class="card">
    <h2>🔗 Referral links</h2>
    <p style="font-size:12px;color:#777;margin-bottom:12px">
      Create the links you share on social media and see how many visits and
      purchases each one brought in.
    </p>
    <a href="/admin/referrals" class="btn btn-blue"
       style="text-decoration:none;display:inline-block">Open referral links →</a>
  </div>

</div>

<script>
async function issueLicense() {{
  const email = document.getElementById('new-email').value.trim();
  const plan  = document.getElementById('new-plan').value;
  const note  = document.getElementById('new-note').value.trim();
  const msg   = document.getElementById('issue-msg');
  if (!email) {{ alert('Please enter an email address.'); return; }}

  const res = await fetch('/admin/license/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{email, plan, note}}),
  }});
  let data = {{}};
  try {{ data = await res.json(); }} catch (e) {{}}
  if (res.ok && data.license_key) {{
    msg.className = 'msg ok';
    msg.innerHTML = '✅ Issued: <strong>' + data.license_key +
                    '</strong> — expires ' + data.expires_at +
                    '<br>Copy this key and send it to the customer.';
  }} else {{
    msg.className = 'msg error';
    msg.innerHTML = '❌ ' + (data.message || 'Something went wrong.');
  }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(username: str = Depends(verify_admin)):
    stats    = get_license_stats()
    prompts  = get_all_prompts()
    files_all     = get_all_files()
    promo         = get_promo()
    staff_all     = list_staff()

    # 担当者マスタ一覧
    # 削除ボタンは意図的に置いていない。停止しても表示名は残るため、
    # 過去の紹介コードの担当者が不明にならない。
    if staff_all:
        staff_rows = ""
        for s in staff_all:
            if s['is_active']:
                state = ('<span style="background:#E2EFDA;color:#375623;padding:2px 6px;'
                         'border-radius:3px;font-size:11px">有効</span>')
                toggle = (f'<button onclick="staffActive({s["id"]},false)" '
                          'style="font-size:11px;padding:2px 6px;cursor:pointer">停止</button>')
            else:
                state = '<span style="color:#999;font-size:11px">停止中</span>'
                toggle = (f'<button onclick="staffActive({s["id"]},true)" '
                          'style="font-size:11px;padding:2px 6px;cursor:pointer">再開</button>')
            created = s['created_at'].strftime('%Y-%m-%d') if s.get('created_at') else '－'
            staff_rows += f"""
            <tr>
              <td>{s['id']}</td>
              <td><b>{esc(s['display_name'])}</b></td>
              <td style="font-family:monospace;font-size:12px">{esc(s['login_id'])}</td>
              <td>{state}</td>
              <td style="font-size:11px;color:#777">{esc(s.get('note') or '')}</td>
              <td style="font-size:11px;color:#777">{created}</td>
              <td>
                {toggle}
                <button onclick="staffPassword({s['id']},'{esc(s['display_name'])}')"
                  style="font-size:11px;padding:2px 6px;cursor:pointer;margin-left:4px">PW変更</button>
              </td>
            </tr>"""
    else:
        staff_rows = ('<tr><td colspan="7" style="text-align:center;color:#999;padding:18px">'
                      '担当者がまだ登録されていません</td></tr>')

    # ライセンス一覧
    # 発行プランの選択肢は plans.py から生成（定義とUIのズレを防ぐ）
    plan_options = "".join(
        f'<option value="{esc(code)}">{esc(info["label"])}</option>'
        for code, info in PLANS.items()
    )

    # プロンプト一覧
    prompt_rows = ""
    for p in prompts:
        p_site = p.get('site')
        if p['is_active']:
            active_badge = ('<span style="background:#E2EFDA;color:#375623;padding:2px 6px;'
                            'border-radius:3px;font-size:11px">有効</span>')
        elif not p_site:
            # 未割当は有効化できない（どのサイトの採点にも使われないため）
            active_badge = ('<span style="color:#999;font-size:11px">'
                            '－（サイト未割当）</span>')
        else:
            active_badge = ('<button onclick="activatePrompt(' + str(p['id']) + ')" '
                            'style="font-size:11px;padding:2px 6px;cursor:pointer">有効化</button>')

        site_cell = (f'<span style="font-size:11px">{esc(site_label(p_site))}</span>'
                     if p_site else
                     '<span style="color:#999;font-size:11px">未割当</span>')

        prompt_rows += f"""
        <tr>
          <td>{p['id']}</td>
          <td>{esc(p['version'])}</td>
          <td>{site_cell}</td>
          <td>{esc(p['name'])}</td>
          <td>{active_badge}</td>
          <td>{p['created_at'][:10]}</td>
          <td><a href="/admin/prompts/{p['id']}" style="font-size:11px;color:#2E75B6">編集</a></td>
        </tr>"""


    # 配布ファイル一覧
    file_rows = ""
    for fl in files_all:
        active_badge = '<span style="background:#E2EFDA;color:#375623;padding:2px 6px;border-radius:3px;font-size:11px">配信中</span>' \
                       if fl['is_active'] else \
                       '<button onclick="activateFile(' + str(fl['id']) + ')" style="font-size:11px;padding:2px 6px;cursor:pointer">配信開始</button>'
        file_rows += f"""
        <tr>
          <td>{fl['id']}</td>
          <td>{esc(fl['component'])}</td>
          <td>{esc(fl['version'])}</td>
          <td style="font-size:11px">{esc(fl['filename'])}</td>
          <td>{active_badge}</td>
          <td>{fl['uploaded_at'][:10]}</td>
          <td>
            <button onclick="delFile({fl['id']})"
              style="font-size:11px;padding:2px 6px;cursor:pointer;color:#843C0C">削除</button>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>管理者画面 - JobSearch</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A; font-size: 13px; }}
    .header {{ background: #1A2B4A; color: white; padding: 14px 24px;
               display: flex; align-items: center; justify-content: space-between; }}
    .header h1 {{ font-size: 16px; }}
    .container {{ max-width: 1200px; margin: 24px auto; padding: 0 16px; }}
    .card {{ background: white; border-radius: 8px; padding: 20px;
             margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    .card h2 {{ font-size: 14px; color: #1A2B4A; margin-bottom: 14px;
                padding-bottom: 6px; border-bottom: 2px solid #EBF3FB;
                display: flex; justify-content: space-between; align-items: center; }}
    .form-row {{ display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }}
    .form-row label {{ font-size: 12px; color: #555; min-width: 80px; }}
    .form-row input, .form-row select {{
      padding: 7px 10px; border: 1px solid #BFCFDF; border-radius: 5px; font-size: 12px; }}
    .btn {{ padding: 8px 16px; border: none; border-radius: 5px;
            font-size: 12px; font-weight: bold; cursor: pointer; }}
    .btn-primary {{ background: #C55A11; color: white; }}
    .btn-blue    {{ background: #2E75B6; color: white; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{ background: #2E3A4E; color: white; padding: 8px 10px; text-align: left; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #F0F0F0; }}
    tr:hover {{ background: #F5F7FA; }}
    .msg {{ padding: 10px 14px; border-radius: 6px; margin-top: 10px;
            display: none; font-size: 12px; }}
    .msg.ok    {{ background: #E2EFDA; color: #375623; display: block; }}
    .msg.error {{ background: #FCE4D6; color: #843C0C; display: block; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 12px; }}
    .stat-card {{ background: #F0F6FC; border-radius: 6px; padding: 14px; text-align: center; }}
    .stat-num  {{ font-size: 22px; font-weight: bold; color: #1A2B4A; }}
    .stat-label{{ font-size: 11px; color: #777; margin-top: 4px; }}
    .new-link {{ background: #1F7A4D; color: white; padding: 6px 12px; border-radius: 5px;
                  text-decoration: none; font-size: 11px; }}
  </style>
</head>
<body>
<div class="header">
  <h1>⚡ JobSearch — 管理者画面</h1>
  <span style="font-size:11px;opacity:0.7">{datetime.now().strftime('%Y/%m/%d %H:%M')}</span>
</div>

<div class="container">

  <!-- 統計 -->
  <div class="card">
    <h2>📊 統計</h2>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-num">{stats['total']}</div>
        <div class="stat-label">総ライセンス数</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{stats['active']}</div>
        <div class="stat-label">有効ライセンス</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" style="color:#843C0C">{stats['expired']}</div>
        <div class="stat-label">期限切れ</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{len(prompts)}</div>
        <div class="stat-label">登録プロンプト</div>
      </div>
    </div>
  </div>

  <!-- ライセンス発行 -->
  <div class="card">
    <h2>🔑 ライセンス発行</h2>
    <div class="form-row">
      <label>メール</label>
      <input type="email" id="new-email" placeholder="user@example.com" style="min-width:200px">
      <label>プラン</label>
      <select id="new-plan">{plan_options}</select>
      <label>備考</label>
      <input type="text" id="new-note" placeholder="任意" style="min-width:140px">
      <button class="btn btn-primary" onclick="issueLicense()">発行</button>
    </div>
    <div id="issue-msg" class="msg"></div>
  </div>

  <!-- ライセンス一覧への導線 -->
  <div class="card">
    <h2>📋 ライセンス管理</h2>
    <p style="font-size:12px;color:#777;margin-bottom:12px">
      一覧・検索・延長・プラン変更・削除は専用ページで行えます。
      現在 {stats['total']} 件（有効 {stats['active']} 件／期限切れ {stats['expired']} 件）。
    </p>
    <a href="/admin/licenses" class="btn btn-blue"
       style="text-decoration:none;display:inline-block">ライセンス一覧を開く →</a>
    <a href="/admin/backup" style="font-size:11px;color:#2E75B6;margin-left:14px">CSVバックアップ</a>
  </div>

  <!-- 紹介リンク管理への導線 -->
  <div class="card">
    <h2>🔗 紹介リンク管理</h2>
    <p style="font-size:12px;color:#777;margin-bottom:12px">
      SNS流入の計測。コードの登録、訪問・購入の集計、CSV出力を行えます。
    </p>
    <a href="/admin/referrals" class="btn btn-blue"
       style="text-decoration:none;display:inline-block">紹介リンク管理を開く →</a>
    <a href="/staff" style="font-size:11px;color:#2E75B6;margin-left:14px">スタッフ用画面を見る</a>
  </div>

  <!-- 担当者マスタ -->
  <div class="card">
    <h2>👥 担当者マスタ</h2>
    <p style="font-size:12px;color:#777;margin-bottom:12px">
      紹介リンクの「担当者」に使う名前を管理します。ここに登録した表示名だけが選べるようになるため、
      同じ人が複数の綴りで登録される事故を防げます。
    </p>
    <div class="form-row">
      <label>表示名</label>
      <input type="text" id="staff-name" placeholder="koji" style="max-width:150px">
      <label>ログインID</label>
      <input type="text" id="staff-login" placeholder="koji" style="max-width:150px">
      <label>パスワード</label>
      <input type="text" id="staff-pass" placeholder="8文字以上" style="max-width:170px">
      <label>備考</label>
      <input type="text" id="staff-note" placeholder="任意" style="min-width:140px">
      <button class="btn btn-primary" onclick="staffCreate()">登録</button>
    </div>
    <div id="staff-msg" class="msg"></div>
    <table style="margin-top:14px">
      <thead>
        <tr><th>ID</th><th>表示名</th><th>ログインID</th><th>状態</th>
            <th>備考</th><th>登録日</th><th>操作</th></tr>
      </thead>
      <tbody>{staff_rows}</tbody>
    </table>
    <div style="font-size:11px;color:#777;margin-top:8px;line-height:1.7">
      ・<b>表示名</b>が紹介リンクの担当者欄に記録されます。個人名そのままではなく短い呼称を推奨します（例：Jenn）。<br>
      ・<b>ログインID／パスワード</b>はスタッフ用管理画面のサインインに使います（切り替えは次の段階で行うため、現時点ではまだ使われません）。<br>
      ・パスワードはハッシュ化して保存され、画面に再表示できません。忘れた場合は「PW変更」で再設定してください。<br>
      ・<b>削除はできません。</b>退職時は「停止」してください。過去の紹介コードの担当者が不明にならないようにするためです。
    </div>
  </div>

  <!-- プロンプト管理 -->
  <div class="card">
    <h2>📝 プロンプト管理
      <a href="/admin/prompts/new" class="new-link">+ 新規作成</a>
    </h2>
    <table>
      <thead>
        <tr><th>ID</th><th>バージョン</th><th>サイト</th><th>名前</th><th>状態</th><th>作成日</th><th>操作</th></tr>
      </thead>
      <tbody>{prompt_rows}</tbody>
    </table>
  </div>

  <!-- AI設定（為替レート） -->
  <div class="card">
    <h2>💱 AI設定（為替レート）</h2>
    <p style="font-size:12px;color:#777;margin-bottom:12px">
      複数通貨が混在するサイトの採点に使う、対USDの為替レートです。
      対応サイトが増えても、レートを持つ場所はここ1か所のままです。
      保存すると再デプロイなしで次の採点から反映されます。
    </p>
    <a href="/admin/settings" class="btn btn-blue"
       style="text-decoration:none;display:inline-block">為替レートを開く →</a>
  </div>

  <!-- 広告欄設定 -->
  <div class="card">
    <h2>📣 広告欄（結果画面の上に表示）</h2>
    <div class="form-row">
      <label>表示</label>
      <select id="promo-enabled">
        <option value="1" {'selected' if promo['enabled'] else ''}>ON</option>
        <option value="0" {'' if promo['enabled'] else 'selected'}>OFF</option>
      </select>
    </div>
    <div class="form-row">
      <label>タイトル</label>
      <input type="text" id="promo-title" value="{esc(promo['title'])}" placeholder="紹介したいサービス名" style="min-width:300px">
    </div>
    <div class="form-row">
      <label>説明</label>
      <input type="text" id="promo-body" value="{esc(promo['body'])}" placeholder="ひとこと説明" style="min-width:400px">
    </div>
    <div class="form-row">
      <label>リンク先</label>
      <input type="text" id="promo-url" value="{esc(promo['url'])}" placeholder="https://..." style="min-width:400px">
      <button class="btn btn-blue" onclick="savePromo()">保存</button>
    </div>
    <div id="promo-msg" class="msg"></div>
    <div class="help" style="margin-top:8px;font-size:12px;color:#777">
      ※ 表示がOFF、またはタイトルが空の場合、利用者の画面には表示されません。
    </div>
  </div>

  <!-- 配布ファイル管理 -->
  <div class="card">
    <h2>📦 配布ファイル管理（CSV取込用Excel）</h2>
    <div class="form-row">
      <label>種類</label>
      <select id="file-component">
        <option value="excel">excel（.xlsm）</option>
      </select>
      <label>バージョン</label>
      <input type="text" id="file-version" placeholder="1.0.1" style="max-width:120px">
      <label>備考</label>
      <input type="text" id="file-note" placeholder="任意" style="min-width:160px">
    </div>
    <div class="form-row">
      <label>ファイル</label>
      <input type="file" id="file-input" style="flex:1">
      <button class="btn btn-primary" onclick="uploadFile()">アップロード</button>
    </div>
    <p style="font-size:11px;color:#777;margin-top:6px">
      アップロードすると同じ種類の旧バージョンは自動的に配信停止され、最新版として即時配信されます。
    </p>
    <div id="file-msg" class="msg"></div>
    <table style="margin-top:14px">
      <thead>
        <tr><th>ID</th><th>種類</th><th>バージョン</th><th>ファイル名</th><th>状態</th><th>登録日</th><th>操作</th></tr>
      </thead>
      <tbody>{file_rows}</tbody>
    </table>
  </div>

</div>

<script>
async function issueLicense() {{
  const email = document.getElementById('new-email').value.trim();
  const plan  = document.getElementById('new-plan').value;
  const note  = document.getElementById('new-note').value.trim();
  const msg   = document.getElementById('issue-msg');
  if (!email) {{ alert('メールアドレスを入力してください'); return; }}

  const res = await fetch('/admin/license/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{email, plan, note}}),
  }});
  const data = await res.json();
  if (res.ok) {{
    msg.className = 'msg ok';
    msg.innerHTML = '✅ 発行完了：<strong>' + data.license_key + '</strong>　有効期限: ' + data.expires_at;
    setTimeout(() => location.reload(), 2000);
  }} else {{
    msg.className = 'msg error';
    msg.innerHTML = '❌ ' + (data.message || 'エラーが発生しました');
  }}
}}

async function extend(key) {{
  if (!confirm(key + ' を1ヶ月延長しますか？')) return;
  const res = await fetch('/admin/license/extend', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key, months: 1}}),
  }});
  const data = await res.json();
  if (res.ok) {{
    alert('延長完了。新しい有効期限: ' + data.new_expires_at);
    location.reload();
  }} else {{
    alert('エラー: ' + (data.message || '不明なエラー'));
  }}
}}

async function changePlan(key, id) {{
  const plan = document.getElementById('plan-' + id).value;
  if (!confirm(key + ' のプランを「' + plan + '」に変更しますか？\\n\\n・有効期限は変わりません\\n・上位プランへの変更時は、旧プランの残り回数を繰り越します')) {{
    location.reload();
    return;
  }}
  const res = await fetch('/admin/license/plan', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key, plan: plan}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{
    let msg = 'プランを変更しました：' + data.old_plan + ' → ' + data.new_plan;
    msg += '\\n有効期限は変更していません。';
    if (data.carried_over > 0) {{
      msg += '\\n\\n旧プランの残り ' + data.carried_over + ' 回を繰り越しました。';
    }}
    if (data.remaining != null) {{
      msg += '\\n今月の残り回数：' + data.remaining + ' 回（使用済み ' + data.used + ' 回）';
    }}
    alert(msg);
    location.reload();
  }} else {{
    alert('エラー: ' + (data.message || '不明なエラー'));
    location.reload();
  }}
}}

async function activatePrompt(id) {{
  if (!confirm('このプロンプトを有効化しますか？（同じサイトの他のプロンプトは無効化されます）')) return;
  const res = await fetch('/admin/prompt/' + id + '/activate', {{method: 'POST'}});
  let data = {{}};
  try {{ data = await res.json(); }} catch (e) {{}}
  if (res.ok && data.success) {{ location.reload(); }}
  else {{ alert(data.message || 'エラーが発生しました'); }}
}}




async function uploadFile() {{
  const component = document.getElementById('file-component').value;
  const version   = document.getElementById('file-version').value.trim();
  const note      = document.getElementById('file-note').value.trim();
  const fileInput = document.getElementById('file-input');
  const msg       = document.getElementById('file-msg');

  if (!version) {{ alert('バージョンを入力してください'); return; }}
  if (!fileInput.files.length) {{ alert('ファイルを選択してください'); return; }}

  const formData = new FormData();
  formData.append('component', component);
  formData.append('version', version);
  formData.append('note', note);
  formData.append('file', fileInput.files[0]);

  msg.className = 'msg';
  msg.style.display = 'block';
  msg.textContent = 'アップロード中...';

  try {{
    const res = await fetch('/admin/file/upload', {{ method: 'POST', body: formData }});
    const data = await res.json();
    if (res.ok && data.success) {{
      msg.className = 'msg ok';
      msg.innerHTML = '✅ アップロード完了（v' + data.version + '）。即時配信を開始しました。';
      setTimeout(() => location.reload(), 1200);
    }} else {{
      msg.className = 'msg error';
      msg.textContent = '❌ ' + (data.message || 'アップロードに失敗しました');
    }}
  }} catch(e) {{
    msg.className = 'msg error';
    msg.textContent = '❌ アップロード中にエラーが発生しました';
  }}
}}

async function activateFile(id) {{
  if (!confirm('このファイルを最新版として配信しますか？')) return;
  const res = await fetch('/admin/file/' + id + '/activate', {{method: 'POST'}});
  if (res.ok) location.reload();
  else alert('エラーが発生しました');
}}

async function delFile(id) {{
  if (!confirm('削除しますか？')) return;
  const res = await fetch('/admin/file/' + id, {{method: 'DELETE'}});
  if (res.ok) location.reload();
  else alert('エラーが発生しました');
}}

async function staffCreate() {{
  const name  = document.getElementById('staff-name').value.trim();
  const login = document.getElementById('staff-login').value.trim();
  const pass  = document.getElementById('staff-pass').value;
  const note  = document.getElementById('staff-note').value.trim();
  const msg   = document.getElementById('staff-msg');

  if (!name || !login || !pass) {{
    alert('表示名・ログインID・パスワードを入力してください');
    return;
  }}
  const res = await fetch('/admin/staff/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{display_name: name, login_id: login, password: pass, note: note}})
  }});
  let data = {{}};
  try {{ data = await res.json(); }} catch (e) {{}}
  msg.style.display = 'block';
  if (res.ok && data.success) {{
    msg.className = 'msg ok';
    msg.textContent = '登録しました：' + data.display_name;
    setTimeout(() => location.reload(), 900);
  }} else {{
    msg.className = 'msg error';
    msg.textContent = data.message || '登録できませんでした';
  }}
}}

async function staffActive(id, active) {{
  const word = active ? '再開' : '停止';
  if (!confirm('この担当者を' + word + 'しますか？')) return;
  const res = await fetch('/admin/staff/' + id + '/active', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{active: active}})
  }});
  let data = {{}};
  try {{ data = await res.json(); }} catch (e) {{}}
  if (res.ok && data.success) location.reload();
  else alert(data.message || 'エラーが発生しました');
}}

async function staffPassword(id, name) {{
  const pass = prompt(name + ' の新しいパスワードを入力してください（8文字以上）');
  if (pass === null) return;
  const res = await fetch('/admin/staff/' + id + '/password', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{password: pass}})
  }});
  let data = {{}};
  try {{ data = await res.json(); }} catch (e) {{}}
  if (res.ok && data.success) alert('パスワードを変更しました：' + data.display_name);
  else alert(data.message || 'エラーが発生しました');
}}

async function savePromo() {{
  const res = await fetch('/admin/promo/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      title: document.getElementById('promo-title').value,
      body: document.getElementById('promo-body').value,
      url: document.getElementById('promo-url').value,
      enabled: document.getElementById('promo-enabled').value === '1'
    }})
  }});
  const d = await res.json();
  const m = document.getElementById('promo-msg');
  m.textContent = (d.status === 'ok') ? '保存しました' : '保存に失敗しました';
  m.className = 'msg ' + ((d.status === 'ok') ? 'ok' : 'error');
  m.style.display = 'block';
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ══════════════════════════════════════════
# ライセンス一覧ページ（検索・絞り込み・ページ送り・削除）
# ══════════════════════════════════════════
LICENSES_PER_PAGE = 50


@app.get("/admin/licenses", response_class=HTMLResponse)
async def admin_licenses_page(
    q: str = "", status: str = "all", page: int = 1,
    who: dict = Depends(verify_any),
):
    page = max(int(page), 1)
    offset = (page - 1) * LICENSES_PER_PAGE

    # 誰としてログインしているかを常に見せる。Basic認証はログアウトできないため、
    # 意図しない資格情報のまま操作してしまう事故を防ぐ目的。
    T  = ui_text(who)
    EN = who.get("role") != "admin"
    _role = T["role_admin"] if who.get("role") == "admin" else T["role_staff"]
    who_badge = (f'<span style="opacity:.8">{esc(who.get("display_name") or "")}'
                 f'（{_role}）</span>' if not EN else
                 f'<span style="opacity:.8">{esc(who.get("display_name") or "")}'
                 f' ({_role})</span>')
    # スタッフの戻り先はスタッフ用トップ。管理者には管理トップを出す。
    back_link = (f'<a href="/admin" style="margin-left:14px">{T["back_admin"]}</a>'
                 if not EN
                 else f'<a href="/staff" style="margin-left:14px">{T["back_staff"]}</a>'
                      f'<a href="/admin/referrals" style="margin-left:14px">{T["link_referrals"]}</a>')
    found = search_licenses(keyword=q.strip(), status=status,
                            limit=LICENSES_PER_PAGE, offset=offset)
    licenses, total = found["rows"], found["total"]
    stats = get_license_stats()
    today = datetime.today().date().isoformat()
    last_page = max((total + LICENSES_PER_PAGE - 1) // LICENSES_PER_PAGE, 1)

    rows = ""
    for lic in licenses:
        expired = lic["expires_at"] < today
        if lic["status"] != "active":
            badge = f'<span style="color:#843C0C">{T["badge_inactive"]}</span>'
        elif expired:
            badge = f'<span style="color:#843C0C">{T["badge_expired"]}</span>'
        else:
            badge = f'<span style="color:#375623">{T["badge_active"]}</span>'

        current_plan = lic["plan"]
        opts = "".join(
            f'<option value="{esc(code)}"'
            f'{" selected" if code == current_plan else ""}>'
            f'{esc(plan_label_ui(info, EN))}</option>'
            for code, info in PLANS.items()
        )
        if not is_valid_plan(current_plan):
            opts = (f'<option value="{esc(current_plan)}" selected>'
                    f'{esc(current_plan)}{T["plan_legacy"]}</option>') + opts

        paid = T["kind_paid"] if lic.get("subscription_id") else T["kind_manual"]

        # メール送付の状態。failed だけを目立たせる（要手動対応のため）。
        ms = lic.get("mail_status")
        if ms == "sent":
            mail_badge = f'<span style="color:#375623">{T["mail_sent"]}</span>'
        elif ms == "failed":
            mail_badge = ('<span style="background:#FCE4D6;color:#843C0C;'
                          'font-weight:bold;padding:2px 6px;border-radius:3px">'
                          f'{T["mail_failed"]}</span>')
        elif ms == "manual":
            mail_badge = f'<span style="color:#777">{T["mail_manual"]}</span>'
        else:
            # この機能を入れる前に発行された分。送れているかは分からない。
            mail_badge = '<span style="color:#BBB">—</span>'
        rows += f"""
        <tr>
          <td>{lic['id']}</td>
          <td style="font-family:monospace;font-size:11px">{esc(lic['license_key'])}</td>
          <td>{esc(lic['email'])}</td>
          <td><select id="plan-{lic['id']}" style="font-size:11px;padding:2px 4px">{opts}</select></td>
          <td style="font-size:11px;color:#777">{paid}</td>
          <td style="font-size:11px">{mail_badge}</td>
          <td>{badge}</td>
          <td>{lic['expires_at']}</td>
          <td style="white-space:nowrap">
            <button onclick="extend('{esc(lic['license_key'])}')"
              style="font-size:11px;padding:3px 7px;cursor:pointer">{T['btn_extend']}</button>
            <button onclick="changePlan('{esc(lic['license_key'])}', {lic['id']})"
              style="font-size:11px;padding:3px 7px;cursor:pointer">{T['btn_plan']}</button>
            <button onclick="resendKey('{esc(lic['license_key'])}', '{esc(lic['email'])}')"
              style="font-size:11px;padding:3px 7px;cursor:pointer">{T['btn_resend']}</button>
            <button onclick="delLicense('{esc(lic['license_key'])}', '{esc(lic['email'])}')"
              style="font-size:11px;padding:3px 7px;cursor:pointer;color:#843C0C">{T['btn_delete']}</button>
          </td>
        </tr>"""

    if not rows:
        rows = ('<tr><td colspan="9" style="text-align:center;color:#999;padding:24px">'
                f'{T["empty_licenses"]}</td></tr>')

    def sel(v):
        return " selected" if status == v else ""

    def page_link(n, label):
        return (f'<a href="/admin/licenses?q={esc(q)}&status={esc(status)}&page={n}" '
                f'style="padding:5px 10px;border:1px solid #BFCFDF;border-radius:4px;'
                f'text-decoration:none;color:#2E75B6;font-size:12px">{label}</a>')

    # 一括削除の日数選択。日本語と英語で語順が違うため、まとめて組み立てる。
    day_options = "".join(
        f'<option value="{d}"{" selected" if d == 90 else ""}>'
        + (f'{d}日以上経過' if not EN else f'{d}+ days')
        + '</option>'
        for d in (30, 60, 90, 180)
    )

    pager = ""
    if page > 1:
        pager += page_link(page - 1, T["pg_prev"]) + " "
    _pg = (f'{page} / {last_page} ページ（全{total}件）' if not EN
           else f'Page {page} of {last_page} ({total} total)')
    pager += f'<span style="font-size:12px;color:#777">{_pg}</span>'
    if page < last_page:
        pager += " " + page_link(page + 1, T["pg_next"])

    html = f"""<!DOCTYPE html>
<html lang="{'en' if EN else 'ja'}">
<head>
  <meta charset="UTF-8">
  <title>{T['lic_title']} - JobSearch</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A; font-size: 13px; }}
    .header {{ background: #1A2B4A; color: white; padding: 14px 24px;
               display: flex; align-items: center; justify-content: space-between; }}
    .header h1 {{ font-size: 16px; }}
    .header a {{ color: #9FB0CC; font-size: 12px; text-decoration: none; }}
    .header a:hover {{ color: #fff; text-decoration: underline; }}
    .container {{ max-width: 1280px; margin: 24px auto; padding: 0 16px; }}
    .card {{ background: white; border-radius: 8px; padding: 20px;
             margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    .card h2 {{ font-size: 14px; color: #1A2B4A; margin-bottom: 14px;
                padding-bottom: 6px; border-bottom: 2px solid #EBF3FB; }}
    .form-row {{ display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }}
    .form-row label {{ font-size: 12px; color: #555; }}
    input, select {{ padding: 7px 10px; border: 1px solid #BFCFDF; border-radius: 5px; font-size: 12px; }}
    .btn {{ padding: 8px 16px; border: none; border-radius: 5px;
            font-size: 12px; font-weight: bold; cursor: pointer; }}
    .btn-primary {{ background: #C55A11; color: white; }}
    .btn-blue    {{ background: #2E75B6; color: white; }}
    .btn-danger  {{ background: #A33A22; color: white; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{ background: #2E3A4E; color: white; padding: 8px 10px; text-align: left; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #F0F0F0; }}
    tr:hover {{ background: #F5F7FA; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 12px; }}
    .stat-card {{ background: #F0F6FC; border-radius: 6px; padding: 12px; text-align: center; }}
    .stat-num  {{ font-size: 20px; font-weight: bold; color: #1A2B4A; }}
    .stat-label{{ font-size: 11px; color: #777; margin-top: 4px; }}
    .warn {{ background: #FFF8E7; border: 1px solid #FFD966; padding: 10px 12px;
             border-radius: 5px; font-size: 11.5px; color: #7F6000; margin-bottom: 12px; }}
  </style>
</head>
<body>
<div class="header">
  <h1>{T['lic_h1']}</h1>
  <span style="font-size:12px">{who_badge}{back_link}</span>
</div>

<div class="container">

  <div class="card">
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-num">{stats['total']}</div><div class="stat-label">{T['st_total']}</div></div>
      <div class="stat-card"><div class="stat-num" style="color:#1F7A4D">{stats['active']}</div><div class="stat-label">{T['st_active']}</div></div>
      <div class="stat-card"><div class="stat-num" style="color:#843C0C">{stats['expired']}</div><div class="stat-label">{T['st_expired']}</div></div>
      <div class="stat-card"><div class="stat-num">{stats['inactive']}</div><div class="stat-label">{T['st_inactive']}</div></div>
    </div>
  </div>

  <div class="card">
    <h2>{T['search_h2']}</h2>
    <form method="get" action="/admin/licenses" class="form-row">
      <label>{T['kw_label']}</label>
      <input type="text" name="q" value="{esc(q)}" placeholder="{T['kw_ph']}" style="min-width:240px">
      <label>{T['status_label']}</label>
      <select name="status">
        <option value="all"{sel('all')}>{T['opt_all']}</option>
        <option value="active"{sel('active')}>{T['badge_active']}</option>
        <option value="expired"{sel('expired')}>{T['badge_expired']}</option>
        <option value="inactive"{sel('inactive')}>{T['badge_inactive']}</option>
      </select>
      <button type="submit" class="btn btn-blue">{T['btn_search']}</button>
      <a href="/admin/licenses" style="font-size:12px;color:#777">{T['link_clear']}</a>
    </form>
  </div>

  <div class="card">
    <h2>{T['list_h2']}</h2>
    <table>
      <thead>
        <tr><th>{T['th_id']}</th><th>{T['th_key']}</th><th>{T['th_email']}</th>
            <th>{T['th_plan']}</th><th>{T['th_kind']}</th>
            <th>{T['th_mail']}</th><th>{T['th_status']}</th>
            <th>{T['th_expires']}</th><th>{T['th_ops']}</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center">{pager}</div>
  </div>

  <div class="card">
    <h2>{T['bulk_h2']}</h2>
    <div class="warn">
      {T['bulk_warn_1']}
      <a href="/admin/backup" style="color:#2E75B6">{T['bulk_warn_link']}</a>{T['bulk_warn_2']}<br>
      {T['bulk_warn_3']}
    </div>
    <div class="form-row">
      <label>{T['bulk_since']}</label>
      <select id="del-days">{day_options}</select>
      <button class="btn btn-danger" onclick="delExpired()">{T['btn_bulk_delete']}</button>
    </div>
    <div id="del-msg" style="margin-top:10px;font-size:12px"></div>
  </div>

</div>

<script>
async function resendKey(key, email) {{
  if (!confirm('{T['js_resend_confirm']}\\n\\n'
               + key + '\\n{T['js_resend_to']}' + email)) return;
  const res = await fetch('/admin/license/resend', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{
    alert('{T['js_sent']}' + data.email);
    location.reload();
  }} else {{
    alert('{T['js_error']}' + (data.message || '{T['js_unknown']}'));
  }}
}}

async function extend(key) {{
  if (!confirm(key + '{T['js_extend_confirm']}')) return;
  const res = await fetch('/admin/license/extend', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key, months: 1}}),
  }});
  const data = await res.json();
  if (res.ok) {{ alert('{T['js_extended']}' + data.new_expires_at); location.reload(); }}
  else {{ alert('{T['js_error']}' + (data.message || '{T['js_unknown']}')); }}
}}

async function changePlan(key, id) {{
  const plan = document.getElementById('plan-' + id).value;
  if (!confirm(key + '{T['js_plan_confirm_1']}' + plan + '{T['js_plan_confirm_2']}\\n\\n{T['js_plan_note']}')) {{
    location.reload(); return;
  }}
  const res = await fetch('/admin/license/plan', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key, plan: plan}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{
    let msg = '{T['js_plan_done']}' + data.old_plan + ' → ' + data.new_plan;
    msg += '\\n{T['js_plan_keep']}';
    if (data.carried_over > 0) {{ msg += '\\n\\n{T['js_plan_carry_1']}' + data.carried_over + '{T['js_plan_carry_2']}'; }}
    if (data.remaining != null) {{ msg += '\\n{T['js_plan_left_1']}' + data.remaining + '{T['js_plan_left_2']}' + data.used + '{T['js_plan_left_3']}'; }}
    alert(msg);
  }} else {{ alert('{T['js_error']}' + (data.message || '{T['js_unknown']}')); }}
  location.reload();
}}

async function delLicense(key, email) {{
  if (!confirm('{T['js_del_confirm']}\\n\\n' + key + '\\n' + email +
               '\\n\\n{T['js_del_warn']}')) return;
  const res = await fetch('/admin/license/delete', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{ alert('{T['js_deleted']}' + key); location.reload(); }}
  else {{ alert('{T['js_del_failed']}\\n\\n' + (data.message || '{T['js_unknown']}')); }}
}}

async function delExpired() {{
  const days = document.getElementById('del-days').value;
  if (!confirm('{T['js_bulk_1']}' + days + '{T['js_bulk_2']}\\n\\n' +
               '{T['js_bulk_3']}')) return;
  if (!confirm('{T['js_bulk_final']}')) return;
  const msg = document.getElementById('del-msg');
  msg.textContent = '{T['js_deleting']}';
  const res = await fetch('/admin/license/delete-expired', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{days: parseInt(days, 10)}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{
    msg.innerHTML = '✅ ' + data.deleted_count + '{T['js_bulk_done_1']}' +
      (data.skipped_count ? '{T['js_bulk_skip_1']}' + data.skipped_count + '{T['js_bulk_skip_2']}' : '');
    setTimeout(() => location.reload(), 2000);
  }} else {{ msg.textContent = '❌ ' + (data.message || '{T['js_generic_error']}'); }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── ライセンス操作API ──
@app.post("/admin/license/create")
async def admin_create_license(request: Request, who: dict = Depends(verify_any)):
    body = await request.json()
    email = body.get("email", "").strip()
    plan  = body.get("plan", "1month")
    note  = body.get("note", "")
    if not email:
        return JSONResponse(status_code=400, content={"message": "emailが必要です"})
    if not is_valid_plan(plan):
        return JSONResponse(
            status_code=400,
            content={"message": f"未定義のプランです: {plan}（plans.py を確認してください）"},
        )
    res = create_license(email=email, plan=plan, note=note)
    # 手動発行はメールを送っていない。一覧で「決済分の送信失敗」と区別できるよう
    # manual を立てておく（赤表示にはしない）。
    try:
        set_mail_status(res["license_key"], "manual")
    except Exception as e:
        log.error("could not mark manual mail status: %s", e)
    return res


@app.post("/admin/license/resend")
async def admin_resend_license(request: Request, who: dict = Depends(verify_any)):
    """ライセンスキーを購入者へ再送する。

    用途は2つ。
      ・決済分でメール送信に失敗したもの（一覧で「未送信」＝赤）の復旧
      ・管理画面から手動発行したもの（メールを送っていない）の送付

    有効なライセンスに限る。期限切れ・無効化されたキーを送っても
    サインインできず、受け取った側が混乱するため。
    """
    body = await request.json()
    key = (body.get("license_key") or "").strip()
    if not key:
        return JSONResponse(status_code=400,
                            content={"message": "license_keyが必要です"})

    lic = get_license_row(key)
    if not lic:
        return JSONResponse(status_code=404,
                            content={"message": "ライセンスキーが見つかりません"})

    # 有効性の確認（状態と有効期限の両方を見る）
    if lic.get("status") != "active":
        return JSONResponse(status_code=400,
                            content={"message": "無効化されたライセンスは再送できません"})
    today = datetime.today().date().isoformat()
    if str(lic.get("expires_at")) < today:
        return JSONResponse(
            status_code=400,
            content={"message": "期限切れのライセンスは再送できません。"
                                "先に「+1ヶ月」で延長してください"})

    try:
        import mailer
        if not mailer.is_configured():
            return JSONResponse(
                status_code=503,
                content={"message": "SMTPが未設定です。Renderの環境変数を確認してください"})
        ok = mailer.send_license_key(
            lic["email"], key, lic["plan"], str(lic.get("expires_at") or "")
        )
    except Exception as e:
        log.error("resend failed for %s: %s", key, e)
        try:
            set_mail_status(key, "failed", str(e))
        except Exception:
            pass
        return JSONResponse(status_code=500,
                            content={"message": f"送信エラー: {e}"})

    if ok:
        try:
            set_mail_status(key, "sent")
        except Exception as e:
            log.error("could not record mail status after resend: %s", e)
        log.info("license key resent: %s to %s", key, lic["email"])
        return {"success": True, "email": lic["email"]}

    try:
        set_mail_status(key, "failed", "resend returned false")
    except Exception:
        pass
    return JSONResponse(
        status_code=500,
        content={"message": "送信できませんでした。SMTPの設定を確認してください"})


@app.post("/admin/license/extend")
async def admin_extend_license(request: Request, who: dict = Depends(verify_any)):
    body = await request.json()
    license_key = body.get("license_key", "").strip()
    months = int(body.get("months", 1))
    if not license_key:
        return JSONResponse(status_code=400, content={"message": "license_keyが必要です"})
    result = extend_license(license_key=license_key, months=months)
    if not result["success"]:
        return JSONResponse(status_code=404, content={"message": result["message"]})
    return result


@app.post("/admin/license/plan")
async def admin_change_license_plan(request: Request, who: dict = Depends(verify_any)):
    """
    ライセンスのプランを手動で変更する。
    Polar側のプラン変更は自動反映していないため、
    Renderのログに "PLAN CHANGE DETECTED" が出たらここから手動で合わせる。
    """
    body = await request.json()
    license_key = body.get("license_key", "").strip()
    plan        = body.get("plan", "").strip()
    if not license_key:
        return JSONResponse(status_code=400, content={"message": "license_keyが必要です"})
    if not is_valid_plan(plan):
        return JSONResponse(
            status_code=400,
            content={"message": f"未定義のプランです: {plan}（plans.py を確認してください）"},
        )
    result = apply_plan_change(license_key=license_key, new_plan=plan)
    if not result.get("success"):
        return JSONResponse(status_code=400, content=result)
    return result


@app.post("/admin/license/delete")
async def admin_delete_license(request: Request, who: dict = Depends(verify_any)):
    """ライセンスを1件削除する。決済に紐づくものは条件を満たす場合のみ。"""
    body = await request.json()
    license_key = body.get("license_key", "").strip()
    if not license_key:
        return JSONResponse(status_code=400, content={"message": "license_keyが必要です"})
    result = delete_license(license_key)
    if not result.get("success"):
        return JSONResponse(status_code=400, content=result)
    return result


@app.post("/admin/license/delete-expired")
async def admin_delete_expired_licenses(request: Request, who: dict = Depends(verify_any)):
    """期限切れライセンスをまとめて削除する。"""
    body = await request.json()
    try:
        days = int(body.get("days", 90))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "daysが不正です"})
    if days < 30:
        return JSONResponse(status_code=400,
            content={"message": "daysは30以上を指定してください"})
    return delete_expired_licenses(older_than_days=days)


@app.get("/admin/backup")
async def admin_backup(who: dict = Depends(verify_any)):
    csv_data = export_licenses_csv()
    filename = f"licenses_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    bom = "\uFEFF"
    return Response(
        content=(bom + csv_data).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ══════════════════════════════════════════
# 紹介リンク管理（SNS流入計測）
# ══════════════════════════════════════════
# 訪問と購入を累積で持ち、リセットはしない。画面側で期間を切り替える。
#
# 【計測の限界】以下は追跡できないため、記録される数字は実際より少なくなる。
#   ・スマホでリンクを踏み、後からPCで購入した場合
#   ・リンクを踏んだ後にブラウザのデータを消してから購入した場合
#   ・リンクを経由せず検索などで直接来た場合
# 傾向の比較には使えるが、絶対値として信用しすぎないこと。
# インフルエンサーへの報酬計算にはPolarの割引コードを併用する方が確実。

def _ref_period(period: str):
    """画面のボタンに対応する期間を (from, to) で返す。None は無制限。"""
    today = datetime.today().date()
    if period == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1).isoformat(), last_prev.isoformat()
    if period == "30d":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    return None, None          # all


def _ref_mrr(plans_csv: str) -> int:
    """継続中ライセンスのプラン名（カンマ区切り）から月額合計を出す。"""
    if not plans_csv:
        return 0
    return sum(plan_price_usd(p.strip()) for p in plans_csv.split(",") if p.strip())


@app.get("/admin/referrals", response_class=HTMLResponse)
async def admin_referrals(period: str = "all", who: dict = Depends(verify_any)):
    d_from, d_to = _ref_period(period)
    try:
        stats = referral_stats(d_from, d_to)
    except Exception as e:
        log.error("referral stats failed: %s", e)
        stats = []

    base = (BASE_URL or "").rstrip("/")
    T  = ui_text(who)
    EN = who.get("role") != "admin"

    # スタッフには管理トップへのリンクを出さない。
    # 押しても403になるだけで、権限がないことを分かりにくくするため。
    top_link = (f'<a href="/admin" style="color:#2E75B6">{T["back_admin_top"]}</a>'
                if not EN
                else f'<a href="/staff" style="color:#2E75B6">{T["back_staff"]}</a>')

    rows = ""
    for st in stats:
        code = st["code"]
        visits = st["human_visits"] or 0
        bots = (st["visits"] or 0) - visits
        purchases = st["purchases"] or 0
        active_cnt = st["active_cnt"] or 0
        mrr = _ref_mrr(st.get("active_plans"))
        cvr = f"{(purchases / visits * 100):.1f}%" if visits else "—"
        url = f"{base}/r/{code}"

        if st["is_active"]:
            state = f'<span style="color:#375623">{T["ref_state_active"]}</span>'
            btn = (f'<button onclick="toggleRef(\'{esc(code)}\', false)" '
                   f'style="font-size:11px;padding:3px 7px;cursor:pointer">'
                   f'{T["ref_btn_stop"]}</button>')
            row_style = ""
        else:
            state = f'<span style="color:#999">{T["ref_state_stopped"]}</span>'
            btn = (f'<button onclick="toggleRef(\'{esc(code)}\', true)" '
                   f'style="font-size:11px;padding:3px 7px;cursor:pointer">'
                   f'{T["ref_btn_resume"]}</button>')
            row_style = ' style="opacity:.55"'

        bot_note = (f'<span style="color:#BBB;font-size:10px"> (+bot {bots})</span>'
                    if bots else "")

        rows += f"""
        <tr{row_style}>
          <td style="font-family:monospace;font-size:12px">{esc(code)}</td>
          <td style="font-size:12px">{esc(st.get('channel') or '—')}</td>
          <td style="font-size:12px">{esc(st.get('owner') or '—')}</td>
          <td style="font-size:11px">{esc(site_label(st.get('site') or DEFAULT_SITE))}</td>
          <td style="text-align:right">{visits}{bot_note}</td>
          <td style="text-align:right"><b>{purchases}</b></td>
          <td style="text-align:right">{cvr}</td>
          <td style="text-align:right">{active_cnt}</td>
          <td style="text-align:right">${mrr}</td>
          <td>{state}</td>
          <td style="font-size:11px;color:#777">{esc(st.get('note') or '')}</td>
          <td>
            <button onclick="copyUrl('{esc(url)}')"
              style="font-size:11px;padding:3px 7px;cursor:pointer">{T['ref_btn_copy']}</button>
            {btn}
          </td>
        </tr>"""

    if not rows:
        rows = ('<tr><td colspan="12" style="text-align:center;color:#999;padding:24px">'
                f'{T["ref_empty"]}</td></tr>')

    def tab(key, label):
        on = (period == key)
        style = ("background:#1F3864;color:#fff" if on
                 else "background:#F0F4F8;color:#2E75B6")
        return (f'<a href="/admin/referrals?period={key}" '
                f'style="{style};padding:5px 12px;border-radius:4px;'
                f'text-decoration:none;font-size:12px;margin-right:6px">{label}</a>')

    # 担当者は担当者マスタから選ばせる。自由入力だと同じ人が
    # jenny / jennifer / jenifer のように複数の綴りで登録され、集計が分かれてしまう。
    # 停止した担当者は選択肢に出さないが、過去に登録された分の表示名は残る。
    try:
        owners = active_staff_names()
    except Exception as e:
        log.error("staff list failed: %s", e)
        owners = []
    if owners:
        owner_field = (f'<select id="r-owner" style="width:190px">'
                       f'<option value="">{T["ref_owner_blank"]}</option>'
                       + "".join(f'<option value="{esc(o)}">{esc(o)}</option>'
                                 for o in owners)
                       + '</select>')
        no_staff_note = ""
    else:
        # 担当者が1人もいない状態。登録させると担当者なしのコードができるため、
        # 入力自体を止めて理由を出す。
        owner_field = ('<select id="r-owner" style="width:190px" disabled>'
                       f'<option value="">{T["ref_owner_blank"]}</option></select>')
        no_staff_note = (f'<div class="hint" style="color:#843C0C">{T["ref_no_staff"]}</div>')

    # 着地先（/r/{code} が飛ぶ先のLP）。公開中のサイトだけを出す。
    # 選択必須にしているのは、選び忘れると投稿内容と違うLPに着地させてしまい、
    # 内容が噛み合わず購入につながらないため。
    site_field = ('<select id="r-site" style="width:150px">'
                  f'<option value="">{T["ref_site_blank"]}</option>'
                  + "".join(f'<option value="{esc(sid)}">{esc(site_label(sid))}</option>'
                            for sid in enabled_sites())
                  + '</select>')

    # ヒント文は URL と日付を差し込む必要があるため、ここで組み立てておく。
    # （HTML側のf-string内では二重の波括弧処理が入り読みにくくなるため）
    _sample_url = f'{esc(base)}/r/' + ('コード' if not EN else 'CODE')
    ref_hint_2 = T["ref_hint_2"].replace("{url}", _sample_url)
    # 例に使う日付は実行時のもの。固定文字列だと時間が経つほど古く見え、
    # 「いつの例か」が伝わらなくなる。
    sample_code = f"koji-x-{datetime.now().strftime('%y%m%d')}a"
    ref_hint_1 = T["ref_hint_1"].replace("{sample}", sample_code)

    tabs = (tab("all", T["tab_all"]) + tab("this_month", T["tab_this_month"])
            + tab("last_month", T["tab_last_month"]) + tab("30d", T["tab_30d"]))

    html = f"""<!DOCTYPE html>
<html lang="{'en' if EN else 'ja'}"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{T['ref_title']} - JobSearch</title>
<style>
* {{ box-sizing: border-box; }}
body {{ font-family: "Segoe UI", "Hiragino Sans", sans-serif; background: #F0F4F8;
  margin: 0; padding: 24px; color: #1F3864; }}
.container {{ max-width: 1280px; margin: 0 auto; }}
.card {{ background: #fff; border-radius: 8px; padding: 20px 24px; margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
h1 {{ font-size: 20px; margin: 0 0 18px; }}
h2 {{ font-size: 14px; margin: 0 0 14px; padding-bottom: 8px;
  border-bottom: 1px solid #DCE6F1; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: #1F3864; color: #fff; font-size: 11px; padding: 8px 6px;
  text-align: left; }}
td {{ padding: 8px 6px; border-bottom: 1px solid #EDF1F5; font-size: 13px; }}
input, select {{ padding: 7px 10px; border: 1px solid #BFCFDF; border-radius: 5px;
  font-size: 13px; }}
.btn {{ background: #C05621; color: #fff; border: none; padding: 8px 18px;
  border-radius: 5px; cursor: pointer; font-size: 13px; }}
.hint {{ font-size: 11px; color: #777; margin-top: 8px; line-height: 1.7; }}
a.dl {{ color: #2E75B6; font-size: 12px; margin-right: 18px; }}
.msg {{ display:none; padding: 8px 12px; border-radius: 5px; font-size: 12px;
  margin-top: 10px; }}
</style></head><body>
<div class="container">
  <h1>{T['ref_h1']}</h1>
  <p style="font-size:12px;margin:-8px 0 18px">
    {top_link}
    <a href="/admin/licenses" style="color:#2E75B6;margin-left:14px">{T['link_licenses']}</a>
  </p>

  <div class="card">
    <h2>{T['ref_reg_h2']}</h2>
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
      <input id="r-code" placeholder="{sample_code}" style="width:190px" />
      <select id="r-channel">
        <option value="">{T['ref_opt_channel']}</option>
        <option>X</option><option>LinkedIn</option><option>Reddit</option>
        <option>Facebook</option><option>YouTube</option><option>Blog</option>
        <option>Email</option><option>Other</option>
      </select>
      {owner_field}
      {site_field}
      <input id="r-note" placeholder="{T['ref_ph_note']}" style="width:250px" />
      <button class="btn" onclick="addRef()">{T['ref_btn_register']}</button>
    </div>
    <div id="r-msg" class="msg"></div>
    {no_staff_note}
    <div class="hint">
      {ref_hint_1}<br>
      {ref_hint_2}
    </div>
  </div>

  <div class="card">
    <h2>{T['ref_stats_h2']}</h2>
    <div style="margin-bottom:12px">{tabs}</div>
    <table>
      <thead>
        <tr><th>{T['rth_code']}</th><th>{T['rth_channel']}</th><th>{T['rth_owner']}</th>
            <th>{T['rth_site']}</th>
            <th style="text-align:right">{T['rth_visits']}</th>
            <th style="text-align:right">{T['rth_purchases']}</th>
            <th style="text-align:right">{T['rth_cvr']}</th>
            <th style="text-align:right">{T['rth_active']}</th>
            <th style="text-align:right">{T['rth_mrr']}</th>
            <th>{T['rth_state']}</th><th>{T['rth_note']}</th><th>{T['rth_ops']}</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="hint">
      {T['ref_note_1']}<br>
      {T['ref_note_2']}<br>
      {T['ref_note_3']}
    </div>
  </div>

  <div class="card">
    <h2>{T['ref_csv_h2']}</h2>
    <a class="dl" href="/admin/referrals/csv?period={period}">{T['ref_csv_sum']}</a>
    <a class="dl" href="/admin/referrals/csv/detail">{T['ref_csv_detail']}</a>
  </div>
</div>
<script>
"use strict";
function show(msg, ok) {{
  const el = document.getElementById('r-msg');
  el.textContent = msg;
  el.style.display = 'block';
  el.style.background = ok ? '#E2EFDA' : '#FCE4D6';
  el.style.color = ok ? '#375623' : '#843C0C';
}}

async function addRef() {{
  const code = document.getElementById('r-code').value.trim();
  if (!code) {{ show('{T['js_ref_need_code']}', false); return; }}
  const owner = document.getElementById('r-owner').value;
  if (!owner) {{ show('{T['js_ref_need_owner']}', false); return; }}
  const site = document.getElementById('r-site').value;
  if (!site) {{ show('{T['js_ref_need_site']}', false); return; }}
  const res = await fetch('/admin/referral/create', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      code: code,
      channel: document.getElementById('r-channel').value,
      owner: owner,
      site: site,
      note: document.getElementById('r-note').value.trim(),
    }}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{ location.reload(); }}
  else {{ show('{T['js_error']}' + (data.message || '{T['js_unknown']}'), false); }}
}}

async function toggleRef(code, active) {{
  const res = await fetch('/admin/referral/active', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{code: code, active: active}}),
  }});
  if (res.ok) {{ location.reload(); }}
  else {{ alert('{T['js_ref_toggle_failed']}'); }}
}}

function copyUrl(url) {{
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(url).then(function () {{
      show('{T['js_ref_copied']}' + url, true);
    }}, function () {{ show(url, true); }});
  }} else {{
    show(url, true);
  }}
}}
</script>
</body></html>"""
    return HTMLResponse(content=html)


@app.post("/admin/referral/create")
async def admin_referral_create(request: Request,
                                who: dict = Depends(verify_any)):
    body = await request.json()
    code = (body.get("code") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
        return JSONResponse(
            status_code=400,
            content={"message": "コードは英数字・ハイフン・アンダースコアのみ（64文字以内）"})
    # 担当者は担当者マスタにある有効な名前だけを受け付ける。
    # 画面のプルダウンだけでは、APIを直接呼ばれた場合に表記ゆれを防げない。
    owner = (body.get("owner") or "").strip()
    try:
        valid_owners = active_staff_names()
    except Exception as e:
        log.error("staff list failed: %s", e)
        return JSONResponse(status_code=500,
                            content={"message": "担当者マスタを読み込めませんでした"})
    if owner not in valid_owners:
        return JSONResponse(
            status_code=400,
            content={"message": "担当者は担当者マスタから選んでください"
                                "（停止中の担当者は選べません）"})

    # 着地先も同様にサーバー側で検証する。
    site = (body.get("site") or "").strip().lower()
    if not is_valid_site(site):
        return JSONResponse(
            status_code=400,
            content={"message": "着地先は公開中の求人サイトから選んでください"})

    try:
        if referral_exists(code):
            return JSONResponse(status_code=400,
                                content={"message": "そのコードは既に登録されています"})
        create_referral(code, body.get("channel") or "",
                        owner, body.get("note") or "", site)
    except Exception as e:
        log.error("referral create failed: %s", e)
        return JSONResponse(status_code=500, content={"message": f"登録エラー: {e}"})
    return {"success": True, "code": code}


@app.post("/admin/referral/active")
async def admin_referral_active(request: Request,
                                who: dict = Depends(verify_any)):
    body = await request.json()
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse(status_code=400, content={"message": "codeが必要です"})
    try:
        set_referral_active(code, bool(body.get("active")))
    except Exception as e:
        log.error("referral toggle failed: %s", e)
        return JSONResponse(status_code=500, content={"message": str(e)})
    return {"success": True}


def _csv_response(rows, header, filename):
    """CSVを返す。ExcelでそのままUTF-8として開けるようBOMを付ける。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return Response(
        content=("\ufeff" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/admin/referrals/csv")
async def admin_referrals_csv(period: str = "all",
                              who: dict = Depends(verify_any)):
    d_from, d_to = _ref_period(period)
    stats = referral_stats(d_from, d_to)
    rows = []
    for st in stats:
        visits = st["human_visits"] or 0
        purchases = st["purchases"] or 0
        rows.append([
            st["code"], st.get("channel") or "", st.get("owner") or "",
            site_label(st.get("site") or DEFAULT_SITE),
            visits, (st["visits"] or 0) - visits, purchases,
            (f"{(purchases / visits * 100):.1f}" if visits else ""),
            st["active_cnt"] or 0, _ref_mrr(st.get("active_plans")),
            "有効" if st["is_active"] else "停止中",
            st.get("note") or "",
        ])
    header = ["コード", "種別", "担当者", "着地先", "訪問（人）", "訪問（bot）", "購入",
              "転換率(%)", "継続中", "MRR(USD)", "状態", "メモ"]
    name = f"referrals_{period}_{datetime.now().strftime('%Y%m%d')}.csv"
    return _csv_response(rows, header, name)


@app.get("/admin/referrals/csv/detail")
async def admin_referrals_csv_detail(who: dict = Depends(verify_any)):
    rows = []
    for r in referral_detail_rows():
        rows.append([
            r.get("ref_code") or "", r.get("channel") or "", r.get("owner") or "",
            site_label(r.get("site") or DEFAULT_SITE),
            r.get("license_key") or "", r.get("email") or "",
            plan_label(r.get("plan") or ""), plan_price_usd(r.get("plan") or ""),
            r.get("status") or "",
            str(r.get("created_at") or "")[:19], str(r.get("expires_at") or ""),
        ])
    header = ["コード", "種別", "担当者", "着地先", "ライセンスキー", "メール",
              "プラン", "月額(USD)", "状態", "発行日時", "有効期限"]
    name = f"referrals_detail_{datetime.now().strftime('%Y%m%d')}.csv"
    return _csv_response(rows, header, name)


# ── プロンプト管理API ──
@app.get("/admin/prompts/new", response_class=HTMLResponse)
async def admin_prompt_new(username: str = Depends(verify_admin)):
    """プロンプト新規作成画面"""
    return _render_prompt_edit_page(None)


@app.get("/admin/prompts/{prompt_id}", response_class=HTMLResponse)
async def admin_prompt_edit(prompt_id: int, username: str = Depends(verify_admin)):
    """プロンプト編集画面"""
    prompts = get_all_prompts()
    target = next((p for p in prompts if p['id'] == prompt_id), None)
    if not target:
        return HTMLResponse(content="<h1>プロンプトが見つかりません</h1>", status_code=404)

    return _render_prompt_edit_page(target)


def _render_prompt_edit_page(target: dict | None):
    is_new = target is None
    title = "新規プロンプト作成" if is_new else f"プロンプト編集 (ID: {target['id']})"
    version  = "" if is_new else esc(target['version'])
    name     = "" if is_new else esc(target['name'])
    template = "" if is_new else esc(target['template'])
    note     = "" if is_new else esc(target.get('note') or '')
    cur_site = "" if is_new else (target.get('site') or "")
    prompt_id = "null" if is_new else str(target['id'])

    # サイト選択。未割当は「どのサイトの採点にも使われない」保管状態。
    site_options = '<option value="">（未割当）</option>'
    for s in enabled_sites():
        sel = " selected" if s == cur_site else ""
        site_options += f'<option value="{esc(s)}"{sel}>{esc(site_label(s))}</option>'
    # 既に存在するが sites.py から外された値も選択肢として残す（データ保護）
    if cur_site and cur_site not in enabled_sites():
        site_options += f'<option value="{esc(cur_site)}" selected>{esc(cur_site)}（無効）</option>'

    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>{title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Arial, sans-serif; background: #F5F7FA; padding: 24px; }}
.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 24px; border-radius: 10px; }}
h1 {{ font-size: 18px; color: #1A2B4A; margin-bottom: 16px; }}
label {{ display: block; font-size: 12px; color: #555; margin: 12px 0 4px; }}
input, textarea {{ width: 100%; padding: 8px 12px; border: 1px solid #BFCFDF;
  border-radius: 5px; font-size: 13px; font-family: monospace; }}
textarea {{ min-height: 400px; resize: vertical; }}
.btn {{ padding: 10px 24px; border: none; border-radius: 5px;
  font-size: 13px; font-weight: bold; cursor: pointer; margin-top: 16px; }}
.btn-primary {{ background: #C55A11; color: white; }}
.btn-cancel {{ background: #888; color: white; margin-left: 8px; }}
.msg {{ padding: 10px; border-radius: 5px; margin-top: 10px; font-size: 12px; display: none; }}
.msg.ok {{ background: #E2EFDA; color: #375623; display: block; }}
.msg.error {{ background: #FCE4D6; color: #843C0C; display: block; }}
.placeholder-help {{ background: #FFF8E7; border: 1px solid #FFD966; padding: 10px;
  border-radius: 5px; font-size: 11px; color: #7F6000; margin-bottom: 12px; }}
</style></head><body>
<div class="container">
<h1>📝 {title}</h1>
<div class="placeholder-help">
<b>ここには「採点の基準」だけを書いてください。</b><br>
以下はシステムが自動で付け足すため、テンプレート内に書く必要はありません。<br>
・フリーランスのプロフィール（スキル / 時給 / キーワード）<br>
・貼り付けられた求人テキスト<br>
・出力形式（JSON）の指定<br>
・利用者からAIへの要望<br>
※ プレースホルダー（{{skills}} など）は使えません。書いてもそのままAIに送られます。
</div>
<label>対象サイト</label>
<select id="p-site" style="width:100%;padding:8px 12px;border:1px solid #BFCFDF;border-radius:5px;font-size:13px">
{site_options}
</select>
<div style="font-size:11px;color:#777;margin-top:4px">
※「未割当」のプロンプトは採点に使われません（旧バージョンの保管用）。<br>
※ 有効化は同じサイト内で排他です。他サイトの有効プロンプトには影響しません。
</div>
<label>バージョン</label>
<input type="text" id="p-version" value="{version}" placeholder="v1.0">
<label>名前</label>
<input type="text" id="p-name" value="{name}" placeholder="案件評価プロンプト v1.0">
<label>備考</label>
<input type="text" id="p-note" value="{note}" placeholder="任意">
<label>テンプレート本文</label>
<textarea id="p-template">{template}</textarea>
<button class="btn btn-primary" onclick="savePrompt()">保存</button>
<a href="/admin" class="btn btn-cancel" style="text-decoration:none;display:inline-block">キャンセル</a>
<div id="msg" class="msg"></div>
</div>
<script>
const PROMPT_ID = {prompt_id};
async function savePrompt() {{
  const version = document.getElementById('p-version').value.trim();
  const name = document.getElementById('p-name').value.trim();
  const note = document.getElementById('p-note').value.trim();
  const site = document.getElementById('p-site').value;
  const template = document.getElementById('p-template').value;
  const msg = document.getElementById('msg');
  if (!version || !name || !template) {{
    msg.className = 'msg error';
    msg.textContent = 'バージョン・名前・本文は必須です';
    return;
  }}
  const url = PROMPT_ID === null
    ? '/admin/prompt/create'
    : '/admin/prompt/' + PROMPT_ID + '/update';
  const res = await fetch(url, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{version, name, template, note, site}}),
  }});
  const data = await res.json();
  if (res.ok) {{
    msg.className = 'msg ok';
    if (PROMPT_ID === null) {{
      msg.innerHTML = '✅ 保存しました (ID: ' + data.id + ')<br>'
        + '<a href="/admin">管理画面に戻る</a>から有効化してください';
    }} else {{
      msg.innerHTML = '✅ 更新しました<br><a href="/admin">管理画面に戻る</a>';
    }}
  }} else {{
    msg.className = 'msg error';
    msg.textContent = '❌ ' + (data.message || 'エラー');
  }}
}}
</script>
</body></html>""")


def _validate_prompt_site(site: str):
    """空文字は「未割当」として許容。値がある場合のみ実在チェック。"""
    site = (site or "").strip().lower()
    if site and site not in SITES:
        return None, JSONResponse(status_code=400,
                                  content={"message": f"未知のサイトです: {site}"})
    return (site or None), None


@app.post("/admin/prompt/create")
async def admin_create_prompt(request: Request, username: str = Depends(verify_admin)):
    body = await request.json()
    version  = body.get("version", "").strip()
    name     = body.get("name", "").strip()
    template = body.get("template", "")
    note     = body.get("note", "")
    if not version or not name or not template:
        return JSONResponse(status_code=400, content={"message": "必須項目が不足しています"})
    site, err = _validate_prompt_site(body.get("site", ""))
    if err:
        return err
    return create_prompt(version, name, template, note, site)


@app.post("/admin/prompt/{prompt_id}/update")
async def admin_update_prompt(prompt_id: int, request: Request,
                              username: str = Depends(verify_admin)):
    body = await request.json()
    version  = body.get("version", "").strip()
    name     = body.get("name", "").strip()
    template = body.get("template", "")
    note     = body.get("note", "")
    if not version or not name or not template:
        return JSONResponse(status_code=400, content={"message": "必須項目が不足しています"})
    site, err = _validate_prompt_site(body.get("site", ""))
    if err:
        return err
    return update_prompt(prompt_id, version, name, template, note, site)


@app.post("/admin/prompt/{prompt_id}/activate")
async def admin_activate_prompt(prompt_id: int, username: str = Depends(verify_admin)):
    return activate_prompt(prompt_id)


# ── セレクター管理API ──







@app.post("/admin/file/upload")
async def admin_upload_file(
    component: str = Form(...),
    version: str = Form(...),
    note: str = Form(""),
    file: UploadFile = File(...),
    username: str = Depends(verify_admin),
):
    if component not in ALLOWED_FILE_COMPONENTS:
        return JSONResponse(status_code=400,
            content={"success": False, "message": "componentはexcelのみ指定できます"})
    if not version.strip():
        return JSONResponse(status_code=400,
            content={"success": False, "message": "versionが必要です"})

    file_bytes = await file.read()
    if not file_bytes:
        return JSONResponse(status_code=400,
            content={"success": False, "message": "ファイルが空です"})

    content_type = ALLOWED_FILE_COMPONENTS[component]
    result = upload_file(
        component=component,
        filename=file.filename,
        content_type=content_type,
        file_data=file_bytes,
        version=version.strip(),
        note=note.strip(),
    )
    return result


@app.post("/admin/file/{file_id}/activate")
async def admin_activate_file(file_id: int, username: str = Depends(verify_admin)):
    result = activate_file(file_id)
    if not result.get("success"):
        return JSONResponse(status_code=404, content=result)
    return result


@app.delete("/admin/file/{file_id}")
async def admin_delete_file(file_id: int, username: str = Depends(verify_admin)):
    return delete_file(file_id)


# ══════════════════════════════════════════
# AI設定（為替レート）の管理画面
# ══════════════════════════════════════════
# 画面とルートは settings_admin.py に置いている。main.py は既に2500行を超えており、
# ここに画面を足すと以後この巨大なファイルを触り続けることになるため。
#
# verify_admin を引数で渡しているのは循環importを避けるため。
# main.py が settings_admin を import するので、逆向きには import できない。
# 引数で渡せば、認証の実装は main.py の1か所のままにできる。
#
# ファイル末尾に置いているのは、verify_admin の定義より後である必要があるため
# （関数そのものを渡すので、定義済みでなければならない）。
from settings_admin import build_settings_router

app.include_router(build_settings_router(verify_admin))
