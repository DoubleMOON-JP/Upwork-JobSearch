# admin_licenses.py
# ─────────────────────────────────────────────────────────────
# ライセンス一覧の画面と、ライセンス操作API一式。
#
# 【main.py から分けた理由】
# main.py が2500行を超え、画面を1つ直すたびに巨大なファイル全体を
# 触ることになっていた。ここは画面1枚とそのAPIで完結しており、
# 他の画面と依存関係がない。
#
# 【main.py に足す2行】ファイル末尾に置く。
#     from admin_licenses import build_licenses_router
#     app.include_router(build_licenses_router(verify_any))
#
# 【認証の受け渡し】
# main.py が このモジュールを import するため、逆向きには import できない
# （循環参照になる）。そこで verify_any を引数で受け取り、下の verify() から
# 呼び出す。認証の実装は main.py の1か所のままにできる。
#
# 【中身は main.py から動かしただけ】
# 画面のHTMLは1文字も変えていない。再インデントもしていない
# （f-string の中身が変わってしまうため）。
# ─────────────────────────────────────────────────────────────
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from database import (
    create_license, extend_license, search_licenses, get_license_stats,
    delete_license, delete_expired_licenses, export_licenses_csv,
)
from db_redesign import apply_plan_change, get_license_row, set_mail_status
from plans import PLANS, is_valid_plan
from admin_ui import ui_text, esc, plan_label_ui

# ログは main.py と分ける。Renderのログに [admin_licenses] と出るため、
# どの画面で起きたことかが追いやすくなる。
log = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBasic()

# main.py の verify_any をここへ差し込む（build_licenses_router で設定）。
_verify_any = None


def build_licenses_router(verify_any) -> APIRouter:
    """main.py から認証関数を受け取り、ルーターを返す。"""
    global _verify_any
    _verify_any = verify_any
    return router


def verify(credentials: HTTPBasicCredentials = Depends(security)) -> dict:
    """認証は main.py の verify_any に委譲する。管理者・スタッフのどちらでも通る。"""
    if _verify_any is None:
        raise RuntimeError(
            "認証関数が未設定です。main.py で "
            "app.include_router(build_licenses_router(verify_any)) を呼んでください。"
        )
    return _verify_any(credentials)


# ══════════════════════════════════════════
# ライセンス一覧ページ（検索・絞り込み・ページ送り・削除）
# ══════════════════════════════════════════
LICENSES_PER_PAGE = 50


@router.get("/admin/licenses", response_class=HTMLResponse)
async def admin_licenses_page(
    q: str = "", status: str = "all", page: int = 1,
    who: dict = Depends(verify),
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
  <title>{T['lic_title']} - MOONpicker</title>
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
@router.post("/admin/license/create")
async def admin_create_license(request: Request, who: dict = Depends(verify)):
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


@router.post("/admin/license/resend")
async def admin_resend_license(request: Request, who: dict = Depends(verify)):
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


@router.post("/admin/license/extend")
async def admin_extend_license(request: Request, who: dict = Depends(verify)):
    body = await request.json()
    license_key = body.get("license_key", "").strip()
    months = int(body.get("months", 1))
    if not license_key:
        return JSONResponse(status_code=400, content={"message": "license_keyが必要です"})
    result = extend_license(license_key=license_key, months=months)
    if not result["success"]:
        return JSONResponse(status_code=404, content={"message": result["message"]})
    return result


@router.post("/admin/license/plan")
async def admin_change_license_plan(request: Request, who: dict = Depends(verify)):
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


@router.post("/admin/license/delete")
async def admin_delete_license(request: Request, who: dict = Depends(verify)):
    """ライセンスを1件削除する。決済に紐づくものは条件を満たす場合のみ。"""
    body = await request.json()
    license_key = body.get("license_key", "").strip()
    if not license_key:
        return JSONResponse(status_code=400, content={"message": "license_keyが必要です"})
    result = delete_license(license_key)
    if not result.get("success"):
        return JSONResponse(status_code=400, content=result)
    return result


@router.post("/admin/license/delete-expired")
async def admin_delete_expired_licenses(request: Request, who: dict = Depends(verify)):
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


@router.get("/admin/backup")
async def admin_backup(who: dict = Depends(verify)):
    csv_data = export_licenses_csv()
    filename = f"licenses_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    bom = "\uFEFF"
    return Response(
        content=(bom + csv_data).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

