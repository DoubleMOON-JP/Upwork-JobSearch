"""
main.py - Upwork JobSearch 本番サーバー v2
ライセンス認証＋プロンプト/セレクター配信型
"""
import os
import json
import secrets as sec_module
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import (
    init_db,
    validate_license, create_license, extend_license,
    get_all_licenses, export_licenses_csv,
    get_license_with_config,
    get_active_prompt, get_all_prompts, create_prompt, activate_prompt,
    get_active_selectors, get_all_selectors, create_selectors, activate_selectors,
    get_excludes, get_all_excludes, add_exclude, delete_exclude,
    get_ai_settings, update_ai_setting,
    get_latest_version,
)

# ══════════════════════════════════════════
# 初期化
# ══════════════════════════════════════════
app = FastAPI(title="Upwork JobSearch API", version="2.0.0")
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 環境変数
ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
BASE_URL       = os.environ.get("BASE_URL",       "https://upwork.doublemoon.biz")

security = HTTPBasic()


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
@app.get("/")
async def root():
    return {
        "service": "Upwork JobSearch API",
        "version": "2.0.0",
        "status":  "running",
        "design":  "license-auth + prompt-distribution",
    }


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
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Upwork JobSearch - マイページ</title>
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
  <h1>⚡ Upwork JobSearch</h1>
  <p>マイページ — ライセンス確認・ファイルダウンロード</p>
</div>

<div class="container">

  <div class="card">
    <h2>🔑 ライセンス確認</h2>
    <div class="form-row">
      <label>ライセンスキー</label>
      <input type="text" id="lic-key" placeholder="UPWK-XXXX-XXXX-XXXX">
    </div>
    <button class="btn btn-primary" onclick="checkLicense()">確認する</button>
    <div id="lic-result" class="result-box"></div>
  </div>

  <div class="card">
    <h2>📥 ファイルダウンロード</h2>
    <p style="font-size:12px;color:#777;margin-bottom:14px">
      ライセンス確認後、以下のボタンからファイルをダウンロードしてください。
    </p>
    <a href="/download/excel" class="dl-btn dl-excel">
      📊 Excelファイルをダウンロード（.xlsm）
    </a>
    <a href="/download/extension" class="dl-btn dl-ext">
      🧩 Chromeエクステンションをダウンロード（.zip）
    </a>
  </div>

  <div class="card">
    <h2>📖 セットアップ手順</h2>
    <ol style="font-size:13px;line-height:2;padding-left:20px;color:#333">
      <li>Excelファイルをダウンロードして開く</li>
      <li>設定シートにライセンスキーとGemini APIキーを入力</li>
      <li>プロフィールシートにスキル・時給等を入力</li>
      <li>Chromeエクステンションをダウンロード・インストール</li>
      <li>Excelのホームシートでキーワードを入力して「検索開始」ボタンを押す</li>
      <li>Chromeのエクステンションで「AI評価を実行」ボタンを押す</li>
      <li>ExcelでCSVを取り込んで結果を確認</li>
    </ol>
  </div>

</div>

<div class="footer">© 2026 Upwork JobSearch</div>

<script>
async function checkLicense() {
  const key = document.getElementById('lic-key').value.trim();
  if (!key) { alert('ライセンスキーを入力してください'); return; }

  const box = document.getElementById('lic-result');
  box.style.display = 'block';
  box.className = 'result-box';
  box.innerHTML = '確認中...';

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
        '✅ ライセンス有効 <span class="badge">' + lic.days_left + '日残り</span></div>' +
        '<div class="row-item"><span class="row-label">プラン</span><span class="row-value">' + lic.plan + '</span></div>' +
        '<div class="row-item"><span class="row-label">有効期限</span><span class="row-value">' + lic.expires_at + '</span></div>' +
        '<div class="row-item"><span class="row-label">最新エクステンション</span><span class="row-value">v' + ver.extension + '</span></div>' +
        '<div class="row-item"><span class="row-label">最新Excelファイル</span><span class="row-value">v' + ver.excel + '</span></div>';
    } else {
      box.className = 'result-box error';
      box.innerHTML = '❌ ' + (data.message || '無効なライセンスキーです');
    }
  } catch(e) {
    box.className = 'result-box error';
    box.innerHTML = '❌ サーバーに接続できませんでした';
  }
}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ══════════════════════════════════════════
# ファイルダウンロード
# ══════════════════════════════════════════
@app.get("/download/excel")
async def download_excel():
    path = Path("files/UpworkJobSearch.xlsm")
    if not path.exists():
        return JSONResponse(status_code=404,
            content={"message": "ファイルが準備中です"})
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        headers={"Content-Disposition": "attachment; filename=UpworkJobSearch.xlsm"},
    )


