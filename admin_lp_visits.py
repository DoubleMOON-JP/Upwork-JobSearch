# admin_lp_visits.py
# ─────────────────────────────────────────────────────────────
# LP到達の集計画面（広告の効果測定）。システム管理者のみ・日本語。
#
# 【main.py に足す2行】ファイル末尾に置く。
#     from admin_lp_visits import build_lp_visits_router
#     app.include_router(build_lp_visits_router(verify_admin))
#
# 【admin_referrals.py に足さなかった理由】
# あちらはスタッフも開ける日英切替の画面で、追記すると UI_TEXT（123項目）に
# 日英対を足すことになる。LP到達数は運営の判断材料であってスタッフ作業には
# 使わないため、管理者専用の独立した画面にした。
# 既存ファイルを1バイトも触らずに済むという利点もある
# （紹介リンク管理は 2026-09-06 に全経路を実測で確認したばかりのため）。
#
# 【この画面が答える問い】
#   ・広告でLPに何人来たか（媒体のツールに頼らず、自社の物差しで）
#   ・そのうち何人がチェックアウトまで進んだか
#     → CTAクリック相当の数は Polar の Deliveries（checkout.created）で数える。
#       ここには出てこない。両方を突き合わせて成約率を出すこと。
#
# 【referral_visits との違い】
#   referral_visits … /r/{code} を踏んだ回数
#   lp_visits       … /for/{site} が表示された回数
#   /r/ 経由で来た人は両方に1件ずつ入る。二重計上ではなく別の指標。
#   リダイレクトを解決する媒体（LinkedIn 等）では /r/ が記録されないため、
#   lp_visits のほうが実態に近いことがある。
# ─────────────────────────────────────────────────────────────
import csv
import io
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response

from db_redesign import (
    lp_visit_summary, lp_visit_by_ref, lp_visit_daily, lp_visit_rows,
)
from sites import site_label

log = logging.getLogger(__name__)


def build_lp_visits_router(verify_admin) -> APIRouter:
    """main.py から認証関数を受け取り、ルーターを返す。

    ルートを関数の内側で定義しているのは settings_admin.py と同じ形。
    Depends(verify_admin) をそのまま書けるため、認証を差し込む仕掛けが要らない。
    verify_admin を引数で受け取るのは循環importを避けるため
    （main.py がこのモジュールを import するので、逆向きにはできない）。
    """
    router = APIRouter()

    @router.get("/admin/lp-visits", response_class=HTMLResponse)
    async def admin_lp_visits(period: str = "30d",
                              username: str = Depends(verify_admin)):
        return _render(period)

    @router.get("/admin/lp-visits/csv")
    async def admin_lp_visits_csv(period: str = "30d",
                                  username: str = Depends(verify_admin)):
        return _csv(period)

    return router


def _period(period: str):
    """画面のタブに対応する期間を (from, to) で返す。None は無制限。
    admin_referrals.py の _ref_period と同じ区切り方にしてある
    （画面ごとに期間の定義が違うと、数字を並べたときに比較できなくなる）。"""
    today = datetime.today().date()
    if period == "today":
        return today.isoformat(), today.isoformat()
    if period == "7d":
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    if period == "30d":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if period == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    return None, None          # all


