"""
main.py - Upwork Job Monitor 本番サーバー
"""
import os
import re
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets as sec_module

from database import (
    init_db, validate_license, create_license,
    extend_license, get_all_licenses, get_latest_version,
    export_licenses_csv,
)

# ══════════════════════════════════════════
# 初期化
# ══════════════════════════════════════════
app = FastAPI(title="Upwork JobSearch API", version="1.0.0")
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 環境変数
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL",   "gemini-2.5-flash")
ADMIN_USER     = os.environ.get("ADMIN_USER",     "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
BASE_URL       = os.environ.get("BASE_URL",       "https://upwork.doublemoon.biz")

security = HTTPBasic()


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = sec_module.compare_digest(credentials.username, ADMIN_USER)
    ok_pass = sec_module.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="認証失敗",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ══════════════════════════════════════════
# 基本エンドポイント
# ══════════════════════════════════════════
@app.get("/")
async def root():
    return {
        "service": "Upwork Job Monitor API",
        "version": "1.0.0",
        "status":  "running",
        "model":   GEMINI_MODEL,
    }


@app.get("/ping")
async def ping():
    return {
        "status":      "ok",
        "server_time": datetime.utcnow().isoformat() + "Z",
    }


# ══════════════════════════════════════════
# ライセンス認証API
# ══════════════════════════════════════════
@app.post("/license/validate")
async def license_validate(request: Request):
    """ライセンスキーの有効性確認"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "リクエスト形式が不正です"})

    license_key = body.get("license_key", "").strip()
    if not license_key:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "license_keyが未送信です"})

    result = validate_license(license_key)
    if result["valid"]:
        # バージョン情報も一緒に返す
        ext_ver   = get_latest_version("extension")
        excel_ver = get_latest_version("excel")
        return {
            "status":            "valid",
            "email":             result["email"],
            "plan":              result["plan"],
            "expires_at":        result["expires_at"],
            "days_left":         result["days_left"],
            "latest_extension":  ext_ver.get("version", "1.0.0"),
            "latest_excel":      excel_ver.get("version", "1.0.0"),
        }
    else:
        return JSONResponse(status_code=403, content={
            "status":  "invalid",
            "reason":  result["reason"],
            "message": result["message"],
        })


# ══════════════════════════════════════════
# 事前チェックAPI
# ══════════════════════════════════════════
@app.post("/precheck")
async def precheck(request: Request):
    """
    実行前にライセンスとAPIキーを一括確認
    エクステンションが実行ボタン押下直後に呼び出す
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "リクエスト形式が不正です"})

    license_key = body.get("license_key", "").strip()
    api_key     = body.get("api_key",     "").strip()

    errors = []

    # ① ライセンス確認
    if not license_key:
        errors.append({"code": "no_license", "message": "ライセンスキーが未入力です"})
    else:
        lic = validate_license(license_key)
        if not lic["valid"]:
            errors.append({"code": lic["reason"], "message": lic["message"]})

    # ② APIキー確認
    if not api_key:
        errors.append({"code": "no_api_key", "message": "AIのAPIキーが未入力です"})
    elif not api_key.startswith("AIza"):
        errors.append({"code": "invalid_api_key_format",
                        "message": "APIキーの形式が正しくありません（AIzaで始まる必要があります）"})

    if errors:
        return JSONResponse(status_code=403, content={
            "status": "error",
            "errors": errors,
        })

    return {"status": "ok", "message": "事前チェック完了。処理を開始できます"}


