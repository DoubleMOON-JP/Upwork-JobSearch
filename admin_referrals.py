# admin_referrals.py
# ─────────────────────────────────────────────────────────────
# 紹介リンク管理（SNS流入計測）の画面と、コード登録・停止・CSV出力API。
#
# main.py に足す2行（ファイル末尾）:
#     from admin_referrals import build_referrals_router
#     app.include_router(build_referrals_router(verify_any))
#
# 中身は main.py から動かしただけで、HTMLは1文字も変えていない
# （再インデントもしていない。f-string の中身が変わるため）。
# ─────────────────────────────────────────────────────────────
import csv
import io
import logging
import os
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from db_redesign import (
    create_referral, set_referral_active, referral_exists,
    referral_stats, referral_detail_rows, active_staff_names,
)
from plans import plan_label, plan_price_usd
from sites import DEFAULT_SITE, is_valid_site, site_label, enabled_sites
from admin_ui import ui_text, esc

log = logging.getLogger(__name__)

# 紹介リンクのURLを組み立てるために使う。main.py と同じ値を同じ既定値で読む
# （main.py を import すると循環参照になるため、環境変数から直接取る）。
BASE_URL = os.environ.get("BASE_URL", "https://jobsearch.doublemoon.biz")

router = APIRouter()
security = HTTPBasic()

# main.py の verify_any をここへ差し込む（build_referrals_router で設定）。
# main.py が このモジュールを import するため、逆向きには import できない。
_verify_any = None


def build_referrals_router(verify_any) -> APIRouter:
    """main.py から認証関数を受け取り、ルーターを返す。"""
    global _verify_any
    _verify_any = verify_any
    return router


def verify(credentials: HTTPBasicCredentials = Depends(security)) -> dict:
    """認証は main.py の verify_any に委譲する。管理者・スタッフのどちらでも通る。"""
    if _verify_any is None:
        raise RuntimeError(
            "認証関数が未設定です。main.py で "
            "app.include_router(build_referrals_router(verify_any)) を呼んでください。"
        )
    return _verify_any(credentials)


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


@router.get("/admin/referrals", response_class=HTMLResponse)
async def admin_referrals(period: str = "all", who: dict = Depends(verify)):
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
<title>{T['ref_title']} - MOONpicker</title>
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


@router.post("/admin/referral/create")
async def admin_referral_create(request: Request,
                                who: dict = Depends(verify)):
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


@router.post("/admin/referral/active")
async def admin_referral_active(request: Request,
                                who: dict = Depends(verify)):
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


@router.get("/admin/referrals/csv")
async def admin_referrals_csv(period: str = "all",
                              who: dict = Depends(verify)):
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


@router.get("/admin/referrals/csv/detail")
async def admin_referrals_csv_detail(who: dict = Depends(verify)):
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

