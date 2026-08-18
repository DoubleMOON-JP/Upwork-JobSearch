# admin_home.py
# ─────────────────────────────────────────────────────────────
# 管理トップ /admin と、その画面から操作する3つの機能のAPI。
#   ・広告欄の保存        POST /admin/promo/save
#   ・担当者マスタ        POST /admin/staff/*
#   ・配布ファイル管理     POST/DELETE /admin/file/*
# いずれも管理トップの画面内にUIがあるため、画面とセットで移している。
#
# main.py に足す2行（ファイル末尾）:
#     from admin_home import build_home_router
#     app.include_router(build_home_router(verify_admin))
#
# **システム管理者のみ**が通る。他の画面モジュール（ライセンス一覧・
# 紹介リンク管理・スタッフ画面）は verify_any だが、ここは verify_admin。
#
# 中身は main.py から動かしただけで、HTMLは1文字も変えていない
# （再インデントもしていない。f-string の中身が変わるため）。
# ─────────────────────────────────────────────────────────────
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import (
    get_license_stats, get_all_prompts,
    upload_file, get_all_files, activate_file, delete_file,
)
from db_redesign import (
    get_promo, save_promo,
    list_staff, create_staff, set_staff_active, set_staff_password,
)
from plans import PLANS
from sites import site_label
from admin_ui import esc

# 管理画面からアップロードできる配布ファイルの種類と Content-Type。
# （Chrome拡張はリデザインで廃止したため excel のみ）
ALLOWED_FILE_COMPONENTS = {
    "excel": "application/vnd.ms-excel.sheet.macroEnabled.12",
}

router = APIRouter()
security = HTTPBasic()

# main.py の verify_admin をここへ差し込む（build_home_router で設定）。
# main.py が このモジュールを import するため、逆向きには import できない。
_verify_admin = None


def build_home_router(verify_admin) -> APIRouter:
    """main.py から認証関数を受け取り、ルーターを返す。"""
    global _verify_admin
    _verify_admin = verify_admin
    return router


def verify(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """認証は main.py の verify_admin に委譲する。**システム管理者のみ**通る。
    スタッフの資格情報では403になる。"""
    if _verify_admin is None:
        raise RuntimeError(
            "認証関数が未設定です。main.py で "
            "app.include_router(build_home_router(verify_admin)) を呼んでください。"
        )
    return _verify_admin(credentials)


@router.post("/admin/promo/save")
async def admin_promo_save(request: Request, username: str = Depends(verify)):
    data = await request.json()
    return save_promo(
        data.get("title", ""), data.get("body", ""),
        data.get("url", ""), bool(data.get("enabled")),
    )


# ── 担当者マスタ（システム管理者のみ）──────────────────────
@router.post("/admin/staff/create")
async def admin_staff_create(request: Request, username: str = Depends(verify)):
    """担当者を登録する。パスワードはハッシュ化して保存される。"""
    data = await request.json()
    return create_staff(
        login_id     = data.get("login_id", ""),
        display_name = data.get("display_name", ""),
        password     = data.get("password", ""),
        note         = data.get("note", ""),
    )


@router.post("/admin/staff/{staff_id}/active")
async def admin_staff_active(staff_id: int, request: Request,
                             username: str = Depends(verify)):
    """有効／停止の切り替え。削除は用意していない（実績が追えなくなるため）。"""
    data = await request.json()
    return set_staff_active(staff_id, bool(data.get("active")))


@router.post("/admin/staff/{staff_id}/password")
async def admin_staff_password(staff_id: int, request: Request,
                               username: str = Depends(verify)):
    """パスワードの再設定。本人からは変更できないため、管理者が行う。"""
    data = await request.json()
    return set_staff_password(staff_id, data.get("password", ""))


# ══════════════════════════════════════════
# 管理者画面
# ══════════════════════════════════════════

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(username: str = Depends(verify)):
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


# ── セレクター管理API ──







@router.post("/admin/file/upload")
async def admin_upload_file(
    component: str = Form(...),
    version: str = Form(...),
    note: str = Form(""),
    file: UploadFile = File(...),
    username: str = Depends(verify),
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


@router.post("/admin/file/{file_id}/activate")
async def admin_activate_file(file_id: int, username: str = Depends(verify)):
    result = activate_file(file_id)
    if not result.get("success"):
        return JSONResponse(status_code=404, content=result)
    return result


@router.delete("/admin/file/{file_id}")
async def admin_delete_file(file_id: int, username: str = Depends(verify)):
    return delete_file(file_id)