# ══════════════════════════════════════════
# AI評価API（本番版）
# ══════════════════════════════════════════
@app.post("/evaluate")
async def evaluate(request: Request):
    """
    案件データを受け取りAIでスコアリングして返す
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "リクエスト形式が不正です"})

    license_key = body.get("license_key", "").strip()
    api_key     = body.get("api_key",     "").strip()
    jobs        = body.get("jobs",        [])
    profile     = body.get("profile",     {})

    # ── バリデーション ──
    if not license_key:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "license_keyが未送信です"})

    lic = validate_license(license_key)
    if not lic["valid"]:
        return JSONResponse(status_code=403, content={
            "status":  "license_error",
            "message": lic["message"],
        })

    if not api_key:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "api_keyが未送信です"})

    if not jobs:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "案件データが未送信です"})

    # ── プロフィール情報 ──
    skills        = profile.get("skills",           "Python, FastAPI")
    category      = profile.get("category",         "バックエンド開発")
    min_rate      = profile.get("min_rate",         "$30")
    exclude_kw    = profile.get("exclude_keywords", "")
    prefer_kw     = profile.get("prefer_keywords",  "")
    threshold     = body.get("score_threshold", 0)

    # ── 案件リスト（最大20件）──
    target_jobs = jobs[:20]

    # ── プロンプト作成 ──
    jobs_text = ""
    for i, job in enumerate(target_jobs):
        skills_str = ", ".join(job.get("skills", [])) if job.get("skills") else "不明"
        jobs_text += f"""
【案件{i+1}】
タイトル: {job.get('title', '')}
予算・時給: {job.get('budget', '不明')}
投稿日時: {job.get('posted', '不明')}
スキル: {skills_str}
説明: {str(job.get('description', '説明なし'))[:300]}
---"""

    exclude_line = f"\n避けたいキーワード: {exclude_kw}" if exclude_kw else ""
    prefer_line  = f"\n優先したいキーワード: {prefer_kw}"  if prefer_kw  else ""

    prompt = f"""あなたはフリーランサーの案件評価アシスタントです。
以下のフリーランサープロフィールに基づいて各案件を0〜100点でスコアリングしてください。

【フリーランサープロフィール】
スキル: {skills}
得意カテゴリ: {category}
希望最低時給: {min_rate}{exclude_line}{prefer_line}

【評価基準】
- スキルの一致度（高いほど高得点）
- 予算・時給が希望最低時給以上か
- 避けたいキーワードが含まれる場合は大幅減点
- 優先したいキーワードが含まれる場合は加点

【評価対象案件】
{jobs_text}