@app.get("/download/extension")
async def download_extension():
    path = Path("files/upwork_jobsearch_extension.zip")
    if not path.exists():
        return JSONResponse(status_code=404,
            content={"message": "ファイルが準備中です"})
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=upwork_jobsearch_extension.zip"},
    )


# ══════════════════════════════════════════
# 管理者画面
# ══════════════════════════════════════════
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(username: str = Depends(verify_admin)):
    licenses = get_all_licenses()
    prompts  = get_all_prompts()
    selectors_all = get_all_selectors('upwork')
    excludes_all  = get_all_excludes()
    today = datetime.today().date().isoformat()

    # ライセンス一覧
    lic_rows = ""
    for lic in licenses:
        expired = lic["expires_at"] < today
        badge = '<span style="color:#843C0C">期限切れ</span>' if expired else \
                '<span style="color:#375623">有効</span>'
        lic_rows += f"""
        <tr>
          <td>{lic['id']}</td>
          <td style="font-family:monospace;font-size:11px">{lic['license_key']}</td>
          <td>{lic['email']}</td>
          <td>{lic['plan']}</td>
          <td>{badge}</td>
          <td>{lic['expires_at']}</td>
          <td>
            <button onclick="extend('{lic['license_key']}')"
              style="font-size:11px;padding:3px 8px;cursor:pointer">+1ヶ月</button>
          </td>
        </tr>"""

    # プロンプト一覧
    prompt_rows = ""
    for p in prompts:
        active_badge = '<span style="background:#E2EFDA;color:#375623;padding:2px 6px;border-radius:3px;font-size:11px">有効</span>' \
                       if p['is_active'] else \
                       '<button onclick="activatePrompt(' + str(p['id']) + ')" style="font-size:11px;padding:2px 6px;cursor:pointer">有効化</button>'
        prompt_rows += f"""
        <tr>
          <td>{p['id']}</td>
          <td>{p['version']}</td>
          <td>{p['name']}</td>
          <td>{active_badge}</td>
          <td>{p['created_at'][:10]}</td>
          <td><a href="/admin/prompts/{p['id']}" style="font-size:11px;color:#2E75B6">編集</a></td>
        </tr>"""

    # セレクター一覧
    sel_rows = ""
    for s in selectors_all:
        active_badge = '<span style="background:#E2EFDA;color:#375623;padding:2px 6px;border-radius:3px;font-size:11px">有効</span>' \
                       if s['is_active'] else \
                       '<button onclick="activateSelector(' + str(s['id']) + ')" style="font-size:11px;padding:2px 6px;cursor:pointer">有効化</button>'
        sel_rows += f"""
        <tr>
          <td>{s['id']}</td>
          <td>{s['version']}</td>
          <td>{s['service']}</td>
          <td>{active_badge}</td>
          <td>{s['created_at'][:10]}</td>
          <td><a href="/admin/selectors/{s['id']}" style="font-size:11px;color:#2E75B6">編集</a></td>
        </tr>"""

    # 除外リスト
    exc_rows = ""
    for e in excludes_all:
        exc_rows += f"""
        <tr>
          <td>{e['id']}</td>
          <td>{e['category']}</td>
          <td>{e['keyword']}</td>
          <td>
            <button onclick="delExclude({e['id']})"
              style="font-size:11px;padding:2px 6px;cursor:pointer;color:#843C0C">削除</button>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>管理者画面 - Upwork JobSearch</title>
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
  <h1>⚡ Upwork JobSearch — 管理者画面</h1>
  <span style="font-size:11px;opacity:0.7">{datetime.now().strftime('%Y/%m/%d %H:%M')}</span>
</div>

<div class="container">

  <!-- 統計 -->
  <div class="card">
    <h2>📊 統計</h2>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-num">{len(licenses)}</div>
        <div class="stat-label">総ライセンス数</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{sum(1 for l in licenses if l['expires_at'] >= today)}</div>
        <div class="stat-label">有効ライセンス</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{len(prompts)}</div>
        <div class="stat-label">登録プロンプト</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{len(excludes_all)}</div>
        <div class="stat-label">除外キーワード</div>
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
      <select id="new-plan">
        <option value="1month">1ヶ月</option>
        <option value="3month">3ヶ月</option>
        <option value="6month">6ヶ月</option>
        <option value="1year">1年</option>
      </select>
      <label>備考</label>
      <input type="text" id="new-note" placeholder="任意" style="min-width:140px">
      <button class="btn btn-primary" onclick="issueLicense()">発行</button>
    </div>
    <div id="issue-msg" class="msg"></div>
  </div>

  <!-- ライセンス一覧 -->
  <div class="card">
    <h2>📋 ライセンス一覧
      <a href="/admin/backup" style="font-size:11px;color:#2E75B6">CSVバックアップ</a>
    </h2>
    <table>
      <thead>
        <tr><th>ID</th><th>ライセンスキー</th><th>メール</th><th>プラン</th><th>状態</th><th>有効期限</th><th>操作</th></tr>
      </thead>
      <tbody>{lic_rows}</tbody>
    </table>
  </div>

  <!-- プロンプト管理 -->
  <div class="card">
    <h2>📝 プロンプト管理
      <a href="/admin/prompts/new" class="new-link">+ 新規作成</a>
    </h2>
    <table>
      <thead>
        <tr><th>ID</th><th>バージョン</th><th>名前</th><th>状態</th><th>作成日</th><th>操作</th></tr>
      </thead>
      <tbody>{prompt_rows}</tbody>
    </table>
  </div>

  <!-- セレクター管理 -->
  <div class="card">
    <h2>🎯 DOMセレクター管理
      <a href="/admin/selectors/new" class="new-link">+ 新規作成</a>
    </h2>
    <table>
      <thead>
        <tr><th>ID</th><th>バージョン</th><th>サービス</th><th>状態</th><th>作成日</th><th>操作</th></tr>
      </thead>
      <tbody>{sel_rows}</tbody>
    </table>
  </div>

  <!-- 除外リスト管理 -->
  <div class="card">
    <h2>🚫 除外キーワード管理</h2>
    <div class="form-row">
      <label>カテゴリ</label>
      <select id="exc-category">
        <option value="skill_tags">skill_tags</option>
      </select>
      <label>キーワード</label>
      <input type="text" id="exc-keyword" placeholder="除外したい文字列" style="min-width:200px">
      <button class="btn btn-blue" onclick="addExclude()">追加</button>
    </div>
    <div id="exc-msg" class="msg"></div>
    <table style="margin-top:14px">
      <thead>
        <tr><th>ID</th><th>カテゴリ</th><th>キーワード</th><th>操作</th></tr>
      </thead>
      <tbody>{exc_rows}</tbody>
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

async function activatePrompt(id) {{
  if (!confirm('このプロンプトを有効化しますか？（他は無効化されます）')) return;
  const res = await fetch('/admin/prompt/' + id + '/activate', {{method: 'POST'}});
  if (res.ok) location.reload();
  else alert('エラーが発生しました');
}}

async function activateSelector(id) {{
  if (!confirm('このセレクター定義を有効化しますか？')) return;
  const res = await fetch('/admin/selector/' + id + '/activate', {{method: 'POST'}});
  if (res.ok) location.reload();
  else alert('エラーが発生しました');
}}

async function addExclude() {{
  const cat = document.getElementById('exc-category').value;
  const kw  = document.getElementById('exc-keyword').value.trim();
  const msg = document.getElementById('exc-msg');
  if (!kw) {{ alert('キーワードを入力してください'); return; }}
  const res = await fetch('/admin/exclude/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{category: cat, keyword: kw}}),
  }});
  if (res.ok) {{
    msg.className = 'msg ok';
    msg.innerHTML = '✅ 追加しました';
    setTimeout(() => location.reload(), 1000);
  }} else {{
    msg.className = 'msg error';
    msg.innerHTML = '❌ エラーが発生しました';
  }}
}}

async function delExclude(id) {{
  if (!confirm('削除しますか？')) return;
  const res = await fetch('/admin/exclude/' + id, {{method: 'DELETE'}});
  if (res.ok) location.reload();
  else alert('エラーが発生しました');
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
@app.get("/admin/prompts/{prompt_id}", response_class=HTMLResponse)
async def admin_prompt_edit(prompt_id: int, username: str = Depends(verify_admin)):
    """プロンプト編集画面"""
    prompts = get_all_prompts()
    target = next((p for p in prompts if p['id'] == prompt_id), None)
    if not target:
        return HTMLResponse(content="<h1>プロンプトが見つかりません</h1>", status_code=404)

    return _render_prompt_edit_page(target)


@app.get("/admin/prompts/new", response_class=HTMLResponse)
async def admin_prompt_new(username: str = Depends(verify_admin)):
    """プロンプト新規作成画面"""
    return _render_prompt_edit_page(None)


def _render_prompt_edit_page(target: dict | None):
    is_new = target is None
    title = "新規プロンプト作成" if is_new else f"プロンプト編集 (ID: {target['id']})"
    version = "" if is_new else target['version']
    name = "" if is_new else target['name']
    template = "" if is_new else target['template']
    note = "" if is_new else (target.get('note') or '')

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
{{prefer_line}}, {{jobs_text}} ※ {{ と }} はテンプレート内では {{{{ と }}}} と書く必要があります
</div>
<label>バージョン</label>
<input type="text" id="p-version" value="{version}" placeholder="v1.0">
<label>名前</label>
<input type="text" id="p-name" value="{name}" placeholder="Upwork案件評価プロンプト v1.0">
<label>備考</label>
<input type="text" id="p-note" value="{note}" placeholder="任意">
<label>テンプレート本文</label>
<textarea id="p-template">{template}</textarea>
<button class="btn btn-primary" onclick="savePrompt()">保存</button>
<a href="/admin" class="btn btn-cancel" style="text-decoration:none;display:inline-block">キャンセル</a>
<div id="msg" class="msg"></div>
</div>
<script>
async function savePrompt() {{
  const version = document.getElementById('p-version').value.trim();
  const name = document.getElementById('p-name').value.trim();
  const note = document.getElementById('p-note').value.trim();
  const template = document.getElementById('p-template').value;
  const msg = document.getElementById('msg');
  if (!version || !name || !template) {{
    msg.className = 'msg error';
    msg.textContent = 'バージョン・名前・本文は必須です';
    return;
  }}
  const res = await fetch('/admin/prompt/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{version, name, template, note}}),
  }});
  const data = await res.json();
  if (res.ok) {{
    msg.className = 'msg ok';
    msg.innerHTML = '✅ 保存しました (ID: ' + data.id + ')<br>'
      + '<a href="/admin">管理画面に戻る</a>から有効化してください';
  }} else {{
    msg.className = 'msg error';
    msg.textContent = '❌ ' + (data.message || 'エラー');
  }}
}}
</script>
</body></html>""")


