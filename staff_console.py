# staff_console.py
# ─────────────────────────────────────────────────────────────
# スタッフ用画面 /staff（全編英語）。
#
# 画面1枚だけで完結する。ライセンス発行フォームは
# /admin/license/create（admin_licenses.py）へ POST しているため、
# このモジュール自身はAPIを持たない。
#
# main.py に足す2行（ファイル末尾）:
#     from staff_console import build_staff_router
#     app.include_router(build_staff_router(verify_any))
#
# 中身は main.py から動かしただけで、HTMLは1文字も変えていない
# （再インデントもしていない。f-string の中身が変わるため）。
# ─────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import get_license_stats
from plans import PLANS
from admin_ui import esc

router = APIRouter()
security = HTTPBasic()

# main.py の verify_any をここへ差し込む（build_staff_router で設定）。
# main.py が このモジュールを import するため、逆向きには import できない。
_verify_any = None


def build_staff_router(verify_any) -> APIRouter:
    """main.py から認証関数を受け取り、ルーターを返す。"""
    global _verify_any
    _verify_any = verify_any
    return router


def verify(credentials: HTTPBasicCredentials = Depends(security)) -> dict:
    """認証は main.py の verify_any に委譲する。管理者・スタッフのどちらでも通る。"""
    if _verify_any is None:
        raise RuntimeError(
            "認証関数が未設定です。main.py で "
            "app.include_router(build_staff_router(verify_any)) を呼んでください。"
        )
    return _verify_any(credentials)


# ══════════════════════════════════════════
# スタッフ用画面（英語表記）
# ══════════════════════════════════════════
@router.get("/staff", response_class=HTMLResponse)
async def staff_page(who: dict = Depends(verify)):
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

