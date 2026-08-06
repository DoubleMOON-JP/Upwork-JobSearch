"""
main.py - JobSearch 本番サーバー v3.8（マルチ求人サイト対応）
ライセンス認証＋プロンプト/セレクター配信型
"""
import os
import re
import json
import logging
import secrets as sec_module
from datetime import datetime

# アプリ側の log.info(...) を Render のログに出す。
# 未設定だとルートロガーが WARNING のままで、決済Webhookの
# "license issued ..." などが一切表示されず、障害調査ができない。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

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
from db_redesign import migrate, get_promo, save_promo, apply_plan_change  # DBマイグレーション＋広告欄＋プラン変更
from plans import PLANS, plan_label, is_valid_plan       # プラン定義（単一情報源）
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


def esc(v) -> str:
    """管理画面HTMLへ値を埋め込む際のエスケープ（属性値・テキスト共用）。"""
    return (
        str("" if v is None else v)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = sec_module.compare_digest(credentials.username, ADMIN_USER)
    ok_pass = sec_module.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401, detail="認証失敗",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


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
# Webマイページ
# ══════════════════════════════════════════
@app.get("/mypage", response_class=HTMLResponse)
async def mypage():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>JobSearch - My Page</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A; }
    .header { background: #1A2B4A; color: white; padding: 16px 24px; }
    .header h1 { font-size: 20px; }
    .header p  { font-size: 12px; opacity: 0.7; margin-top: 4px; }
    .container { max-width: 720px; margin: 32px auto; padding: 0 16px; }
    .card { background: white; border-radius: 10px; padding: 24px; margin-bottom: 20px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
    .card h2 { font-size: 15px; color: #1A2B4A; margin-bottom: 16px;
               padding-bottom: 8px; border-bottom: 2px solid #EBF3FB; }
    .form-row { display: flex; gap: 10px; margin-bottom: 12px; align-items: center; }
    .form-row label { font-size: 13px; color: #555; min-width: 120px; }
    .form-row input { flex: 1; padding: 8px 12px; border: 1px solid #BFCFDF;
                      border-radius: 6px; font-size: 13px; }
    .btn { padding: 10px 20px; border: none; border-radius: 6px; font-size: 13px;
           font-weight: bold; cursor: pointer; }
    .btn-primary { background: #C55A11; color: white; }
    .btn:hover { opacity: 0.88; }
    .result-box { background: #F5F7FA; border: 1px solid #BFCFDF; border-radius: 6px;
                  padding: 14px; margin-top: 12px; font-size: 13px; display: none; }
    .result-box.ok    { background: #E2EFDA; border-color: #A9D18E; color: #375623; }
    .result-box.error { background: #FCE4D6; border-color: #F4B8A0; color: #843C0C; }
    .row-item { display: flex; justify-content: space-between; padding: 4px 0;
                border-bottom: 1px solid rgba(0,0,0,0.06); font-size: 13px; }
    .row-item:last-child { border-bottom: none; }
    .row-label { color: #777; }
    .row-value { font-weight: bold; }
    .dl-btn { display: block; padding: 12px 16px; border-radius: 6px; text-decoration: none;
               text-align: center; font-weight: bold; font-size: 13px; margin-bottom: 8px; }
    .dl-excel { background: #1F7A4D; color: white; }
    .dl-ext   { background: #2E75B6; color: white; }
    .dl-btn:hover { opacity: 0.88; }
    .footer { text-align: center; color: #999; font-size: 12px; padding: 24px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
             font-size: 11px; font-weight: bold; background: #E2EFDA; color: #375623; }
  </style>
</head>
<body>
<div class="header">
  <h1>⚡ JobSearch</h1>
  <p>My Page — License Check &amp; File Downloads</p>
</div>

<div class="container">

  <div class="card">
    <h2>🔑 License Check</h2>
    <div class="form-row">
      <label>License Key</label>
      <input type="text" id="lic-key" placeholder="DMJS-XXXX-XXXX-XXXX">
    </div>
    <button class="btn btn-primary" onclick="checkLicense()">Check</button>
    <div id="lic-result" class="result-box"></div>
  </div>

  <div class="card">
    <h2>📥 File Downloads</h2>
    <p style="font-size:12px;color:#777;margin-bottom:14px">
      After confirming your license, open the web app below. The Excel viewer is optional.
    </p>
    <a href="/" class="dl-btn dl-excel">
      🚀 Open the web app
    </a>
    <a href="/download/excel" class="dl-btn dl-ext">
      📊 Download Excel File (optional)
    </a>
  </div>

  <div class="card">
    <h2>📖 How to use</h2>
    <ol style="font-size:13px;line-height:2;padding-left:20px;color:#333">
      <li>Open the web app and sign in with your license key</li>
      <li>Fill in your profile (skills, desired rate, keywords) and save it</li>
      <li>On the job board, open its job search page and sort by newest</li>
      <li>Select the page (Ctrl/⌘+A), copy it (Ctrl/⌘+C), and paste it into the app</li>
      <li>Press "Score these jobs" to get the triage board</li>
      <li>Optionally download the CSV and import it into the Excel viewer</li>
    </ol>
    <p style="font-size:12px;color:#777;margin-top:10px">
      No browser extension or API key of your own is required.
    </p>
  </div>

</div>

<div class="footer">© 2026 JobSearch</div>

<script>
async function checkLicense() {
  const key = document.getElementById('lic-key').value.trim();
  if (!key) { alert('Please enter your license key'); return; }

  const box = document.getElementById('lic-result');
  box.style.display = 'block';
  box.className = 'result-box';
  box.innerHTML = 'Checking...';

  try {
    const res = await fetch('/license/validate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({license_key: key}),
    });
    const data = await res.json();

    if (res.ok && data.status === 'valid') {
      const lic = data.license;
      const ver = data.versions;
      box.className = 'result-box ok';
      box.innerHTML =
        '<div style="font-weight:bold;margin-bottom:8px">' +
        '✅ License Valid <span class="badge">' + lic.days_left + ' days left</span></div>' +
        '<div class="row-item"><span class="row-label">Plan</span><span class="row-value">' + lic.plan + '</span></div>' +
        '<div class="row-item"><span class="row-label">Expires On</span><span class="row-value">' + lic.expires_at + '</span></div>' +
        '<div class="row-item"><span class="row-label">Latest Excel File</span><span class="row-value">v' + ver.excel + '</span></div>';
    } else {
      box.className = 'result-box error';
      box.innerHTML = '❌ ' + (data.message || 'Invalid license key');
    }
  } catch(e) {
    box.className = 'result-box error';
    box.innerHTML = '❌ Could not connect to the server';
  }
}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ══════════════════════════════════════════
# ファイルダウンロード
# ══════════════════════════════════════════
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
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(username: str = Depends(verify_admin)):
    stats    = get_license_stats()
    prompts  = get_all_prompts()
    files_all     = get_all_files()
    promo         = get_promo()

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
    username: str = Depends(verify_admin),
):
    page = max(int(page), 1)
    offset = (page - 1) * LICENSES_PER_PAGE
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
            badge = '<span style="color:#843C0C">無効化</span>'
        elif expired:
            badge = '<span style="color:#843C0C">期限切れ</span>'
        else:
            badge = '<span style="color:#375623">有効</span>'

        current_plan = lic["plan"]
        opts = "".join(
            f'<option value="{esc(code)}"'
            f'{" selected" if code == current_plan else ""}>{esc(info["label"])}</option>'
            for code, info in PLANS.items()
        )
        if not is_valid_plan(current_plan):
            opts = f'<option value="{esc(current_plan)}" selected>{esc(current_plan)}（旧）</option>' + opts

        paid = "決済" if lic.get("subscription_id") else "手動"
        rows += f"""
        <tr>
          <td>{lic['id']}</td>
          <td style="font-family:monospace;font-size:11px">{esc(lic['license_key'])}</td>
          <td>{esc(lic['email'])}</td>
          <td><select id="plan-{lic['id']}" style="font-size:11px;padding:2px 4px">{opts}</select></td>
          <td style="font-size:11px;color:#777">{paid}</td>
          <td>{badge}</td>
          <td>{lic['expires_at']}</td>
          <td style="white-space:nowrap">
            <button onclick="extend('{esc(lic['license_key'])}')"
              style="font-size:11px;padding:3px 7px;cursor:pointer">+1ヶ月</button>
            <button onclick="changePlan('{esc(lic['license_key'])}', {lic['id']})"
              style="font-size:11px;padding:3px 7px;cursor:pointer">プラン変更</button>
            <button onclick="delLicense('{esc(lic['license_key'])}', '{esc(lic['email'])}')"
              style="font-size:11px;padding:3px 7px;cursor:pointer;color:#843C0C">削除</button>
          </td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="8" style="text-align:center;color:#999;padding:24px">該当するライセンスはありません</td></tr>'

    def sel(v):
        return " selected" if status == v else ""

    def page_link(n, label):
        return (f'<a href="/admin/licenses?q={esc(q)}&status={esc(status)}&page={n}" '
                f'style="padding:5px 10px;border:1px solid #BFCFDF;border-radius:4px;'
                f'text-decoration:none;color:#2E75B6;font-size:12px">{label}</a>')

    pager = ""
    if page > 1:
        pager += page_link(page - 1, "← 前へ") + " "
    pager += f'<span style="font-size:12px;color:#777">{page} / {last_page} ページ（全{total}件）</span>'
    if page < last_page:
        pager += " " + page_link(page + 1, "次へ →")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>ライセンス一覧 - JobSearch</title>
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
  <h1>📋 ライセンス一覧</h1>
  <a href="/admin">← 管理画面に戻る</a>
</div>

<div class="container">

  <div class="card">
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-num">{stats['total']}</div><div class="stat-label">総数</div></div>
      <div class="stat-card"><div class="stat-num" style="color:#1F7A4D">{stats['active']}</div><div class="stat-label">有効</div></div>
      <div class="stat-card"><div class="stat-num" style="color:#843C0C">{stats['expired']}</div><div class="stat-label">期限切れ</div></div>
      <div class="stat-card"><div class="stat-num">{stats['inactive']}</div><div class="stat-label">無効化</div></div>
    </div>
  </div>

  <div class="card">
    <h2>🔍 検索・絞り込み</h2>
    <form method="get" action="/admin/licenses" class="form-row">
      <label>キーワード</label>
      <input type="text" name="q" value="{esc(q)}" placeholder="ライセンスキー / メール" style="min-width:240px">
      <label>状態</label>
      <select name="status">
        <option value="all"{sel('all')}>すべて</option>
        <option value="active"{sel('active')}>有効</option>
        <option value="expired"{sel('expired')}>期限切れ</option>
        <option value="inactive"{sel('inactive')}>無効化</option>
      </select>
      <button type="submit" class="btn btn-blue">検索</button>
      <a href="/admin/licenses" style="font-size:12px;color:#777">クリア</a>
    </form>
  </div>

  <div class="card">
    <h2>📋 一覧</h2>
    <table>
      <thead>
        <tr><th>ID</th><th>ライセンスキー</th><th>メール</th><th>プラン</th><th>種別</th>
            <th>状態</th><th>有効期限</th><th>操作</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center">{pager}</div>
  </div>

  <div class="card">
    <h2>🗑 期限切れライセンスの一括削除</h2>
    <div class="warn">
      <b>削除すると元に戻せません。</b>実行前に
      <a href="/admin/backup" style="color:#2E75B6">CSVバックアップ</a> を取得してください。<br>
      決済に紐づくライセンスは、失効から30日経過するまで削除されません
      （支払いリトライ中に削除すると、更新時に別のキーが発行されてしまうため）。
      条件を満たさないものは自動的にスキップされます。
    </div>
    <div class="form-row">
      <label>失効から</label>
      <select id="del-days">
        <option value="30">30日以上経過</option>
        <option value="60">60日以上経過</option>
        <option value="90" selected>90日以上経過</option>
        <option value="180">180日以上経過</option>
      </select>
      <button class="btn btn-danger" onclick="delExpired()">まとめて削除</button>
    </div>
    <div id="del-msg" style="margin-top:10px;font-size:12px"></div>
  </div>

</div>

<script>
async function extend(key) {{
  if (!confirm(key + ' を1ヶ月延長しますか？')) return;
  const res = await fetch('/admin/license/extend', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key, months: 1}}),
  }});
  const data = await res.json();
  if (res.ok) {{ alert('延長完了。新しい有効期限: ' + data.new_expires_at); location.reload(); }}
  else {{ alert('エラー: ' + (data.message || '不明なエラー')); }}
}}

async function changePlan(key, id) {{
  const plan = document.getElementById('plan-' + id).value;
  if (!confirm(key + ' のプランを「' + plan + '」に変更しますか？\\n\\n・有効期限は変わりません\\n・上位プランへの変更時は、旧プランの残り回数を繰り越します')) {{
    location.reload(); return;
  }}
  const res = await fetch('/admin/license/plan', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key, plan: plan}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{
    let msg = 'プランを変更しました：' + data.old_plan + ' → ' + data.new_plan;
    msg += '\\n有効期限は変更していません。';
    if (data.carried_over > 0) {{ msg += '\\n\\n旧プランの残り ' + data.carried_over + ' 回を繰り越しました。'; }}
    if (data.remaining != null) {{ msg += '\\n今月の残り回数：' + data.remaining + ' 回（使用済み ' + data.used + ' 回）'; }}
    alert(msg);
  }} else {{ alert('エラー: ' + (data.message || '不明なエラー')); }}
  location.reload();
}}

async function delLicense(key, email) {{
  if (!confirm('このライセンスを削除しますか？\\n\\n' + key + '\\n' + email +
               '\\n\\n削除すると元に戻せません。')) return;
  const res = await fetch('/admin/license/delete', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{license_key: key}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{ alert('削除しました：' + key); location.reload(); }}
  else {{ alert('削除できませんでした\\n\\n' + (data.message || '不明なエラー')); }}
}}

async function delExpired() {{
  const days = document.getElementById('del-days').value;
  if (!confirm('失効から' + days + '日以上経過したライセンスをまとめて削除します。\\n\\n' +
               '元に戻せません。CSVバックアップは取得済みですか？')) return;
  if (!confirm('本当に実行しますか？（最終確認）')) return;
  const msg = document.getElementById('del-msg');
  msg.textContent = '削除中...';
  const res = await fetch('/admin/license/delete-expired', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{days: parseInt(days, 10)}}),
  }});
  const data = await res.json();
  if (res.ok && data.success) {{
    msg.innerHTML = '✅ ' + data.deleted_count + ' 件を削除しました。' +
      (data.skipped_count ? '（条件を満たさない ' + data.skipped_count + ' 件はスキップ）' : '');
    setTimeout(() => location.reload(), 2000);
  }} else {{ msg.textContent = '❌ ' + (data.message || 'エラーが発生しました'); }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── ライセンス操作API ──
@app.post("/admin/license/create")
async def admin_create_license(request: Request, username: str = Depends(verify_admin)):
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
    return create_license(email=email, plan=plan, note=note)


@app.post("/admin/license/extend")
async def admin_extend_license(request: Request, username: str = Depends(verify_admin)):
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
async def admin_change_license_plan(request: Request, username: str = Depends(verify_admin)):
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
async def admin_delete_license(request: Request, username: str = Depends(verify_admin)):
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
async def admin_delete_expired_licenses(request: Request, username: str = Depends(verify_admin)):
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
async def admin_backup(username: str = Depends(verify_admin)):
    csv_data = export_licenses_csv()
    filename = f"licenses_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    bom = "\uFEFF"
    return Response(
        content=(bom + csv_data).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
利用可能なプレースホルダー: {{skills}}, {{category}}, {{min_rate}}, {{exclude_line}},
{{prefer_line}}, {{ai_request_line}}, {{jobs_text}} ※ {{ と }} はテンプレート内では {{{{ と }}}} と書く必要があります
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