【出力形式】
JSON形式のみで回答してください。
{{
  "results": [
    {{
      "index": 0,
      "score": 85,
      "reason": "スコアの理由を1〜2文で日本語で",
      "recommendation": "応募推奨"
    }}
  ]
}}
recommendationは「応募推奨」「様子見」「スキップ」のいずれか。
resultsの件数は評価対象案件と同じ件数にすること。"""

    # ── Gemini API呼び出し ──
    gemini_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    gemini_payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":      0.3,
            "maxOutputTokens":  4096,
            "responseMimeType": "application/json",
        },
    }

    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                gemini_url,
                json=gemini_payload,
                headers={"Content-Type": "application/json"},
            )
        elapsed = round(time.time() - start_time, 2)

        if res.status_code != 200:
            return JSONResponse(status_code=502, content={
                "status":  "gemini_error",
                "message": f"Gemini APIエラー: HTTP {res.status_code}",
                "detail":  res.text[:300],
            })

        gemini_data = res.json()
        raw_text = gemini_data["candidates"][0]["content"]["parts"][0]["text"]

        # JSONを抽出
        clean = raw_text.strip()
        m = re.search(r'(\{[\s\S]*\})', clean)
        if m:
            clean = m.group(1).strip()

        ai_result = json.loads(clean)

    except json.JSONDecodeError as e:
        return JSONResponse(status_code=502, content={
            "status":  "json_parse_error",
            "message": "AIレスポンスのJSON変換に失敗",
            "detail":  str(e),
        })
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={
            "status":  "timeout",
            "message": "Gemini APIがタイムアウトしました（60秒）",
        })
    except httpx.RequestError as e:
        return JSONResponse(status_code=503, content={
            "status":  "connection_error",
            "message": f"Gemini APIへの接続エラー: {e}",
        })
    except (KeyError, IndexError) as e:
        return JSONResponse(status_code=502, content={
            "status":  "parse_error",
            "message": f"Geminiレスポンスのパースに失敗: {e}",
        })

    # ── 案件データとスコアを結合 ──
    scored_jobs = []
    for r in ai_result.get("results", []):
        idx = r.get("index", 0)
        if idx < len(target_jobs):
            job = target_jobs[idx]
            scored_jobs.append({
                "title":          job.get("title",  ""),
                "url":            job.get("url",    ""),
                "budget":         job.get("budget", ""),
                "posted":         job.get("posted", ""),
                "skills":         job.get("skills", []),
                "score":          r.get("score",          0),
                "reason":         r.get("reason",          ""),
                "recommendation": r.get("recommendation", ""),
            })

    scored_jobs.sort(key=lambda x: x["score"], reverse=True)

    return {
        "status":       "success",
        "message":      f"{len(scored_jobs)}件の案件をAIが評価しました",
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
        "elapsed_sec":  elapsed,
        "job_count":    len(scored_jobs),
        "scored_jobs":  scored_jobs,
    }


# ══════════════════════════════════════════
# バージョン確認API
# ══════════════════════════════════════════
@app.get("/version/{component}")
async def get_version(component: str):
    """エクステンション・Excelのバージョン確認"""
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
    """ユーザー向けマイページ"""
    html = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Upwork Job Monitor - マイページ</title>
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
    .btn-blue    { background: #2E75B6; color: white; }
    .btn-green   { background: #1F7A4D; color: white; }
    .btn:hover   { opacity: 0.88; }
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
             font-size: 11px; font-weight: bold; }
    .badge-ok  { background: #E2EFDA; color: #375623; }
    .badge-ng  { background: #FCE4D6; color: #843C0C; }
  </style>
</head>
<body>
<div class="header">
  <h1>⚡ Upwork Job Monitor</h1>
  <p>マイページ — ライセンス確認・ファイルダウンロード</p>
</div>

<div class="container">

  <!-- ライセンス確認 -->
  <div class="card">
    <h2>🔑 ライセンス確認</h2>
    <div class="form-row">
      <label>ライセンスキー</label>
      <input type="text" id="lic-key" placeholder="UPWK-XXXX-XXXX-XXXX">
    </div>
    <button class="btn btn-primary" onclick="checkLicense()">確認する</button>
    <div id="lic-result" class="result-box"></div>
  </div>

  <!-- ファイルダウンロード -->
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
    <p style="font-size:11px;color:#777;margin-top:8px">
      ダウンロードURL: https://upwork.doublemoon.biz/download/
    </p>
  </div>

  <!-- 使い方 -->
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

<div class="footer">
  © 2026 Upwork Job Monitor　｜　<a href="https://upwork.doublemoon.biz/mypage" style="color:#2E75B6">https://upwork.doublemoon.biz/mypage</a>
</div>

<script>
async function checkLicense() {
  const key = document.getElementById('lic-key').value.trim();
  if (!key) { alert('ライセンスキーを入力してください'); return; }

  const box = document.getElementById('lic-result');
  box.style.display = 'block';
  box.className = 'result-box';
  box.innerHTML = '確認中...';

  try {
    const res  = await fetch('/license/validate', {
      method:  'POST',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify({license_key: key}),
    });
    const data = await res.json();

    if (res.ok && data.status === 'valid') {
      box.className = 'result-box ok';
      box.innerHTML = `
        <div style="font-weight:bold;margin-bottom:8px">
          ✅ ライセンス有効
          <span class="badge badge-ok">${data.days_left}日残り</span>
        </div>
        <div class="row-item"><span class="row-label">プラン</span><span class="row-value">${data.plan}</span></div>
        <div class="row-item"><span class="row-label">有効期限</span><span class="row-value">${data.expires_at}</span></div>
        <div class="row-item"><span class="row-label">最新エクステンション</span><span class="row-value">v${data.latest_extension}</span></div>
        <div class="row-item"><span class="row-label">最新Excelファイル</span><span class="row-value">v${data.latest_excel}</span></div>`;
    } else {
      box.className = 'result-box error';
      box.innerHTML = `❌ ${data.message || '無効なライセンスキーです'}`;
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
    """Excelファイルのダウンロード"""
    path = Path("files/UpworkMonitor.xlsm")
    if not path.exists():
        return JSONResponse(status_code=404,
            content={"message": "ファイルが準備中です。しばらくお待ちください。"})
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        headers={"Content-Disposition": "attachment; filename=UpworkMonitor.xlsm"},
    )


@app.get("/download/extension")
async def download_extension():
    """Chromeエクステンションのダウンロード"""
    path = Path("files/upwork_monitor_extension.zip")
    if not path.exists():
        return JSONResponse(status_code=404,
            content={"message": "ファイルが準備中です。しばらくお待ちください。"})
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=upwork_monitor_extension.zip"},
    )


# ══════════════════════════════════════════
# 管理者画面
# ══════════════════════════════════════════
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(username: str = Depends(verify_admin)):
    """管理者画面"""
    licenses = get_all_licenses()
    today    = datetime.today().date().isoformat()

    rows_html = ""
    for lic in licenses:
        expired = lic["expires_at"] < today
        badge   = '<span style="color:#843C0C">期限切れ</span>' if expired else \
                  '<span style="color:#375623">有効</span>'
        rows_html += f"""
        <tr>
          <td>{lic['id']}</td>
          <td style="font-family:monospace;font-size:12px">{lic['license_key']}</td>
          <td>{lic['email']}</td>
          <td>{lic['plan']}</td>
          <td>{badge}</td>
          <td>{lic['expires_at']}</td>
          <td>{lic['created_at'][:10]}</td>
          <td>
            <button onclick="extend('{lic['license_key']}')"
              style="font-size:11px;padding:3px 8px;cursor:pointer">+1ヶ月</button>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>管理者画面 - Upwork Job Monitor</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A; font-size: 13px; }}
    .header {{ background: #1A2B4A; color: white; padding: 14px 24px;
               display: flex; align-items: center; justify-content: space-between; }}
    .header h1 {{ font-size: 16px; }}
    .container {{ max-width: 1100px; margin: 24px auto; padding: 0 16px; }}
    .card {{ background: white; border-radius: 8px; padding: 20px;
             margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    .card h2 {{ font-size: 14px; color: #1A2B4A; margin-bottom: 14px;
                padding-bottom: 6px; border-bottom: 2px solid #EBF3FB; }}
    .form-row {{ display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }}
    .form-row label {{ font-size: 12px; color: #555; min-width: 90px; }}
    .form-row input, .form-row select {{
      padding: 7px 10px; border: 1px solid #BFCFDF; border-radius: 5px; font-size: 12px; }}
    .btn {{ padding: 8px 16px; border: none; border-radius: 5px;
            font-size: 12px; font-weight: bold; cursor: pointer; }}
    .btn-primary {{ background: #C55A11; color: white; }}
    .btn-green   {{ background: #1F7A4D; color: white; }}
    .btn-blue    {{ background: #2E75B6; color: white; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{ background: #2E3A4E; color: white; padding: 8px 10px; text-align: left; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #F0F0F0; }}
    tr:hover {{ background: #F5F7FA; }}
    .msg {{ padding: 10px 14px; border-radius: 6px; margin-top: 10px;
            display: none; font-size: 12px; }}
    .msg.ok    {{ background: #E2EFDA; color: #375623; display: block; }}
    .msg.error {{ background: #FCE4D6; color: #843C0C; display: block; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }}
    .stat-card {{ background: #F0F6FC; border-radius: 6px; padding: 14px; text-align: center; }}
    .stat-num  {{ font-size: 28px; font-weight: bold; color: #1A2B4A; }}
    .stat-label{{ font-size: 11px; color: #777; margin-top: 4px; }}
  </style>
</head>
<body>
<div class="header">
  <h1>⚡ Upwork Job Monitor — 管理者画面</h1>
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
        <div class="stat-num">{sum(1 for l in licenses if l['expires_at'] < today)}</div>
        <div class="stat-label">期限切れ</div>
      </div>
    </div>
  </div>

  <!-- ライセンス発行 -->
  <div class="card">
    <h2>🔑 ライセンス発行</h2>
    <div class="form-row">
      <label>メールアドレス</label>
      <input type="email" id="new-email" placeholder="user@example.com" style="min-width:220px">
      <label>プラン</label>
      <select id="new-plan">
        <option value="1month">1ヶ月</option>
        <option value="3month">3ヶ月</option>
        <option value="6month">6ヶ月</option>
        <option value="1year">1年</option>
      </select>
      <label>備考</label>
      <input type="text" id="new-note" placeholder="Upwork購入 等" style="min-width:160px">
      <button class="btn btn-primary" onclick="issueLicense()">発行する</button>
    </div>
    <div id="issue-msg" class="msg"></div>
  </div>

  <!-- ライセンス一覧 -->
  <div class="card">
    <h2>📋 ライセンス一覧
      <a href="/admin/backup" style="float:right;font-size:11px;color:#2E75B6">CSVバックアップ</a>
    </h2>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>ライセンスキー</th><th>メール</th><th>プラン</th>
          <th>状態</th><th>有効期限</th><th>発行日</th><th>操作</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
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

  const res  = await fetch('/admin/license/create', {{
    method:  'POST',
    headers: {{'Content-Type': 'application/json'}},
    body:    JSON.stringify({{email, plan, note}}),
  }});
  const data = await res.json();

  if (res.ok) {{
    msg.className = 'msg ok';
    msg.innerHTML = '✅ 発行完了：<strong>' + data.license_key + '</strong>'
      + '　有効期限: ' + data.expires_at;
    setTimeout(() => location.reload(), 2000);
  }} else {{
    msg.className = 'msg error';
    msg.innerHTML = '❌ ' + (data.message || 'エラーが発生しました');
  }}
}}

async function extend(key) {{
  if (!confirm(key + ' を1ヶ月延長しますか？')) return;
  const res  = await fetch('/admin/license/extend', {{
    method:  'POST',
    headers: {{'Content-Type': 'application/json'}},
    body:    JSON.stringify({{license_key: key, months: 1}}),
  }});
  const data = await res.json();
  if (res.ok) {{
    alert('延長完了。新しい有効期限: ' + data.new_expires_at);
    location.reload();
  }} else {{
    alert('エラー: ' + (data.message || '不明なエラー'));
  }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.post("/admin/license/create")
async def admin_create_license(
    request: Request,
    username: str = Depends(verify_admin),
):
    """ライセンスキーを発行（管理者のみ）"""
    body  = await request.json()
    email = body.get("email", "").strip()
    plan  = body.get("plan",  "1month")
    note  = body.get("note",  "")

    if not email:
        return JSONResponse(status_code=400,
            content={"message": "emailが必要です"})

    result = create_license(email=email, plan=plan, note=note)
    return result


@app.post("/admin/license/extend")
async def admin_extend_license(
    request: Request,
    username: str = Depends(verify_admin),
):
    """ライセンスを延長（管理者のみ）"""
    body        = await request.json()
    license_key = body.get("license_key", "").strip()
    months      = int(body.get("months", 1))

    if not license_key:
        return JSONResponse(status_code=400,
            content={"message": "license_keyが必要です"})

    result = extend_license(license_key=license_key, months=months)
    if not result["success"]:
        return JSONResponse(status_code=404,
            content={"message": result["message"]})
    return result


@app.get("/admin/backup")
async def admin_backup(username: str = Depends(verify_admin)):
    """DBをCSVでバックアップ（管理者のみ）"""
    csv_data = export_licenses_csv()
    filename = f"licenses_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    bom      = "\uFEFF"
    return Response(
        content=(bom + csv_data).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
