# admin_prompts.py
# ─────────────────────────────────────────────────────────────
# 採点プロンプトの編集画面と、作成・更新・有効化API。
#
# main.py に足す2行（ファイル末尾）:
#     from admin_prompts import build_prompts_router
#     app.include_router(build_prompts_router(verify_admin))
#
# **システム管理者のみ**が通る（採点の品質を左右するため）。
#
# 【ルート定義の順序】/admin/prompts/new を /admin/prompts/{id} より
# 先に定義すること。逆にすると new が {id} として解釈される。
# このファイル内の並び順がそのまま登録順になる。
#
# 中身は main.py から動かしただけで、HTMLは1文字も変えていない。
# ─────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import get_all_prompts, create_prompt, update_prompt, activate_prompt
from sites import SITES, site_label, enabled_sites
from admin_ui import esc

router = APIRouter()
security = HTTPBasic()

# main.py の verify_admin をここへ差し込む（build_prompts_router で設定）。
# main.py が このモジュールを import するため、逆向きには import できない。
_verify_admin = None


def build_prompts_router(verify_admin) -> APIRouter:
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
            "app.include_router(build_prompts_router(verify_admin)) を呼んでください。"
        )
    return _verify_admin(credentials)


# ── プロンプト管理API ──
@router.get("/admin/prompts/new", response_class=HTMLResponse)
async def admin_prompt_new(username: str = Depends(verify)):
    """プロンプト新規作成画面"""
    return _render_prompt_edit_page(None)


@router.get("/admin/prompts/{prompt_id}", response_class=HTMLResponse)
async def admin_prompt_edit(prompt_id: int, username: str = Depends(verify)):
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


@router.post("/admin/prompt/create")
async def admin_create_prompt(request: Request, username: str = Depends(verify)):
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


@router.post("/admin/prompt/{prompt_id}/update")
async def admin_update_prompt(prompt_id: int, request: Request,
                              username: str = Depends(verify)):
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


@router.post("/admin/prompt/{prompt_id}/activate")
async def admin_activate_prompt(prompt_id: int, username: str = Depends(verify)):
    return activate_prompt(prompt_id)