@app.post("/admin/prompt/create")
async def admin_create_prompt(request: Request, username: str = Depends(verify_admin)):
    body = await request.json()
    version  = body.get("version", "").strip()
    name     = body.get("name", "").strip()
    template = body.get("template", "")
    note     = body.get("note", "")
    if not version or not name or not template:
        return JSONResponse(status_code=400, content={"message": "必須項目が不足しています"})
    return create_prompt(version, name, template, note)


@app.post("/admin/prompt/{prompt_id}/activate")
async def admin_activate_prompt(prompt_id: int, username: str = Depends(verify_admin)):
    return activate_prompt(prompt_id)


# ── セレクター管理API ──
@app.get("/admin/selectors/{selector_id}", response_class=HTMLResponse)
async def admin_selector_edit(selector_id: int, username: str = Depends(verify_admin)):
    selectors = get_all_selectors('upwork')
    target = next((s for s in selectors if s['id'] == selector_id), None)
    if not target:
        return HTMLResponse(content="<h1>セレクター定義が見つかりません</h1>", status_code=404)
    return _render_selector_edit_page(target)


@app.get("/admin/selectors/new", response_class=HTMLResponse)
async def admin_selector_new(username: str = Depends(verify_admin)):
    return _render_selector_edit_page(None)


def _render_selector_edit_page(target: dict | None):
    is_new = target is None
    title = "新規セレクター定義作成" if is_new else f"セレクター定義編集 (ID: {target['id']})"
    version = "" if is_new else target['version']
    service = "upwork" if is_new else target['service']
    config_str = "{}" if is_new else json.dumps(json.loads(target['config_json']), ensure_ascii=False, indent=2)
    note = "" if is_new else (target.get('note') or '')

    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>{title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Arial, sans-serif; background: #F5F7FA; padding: 24px; }}
.container {{ max-width: 900px; margin: 0 auto; background: white; padding: 24px; border-radius: 10px; }}
h1 {{ font-size: 18px; color: #1A2B4A; margin-bottom: 16px; }}
label {{ display: block; font-size: 12px; color: #555; margin: 12px 0 4px; }}
input, textarea, select {{ width: 100%; padding: 8px 12px; border: 1px solid #BFCFDF;
  border-radius: 5px; font-size: 13px; font-family: monospace; }}
textarea {{ min-height: 400px; resize: vertical; }}
.btn {{ padding: 10px 24px; border: none; border-radius: 5px;
  font-size: 13px; font-weight: bold; cursor: pointer; margin-top: 16px; }}
.btn-primary {{ background: #C55A11; color: white; }}
.btn-cancel {{ background: #888; color: white; margin-left: 8px; }}
.msg {{ padding: 10px; border-radius: 5px; margin-top: 10px; font-size: 12px; display: none; }}
.msg.ok {{ background: #E2EFDA; color: #375623; display: block; }}
.msg.error {{ background: #FCE4D6; color: #843C0C; display: block; }}
.help {{ background: #FFF8E7; border: 1px solid #FFD966; padding: 10px;
  border-radius: 5px; font-size: 11px; color: #7F6000; margin-bottom: 12px; }}
</style></head><body>
<div class="container">
<h1>🎯 {title}</h1>
<div class="help">JSON形式で記述してください。必須キー: title_selector, section_selector, budget_keywords, posted_keywords, max_jobs等</div>
<label>バージョン</label>
<input type="text" id="s-version" value="{version}" placeholder="v1.0">
<label>サービス</label>
<input type="text" id="s-service" value="{service}" placeholder="upwork">
<label>備考</label>
<input type="text" id="s-note" value="{note}" placeholder="任意">
<label>セレクター定義（JSON）</label>
<textarea id="s-config">{config_str}</textarea>
<button class="btn btn-primary" onclick="saveSelector()">保存</button>
<a href="/admin" class="btn btn-cancel" style="text-decoration:none;display:inline-block">キャンセル</a>
<div id="msg" class="msg"></div>
</div>
<script>
async function saveSelector() {{
  const version = document.getElementById('s-version').value.trim();
  const service = document.getElementById('s-service').value.trim();
  const note = document.getElementById('s-note').value.trim();
  const configStr = document.getElementById('s-config').value;
  const msg = document.getElementById('msg');
  let config;
  try {{ config = JSON.parse(configStr); }} catch(e) {{
    msg.className = 'msg error';
    msg.textContent = '❌ JSONの形式が正しくありません: ' + e.message;
    return;
  }}
  if (!version || !service) {{
    msg.className = 'msg error';
    msg.textContent = 'バージョンとサービスは必須です';
    return;
  }}
  const res = await fetch('/admin/selector/create', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{version, service, config, note}}),
  }});
  const data = await res.json();
  if (res.ok) {{
    msg.className = 'msg ok';
    msg.innerHTML = '✅ 保存しました (ID: ' + data.id + ')<br>'
      + '<a href="/admin">管理画面に戻る</a>から有効化してください';
  }} else {{
    msg.className = 'msg error';
    msg.textContent = '❌ ' + (data.message || 'エラー');
  }}
}}
</script>
</body></html>""")


@app.post("/admin/selector/create")
async def admin_create_selector(request: Request, username: str = Depends(verify_admin)):
    body = await request.json()
    version = body.get("version", "").strip()
    service = body.get("service", "upwork").strip()
    config  = body.get("config", {})
    note    = body.get("note", "")
    if not version or not service or not config:
        return JSONResponse(status_code=400, content={"message": "必須項目が不足しています"})
    return create_selectors(version, service, config, note)


@app.post("/admin/selector/{selector_id}/activate")
async def admin_activate_selector(selector_id: int, username: str = Depends(verify_admin)):
    return activate_selectors(selector_id)


# ── 除外リスト管理API ──
@app.post("/admin/exclude/add")
async def admin_add_exclude(request: Request, username: str = Depends(verify_admin)):
    body = await request.json()
    category = body.get("category", "skill_tags")
    keyword  = body.get("keyword", "").strip()
    if not keyword:
        return JSONResponse(status_code=400, content={"message": "keywordが必要です"})
    return add_exclude(category, keyword)


@app.delete("/admin/exclude/{exclude_id}")
async def admin_delete_exclude(exclude_id: int, username: str = Depends(verify_admin)):
    return delete_exclude(exclude_id)
