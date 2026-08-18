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

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import (
    init_db, get_license_with_config, get_latest_version, get_active_file
)

# ── リデザイン追加分 ──
from db_redesign import (  # DBマイグレーション・広告欄・紹介リンク・スタッフ照合
    migrate, get_promo, find_license_by_checkout, record_referral_visit,
    referral_site, verify_staff
)
from plans import (  # プラン定義（単一情報源）
    plan_label
)
from sites import (  # 対応求人サイト定義（単一情報源）
    DEFAULT_SITE, get_site, is_valid_site
)
from evaluate import router as evaluate_router  # 採点API: POST /evaluate
from payments import router as payments_router  # 決済Webhook: POST /webhook/{provider}

# ══════════════════════════════════════════
# 版数とデプロイの識別
# ══════════════════════════════════════════
# APP_VERSION は「宣言した版数」。リリースのたびにこの1行だけを更新する。
# v3.9 以降ずっと 3.8.0 のまま放置され、デプロイ確認の役に立たなくなっていた。
#
# GIT_COMMIT は「実際に動いているコード」。Render がデプロイごとに設定する
# 環境変数から取るため、APP_VERSION の更新を忘れても必ず変わる。
# 「新しいコードが本当に載ったか」はこちらで判定する。
# 環境変数が無い場合（ローカル起動など）は "unknown" を返す。
# ここが "unknown" のままなら、環境変数名が違うということ。
APP_VERSION = "3.27.0"
GIT_COMMIT = (
    os.environ.get("RENDER_GIT_COMMIT")
    or os.environ.get("GIT_COMMIT")
    or ""
)[:7] or "unknown"

# ══════════════════════════════════════════
# 初期化
# ══════════════════════════════════════════
app = FastAPI(title="JobSearch API", version=APP_VERSION)
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

security = HTTPBasic()


# ── 管理画面の共通部品 ───────────────────────────────────
# 画面文言の辞書（UI_TEXT）とエスケープ処理は admin_ui.py へ移した。
# 画面を独立ファイルへ切り出すにあたり、どのモジュールからも参照できる
# 場所に置く必要があったため。main.py は紹介リンク管理などで引き続き使う。
from admin_ui import esc

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
    """稼働確認。障害検知（対応予定 No.5）の監視先としても使う想定。

    commit はデプロイのたびに変わるため、デプロイが反映されたかを
    ここだけで判定できる。version の更新漏れに影響されない。
    """
    return {
        "service": "JobSearch API",
        "version": APP_VERSION,
        "commit":  GIT_COMMIT,
        "status":  "running",
    }


# ── 広告欄 ──
@app.get("/promo")
async def promo_get():
    """フロントが起動時に取得。未設定・OFFなら表示されない。"""
    return get_promo()




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

# ══════════════════════════════════════════
# ライセンス一覧（画面＋操作API）
# ══════════════════════════════════════════
# 画面とルートは admin_licenses.py に置いている。main.py を小さく保ち、
# 画面を直すときにアプリ本体を触らずに済むようにするため。
#
# verify_any を引数で渡しているのは循環importを避けるため。
# ファイル末尾に置いているのは、verify_any の定義より後である必要があるため。
from admin_licenses import build_licenses_router

app.include_router(build_licenses_router(verify_any))

# ══════════════════════════════════════════
# スタッフ用画面 / 紹介リンク管理
# ══════════════════════════════════════════
# いずれも画面モジュールへ切り出している。切り出しの型は README.txt を参照。
from staff_console import build_staff_router
from admin_referrals import build_referrals_router

app.include_router(build_staff_router(verify_any))
app.include_router(build_referrals_router(verify_any))

# ══════════════════════════════════════════
# 管理トップ / プロンプト管理
# ══════════════════════════════════════════
# いずれもシステム管理者専用のため verify_admin を渡す。
from admin_home import build_home_router
from admin_prompts import build_prompts_router

app.include_router(build_home_router(verify_admin))
app.include_router(build_prompts_router(verify_admin))