def esc(v) -> str:
    return (
        str(v if v is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, sans-serif; background: #F5F7FA; color: #1A1A1A;
       font-size: 13px; padding: 24px; }
.container { max-width: 980px; margin: 0 auto; }
.header { background: #1A2B4A; color: white; padding: 14px 20px; border-radius: 8px;
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: 16px; }
.header h1 { font-size: 16px; }
.header a { color: #9FB0CC; font-size: 12px; text-decoration: none; }
.card { background: white; border-radius: 8px; padding: 20px 24px; margin-bottom: 16px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.card h2 { font-size: 14px; color: #1A2B4A; margin-bottom: 14px;
           padding-bottom: 6px; border-bottom: 2px solid #EBF3FB; }
table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
th { background: #1F3864; color: #fff; font-size: 11px; padding: 8px 6px;
     text-align: left; }
td { padding: 8px 6px; border-bottom: 1px solid #EDF1F5; }
td.num, th.num { text-align: right; }
.tab { padding: 5px 12px; border-radius: 4px; text-decoration: none;
       font-size: 12px; margin-right: 6px; }
.tab.on { background: #1F3864; color: #fff; }
.tab.off { background: #F0F4F8; color: #2E75B6; }
.big { font-size: 22px; font-weight: bold; color: #1A2B4A; }
.kpi { display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 6px; }
.kpi div span { display: block; font-size: 11px; color: #777; font-weight: normal; }
.note { font-size: 11px; color: #777; margin-top: 10px; line-height: 1.8; }
.help { background: #FFF8E7; border: 1px solid #FFD966; padding: 10px 12px;
        border-radius: 5px; font-size: 11.5px; color: #7F6000; line-height: 1.8;
        margin-bottom: 12px; }
.bar { background: #C55A11; height: 10px; border-radius: 2px; display: inline-block;
       vertical-align: middle; }
a.dl { color: #2E75B6; font-size: 12px; margin-right: 18px; }
.err { background: #FCE4D6; color: #843C0C; padding: 10px 14px; border-radius: 6px;
       font-size: 12px; margin-bottom: 14px; line-height: 1.8; }
"""


def _render(period: str) -> HTMLResponse:
    d_from, d_to = _period(period)

    err = ""
    try:
        summary = lp_visit_summary(d_from, d_to)
        by_ref = lp_visit_by_ref(d_from, d_to)
        daily = lp_visit_daily(30)
    except Exception as e:
        # 集計に失敗しても画面は出す。数字が見えないことより、
        # 「画面ごと開けない」ほうが原因の切り分けを難しくするため。
        log.error("lp visit stats failed: %s", e)
        summary, by_ref, daily = [], [], []
        err = ("集計の取得に失敗しました。テーブルがまだ作られていない可能性があります"
               "（デプロイ直後はマイグレーションの完了を待ってください）。<br>"
               f"詳細: {esc(e)}")

    # ── KPI（合計）────────────────────────────────
    total = sum(r["visits"] for r in summary)
    human = sum(r["human"] for r in summary)
    human_ref = sum(r["human"] for r in summary if r["has_ref"])
    human_direct = human - human_ref
    bots = total - human

    # ── サイト × 流入区分 ──────────────────────────
    rows_sum = ""
    for r in summary:
        kind = "紹介リンク経由（?ref= あり）" if r["has_ref"] else "直接・自然流入"
        rows_sum += (
            f'<tr><td>{esc(site_label(r["site"]) or r["site"])}</td>'
            f'<td>{kind}</td>'
            f'<td class="num">{r["human"]}</td>'
            f'<td class="num" style="color:#999">{r["visits"] - r["human"]}</td>'
            f'<td class="num">{r["visits"]}</td></tr>'
        )
    if not rows_sum:
        rows_sum = ('<tr><td colspan="5" style="text-align:center;color:#999;'
                    'padding:20px">この期間の記録はありません</td></tr>')

    # ── 紹介コード別 ────────────────────────────────
    rows_ref = ""
    for r in by_ref:
        last = r["last_at"].strftime("%Y-%m-%d %H:%M") if r.get("last_at") else "—"
        rows_ref += (
            f'<tr><td><code>{esc(r["ref_code"])}</code></td>'
            f'<td>{esc(site_label(r["site"]) or r["site"])}</td>'
            f'<td class="num">{r["human"]}</td>'
            f'<td class="num" style="color:#999">{r["visits"] - r["human"]}</td>'
            f'<td>{last}</td></tr>'
        )
    if not rows_ref:
        rows_ref = ('<tr><td colspan="5" style="text-align:center;color:#999;'
                    'padding:20px">紹介リンク経由の到達はまだありません</td></tr>')

    # ── 日別の推移（直近30日・棒の長さは最大値との比）──────
    peak = max([d["human"] for d in daily] or [0]) or 1
    rows_day = ""
    for d in daily:
        w = int(d["human"] / peak * 220)
        rows_day += (
            f'<tr><td>{d["d"].strftime("%m/%d")}</td>'
            f'<td class="num">{d["human"]}</td>'
            f'<td class="num" style="color:#C55A11">{d["human_ref"]}</td>'
            f'<td class="num" style="color:#999">{d["visits"] - d["human"]}</td>'
            f'<td><span class="bar" style="width:{w}px"></span></td></tr>'
        )
    if not rows_day:
        rows_day = ('<tr><td colspan="5" style="text-align:center;color:#999;'
                    'padding:20px">直近30日の記録はありません</td></tr>')

    def tab(key, label):
        cls = "on" if period == key else "off"
        return f'<a class="tab {cls}" href="/admin/lp-visits?period={key}">{label}</a>'

    tabs = (tab("today", "今日") + tab("7d", "7日") + tab("30d", "30日")
            + tab("this_month", "今月") + tab("all", "全期間"))

    err_html = f'<div class="err">{err}</div>' if err else ""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<title>LP到達の集計 - MOONpicker</title>
<style>{_STYLE}</style></head><body>
<div class="container">

<div class="header">
  <h1>📈 LP到達の集計（広告の効果測定）</h1>
  <a href="/admin">← 管理画面に戻る</a>
</div>

{err_html}

<div class="card">
  <div class="help">
    <b>この画面は「LPに何人来たか」だけを示します。</b>
    そこから先（購入ボタンを押した数）は Polar の
    <b>Settings → Webhooks → Deliveries</b> で <code>checkout.created</code> の件数を数えてください。<br>
    <b>LPの成約率 ＝ checkout.created の件数 ÷ 下の「人間」の数</b> です。
    この比率は媒体ではなくLPの性質のため、広告をまたいで比較できます。
  </div>
  <div style="margin-bottom:14px">{tabs}</div>
  <div class="kpi">
    <div class="big">{human}<span>人間の到達</span></div>
    <div class="big" style="color:#C55A11">{human_ref}<span>うち紹介リンク経由</span></div>
    <div class="big">{human_direct}<span>うち直接・自然流入</span></div>
    <div class="big" style="color:#999">{bots}<span>ボット判定（除外済み）</span></div>
  </div>
  <div class="note">
    「人間」はボット判定を除いた数です。判定は User-Agent による簡易的なもので、
    完全ではありません（除外せずフラグを立てているだけなので、後から数え直せます）。
  </div>
</div>

<div class="card">
  <h2>サイト × 流入区分</h2>
  <table>
    <thead><tr><th>LP</th><th>流入区分</th><th class="num">人間</th>
      <th class="num">ボット</th><th class="num">合計</th></tr></thead>
    <tbody>{rows_sum}</tbody>
  </table>
  <div class="note">
    広告は紹介コード付きのURLで出すため、「紹介リンク経由」がほぼ広告の流入にあたります。
    「直接・自然流入」との差を見れば、広告で増えた分が分かります。
  </div>
</div>

<div class="card">
  <h2>紹介コード別のLP到達</h2>
  <table>
    <thead><tr><th>コード</th><th>LP</th><th class="num">人間</th>
      <th class="num">ボット</th><th>最終到達</th></tr></thead>
    <tbody>{rows_ref}</tbody>
  </table>
  <div class="note">
    <b>紹介リンク管理の「訪問」とは別の数字です。</b>
    あちらは <code>/r/{{code}}</code> を踏んだ回数、ここはLPが実際に表示された回数。
    LinkedIn のようにリダイレクトを解決する媒体では <code>/r/</code> が記録されないため、
    こちらのほうが実態に近いことがあります。両者が食い違っても異常ではありません。
  </div>
</div>

<div class="card">
  <h2>日別の推移（直近30日）</h2>
  <table>
    <thead><tr><th>日付</th><th class="num">人間</th>
      <th class="num">うち紹介経由</th><th class="num">ボット</th><th></th></tr></thead>
    <tbody>{rows_day}</tbody>
  </table>
  <div class="note">
    記録が0件の日は行として出ません。広告の出稿日と山が一致するかの確認に使ってください。
  </div>
</div>

<div class="card">
  <h2>CSV出力</h2>
  <a class="dl" href="/admin/lp-visits/csv?period={esc(period)}">明細（1到達1行）をダウンロード</a>
  <div class="note">最大5,000行まで。新しい順に出力します。</div>
</div>

</div></body></html>""")


def _csv(period: str) -> Response:
    d_from, d_to = _period(period)
    try:
        rows = lp_visit_rows(d_from, d_to)
    except Exception as e:
        log.error("lp visit csv failed: %s", e)
        rows = []

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "site", "ref_code", "is_bot", "visited_at"])
    for r in rows:
        w.writerow([
            r["id"], r["site"], r["ref_code"] or "",
            "1" if r["is_bot"] else "0",
            r["visited_at"].strftime("%Y-%m-%d %H:%M:%S") if r["visited_at"] else "",
        ])
    name = f"lp_visits_{period}_{datetime.now().strftime('%Y%m%d')}.csv"
    # Excel で開いたときに文字化けしないよう BOM を付ける（既存のCSV出力と同じ扱い）。
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
