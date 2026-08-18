# admin_ui.py
# ─────────────────────────────────────────────────────────────
# 管理画面まわりの共通部品。画面を独立ファイルへ切り出すための土台。
#
# 【ここに置くもの】どの画面からも使う、外部に依存しない部品だけ。
#   UI_TEXT        画面文言の日英辞書
#   ui_text()      ログインしている人に合わせて辞書を返す
#   plan_label_ui() プラン名を画面に出す形にする
#   esc()          HTMLエスケープ
#
# 【ここに置かないもの】認証（verify_admin / verify_any）は main.py に残す。
# 環境変数とアプリ本体に依存するため。各画面モジュールへは引数で渡す。
#
# このファイルは他のモジュールを一切importしない。
# そのため、どこからimportしても循環参照が起きない。
# ─────────────────────────────────────────────────────────────

# ── 管理画面の表示文言（日本語／英語）────────────────────
# スタッフは日本語話者とは限らないため、スタッフとしてログインした場合だけ
# 英語で表示する。画面そのものは複製せず1枚のまま言語を差し替える。
# （同じ機能の画面を2枚持つと、以後の修正が常に2箇所になるため）
#
# ここに入れているのは「画面に書かれている文字」だけ。
# サーバーが返すエラー文（database.py などの戻り値）は日本語のままなので、
# 異常時のメッセージは日本語で出る。まず画面文言だけを対象とする判断。
UI_TEXT = {
    "ja": {
        "role_admin": "システム管理者", "role_staff": "スタッフ",
        # ライセンス一覧
        "lic_title": "ライセンス一覧", "lic_h1": "📋 ライセンス一覧",
        "back_admin": "← 管理画面に戻る", "back_staff": "← スタッフ画面",
        "link_referrals": "紹介リンク管理", "link_licenses": "ライセンス一覧",
        "st_total": "総数", "st_active": "有効",
        "st_expired": "期限切れ", "st_inactive": "無効化",
        "badge_inactive": "無効化", "badge_expired": "期限切れ", "badge_active": "有効",
        "kind_paid": "決済", "kind_manual": "手動",
        "mail_sent": "送信済", "mail_failed": "未送信", "mail_manual": "手動発行",
        "plan_legacy": "（旧）",
        "btn_extend": "+1ヶ月", "btn_plan": "プラン変更",
        "btn_resend": "キー再送", "btn_delete": "削除",
        "empty_licenses": "該当するライセンスはありません",
        "pg_prev": "← 前へ", "pg_next": "次へ →",
        "search_h2": "🔍 検索・絞り込み", "kw_label": "キーワード",
        "kw_ph": "ライセンスキー / メール", "status_label": "状態",
        "opt_all": "すべて", "btn_search": "検索", "link_clear": "クリア",
        "list_h2": "📋 一覧",
        "th_id": "ID", "th_key": "ライセンスキー", "th_email": "メール",
        "th_plan": "プラン", "th_kind": "種別", "th_mail": "キー送付",
        "th_status": "状態", "th_expires": "有効期限", "th_ops": "操作",
        "bulk_h2": "🗑 期限切れライセンスの一括削除",
        "bulk_warn_1": "<b>削除すると元に戻せません。</b>実行前に",
        "bulk_warn_link": "CSVバックアップ",
        "bulk_warn_2": "を取得してください。",
        "bulk_warn_3": ("決済に紐づくライセンスは、失効から30日経過するまで削除されません"
                        "（支払いリトライ中に削除すると、更新時に別のキーが発行されてしまうため）。"
                        "条件を満たさないものは自動的にスキップされます。"),
        "bulk_since": "失効から", "btn_bulk_delete": "まとめて削除",
        # ライセンス一覧のJS
        "js_resend_confirm": "このライセンスキーをメールで送信しますか？",
        "js_resend_to": "宛先: ", "js_sent": "送信しました: ",
        "js_error": "エラー: ", "js_unknown": "不明なエラー",
        "js_extend_confirm": " を1ヶ月延長しますか？",
        "js_extended": "延長完了。新しい有効期限: ",
        "js_plan_confirm_1": " のプランを「", "js_plan_confirm_2": "」に変更しますか？",
        "js_plan_note": "・有効期限は変わりません\\n・上位プランへの変更時は、旧プランの残り回数を繰り越します",
        "js_plan_done": "プランを変更しました：", "js_plan_keep": "有効期限は変更していません。",
        "js_plan_carry_1": "旧プランの残り ", "js_plan_carry_2": " 回を繰り越しました。",
        "js_plan_left_1": "今月の残り回数：", "js_plan_left_2": " 回（使用済み ",
        "js_plan_left_3": " 回）",
        "js_del_confirm": "このライセンスを削除しますか？",
        "js_del_warn": "削除すると元に戻せません。",
        "js_deleted": "削除しました：", "js_del_failed": "削除できませんでした",
        "js_bulk_1": "失効から", "js_bulk_2": "日以上経過したライセンスをまとめて削除します。",
        "js_bulk_3": "元に戻せません。CSVバックアップは取得済みですか？",
        "js_bulk_final": "本当に実行しますか？（最終確認）",
        "js_deleting": "削除中...", "js_bulk_done_1": " 件を削除しました。",
        "js_bulk_skip_1": "（条件を満たさない ", "js_bulk_skip_2": " 件はスキップ）",
        "js_generic_error": "エラーが発生しました",
        # 紹介リンク管理
        "ref_title": "紹介リンク管理", "ref_h1": "🔗 紹介リンク管理",
        "back_admin_top": "← 管理トップ",
        "ref_reg_h2": "コードを登録",
        "ref_owner_blank": "担当者を選択",
        "ref_site_blank": "着地先を選択",
        "rth_site": "着地先",
        "js_ref_need_site": "着地先を選択してください",
        "ref_no_staff": "担当者マスタに有効な担当者がいないため登録できません。"
                        "管理画面の「担当者マスタ」から登録してください。",
        "js_ref_need_owner": "担当者を選択してください",
        "ref_opt_channel": "種別", "ref_ph_note": "メモ（任意）",
        "ref_btn_register": "登録",
        "ref_hint_1": "付け方：<code>担当者-種別-年月日+英字</code>（例 <code>{sample}</code>）。"
                      " <b>投稿1本につき1コード</b>を作ってください（使い回すと、どの投稿が効いたか分けられません）。<br>"
                      "日付は<b>年月日の6桁</b>。年を入れないと翌年の同じ日付と重複します。"
                      " 末尾は<b>必ず英字1文字</b>で、1本目から <code>a</code> を付けます。"
                      "同じ日に同じSNSへ複数回投稿する場合は <code>b</code> <code>c</code> と続けます。<br>"
                      " 外部のインフルエンサーは <code>infl-tanaka</code> のように日付なしにすると使い回せます。<br>"
                      "<b>着地先</b>は、そのリンクを踏んだ人が見るLPです。投稿で紹介する求人サイトに合わせてください。",
        "ref_hint_2": "登録すると <code>{url}</code> が使えるようになります。",
        "ref_stats_h2": "成績",
        "tab_all": "全期間", "tab_this_month": "今月",
        "tab_last_month": "先月", "tab_30d": "過去30日",
        "rth_code": "コード", "rth_channel": "種別", "rth_owner": "担当者",
        "rth_visits": "訪問", "rth_purchases": "購入", "rth_cvr": "転換率",
        "rth_active": "継続中", "rth_mrr": "MRR", "rth_state": "状態",
        "rth_note": "メモ", "rth_ops": "操作",
        "ref_state_active": "有効", "ref_state_stopped": "停止中",
        "ref_btn_stop": "停止", "ref_btn_resume": "再開", "ref_btn_copy": "URLコピー",
        "ref_empty": "紹介コードがまだ登録されていません",
        "ref_note_1": "<b>訪問・購入</b>は選択した期間内の件数。<b>継続中・MRR</b>は期間に関係なく「現時点」の値です。",
        "ref_note_2": "ロボットと判定したアクセスは訪問数から除いています（括弧内が除外数）。",
        "ref_note_3": "スマホで踏んでPCで購入した場合などは追跡できないため、実際の貢献はこの数字より多くなります。"
                      " 傾向の比較には使えますが、絶対値として信用しすぎないでください。",
        "ref_csv_h2": "CSVダウンロード",
        "ref_csv_sum": "集計CSV（この期間）", "ref_csv_detail": "明細CSV（ライセンス1件ごと）",
        "js_ref_need_code": "コードを入力してください",
        "js_ref_toggle_failed": "変更できませんでした",
        "js_ref_copied": "コピーしました: ",
    },
    "en": {
        "role_admin": "Administrator", "role_staff": "Staff",
        "lic_title": "License list", "lic_h1": "📋 License list",
        "back_admin": "← Back to admin", "back_staff": "← Staff Console",
        "link_referrals": "Referral links", "link_licenses": "License list",
        "st_total": "Total", "st_active": "Active",
        "st_expired": "Expired", "st_inactive": "Deactivated",
        "badge_inactive": "Deactivated", "badge_expired": "Expired", "badge_active": "Active",
        "kind_paid": "Paid", "kind_manual": "Manual",
        "mail_sent": "Sent", "mail_failed": "Not sent", "mail_manual": "Issued manually",
        "plan_legacy": " (legacy)",
        "btn_extend": "+1 month", "btn_plan": "Change plan",
        "btn_resend": "Resend key", "btn_delete": "Delete",
        "empty_licenses": "No licenses match your search",
        "pg_prev": "← Prev", "pg_next": "Next →",
        "search_h2": "🔍 Search", "kw_label": "Keyword",
        "kw_ph": "license key / email", "status_label": "Status",
        "opt_all": "All", "btn_search": "Search", "link_clear": "Clear",
        "list_h2": "📋 Licenses",
        "th_id": "ID", "th_key": "License key", "th_email": "Email",
        "th_plan": "Plan", "th_kind": "Source", "th_mail": "Key delivery",
        "th_status": "Status", "th_expires": "Expires", "th_ops": "Actions",
        "bulk_h2": "🗑 Bulk delete expired licenses",
        "bulk_warn_1": "<b>This cannot be undone.</b> Before you run it, download a",
        "bulk_warn_link": "CSV backup",
        "bulk_warn_2": ".",
        "bulk_warn_3": ("Licenses tied to a payment are kept for 30 days after they expire "
                        "(deleting one mid-retry would issue a different key on renewal). "
                        "Anything that doesn't qualify is skipped automatically."),
        "bulk_since": "Expired for", "btn_bulk_delete": "Delete them",
        "js_resend_confirm": "Email this license key?",
        "js_resend_to": "To: ", "js_sent": "Sent to: ",
        "js_error": "Error: ", "js_unknown": "Unknown error",
        "js_extend_confirm": " — extend by one month?",
        "js_extended": "Extended. New expiry date: ",
        "js_plan_confirm_1": " — change the plan to ", "js_plan_confirm_2": "?",
        "js_plan_note": "- The expiry date stays the same\\n- Unused evaluations carry over when moving to a higher plan",
        "js_plan_done": "Plan changed: ", "js_plan_keep": "The expiry date was not changed.",
        "js_plan_carry_1": "Carried over ", "js_plan_carry_2": " evaluations from the old plan.",
        "js_plan_left_1": "Remaining this month: ", "js_plan_left_2": " (used ",
        "js_plan_left_3": ")",
        "js_del_confirm": "Delete this license?",
        "js_del_warn": "This cannot be undone.",
        "js_deleted": "Deleted: ", "js_del_failed": "Could not delete",
        "js_bulk_1": "Delete every license that expired more than ", "js_bulk_2": " days ago.",
        "js_bulk_3": "This cannot be undone. Have you downloaded the CSV backup?",
        "js_bulk_final": "Run it now? (final confirmation)",
        "js_deleting": "Deleting...", "js_bulk_done_1": " deleted.",
        "js_bulk_skip_1": " (", "js_bulk_skip_2": " skipped — they did not qualify)",
        "js_generic_error": "Something went wrong",
        "ref_title": "Referral links", "ref_h1": "🔗 Referral links",
        "back_admin_top": "← Admin home",
        "ref_reg_h2": "Add a code",
        "ref_owner_blank": "Select owner",
        "ref_site_blank": "Select landing page",
        "rth_site": "Landing",
        "js_ref_need_site": "Please select a landing page.",
        "ref_no_staff": "No active owners are registered yet, so codes cannot be added. "
                        "Ask the administrator to add one.",
        "js_ref_need_owner": "Please select an owner.",
        "ref_opt_channel": "Channel", "ref_ph_note": "note (optional)",
        "ref_btn_register": "Add",
        "ref_hint_1": "Format: <code>owner-channel-date+letter</code> (e.g. <code>{sample}</code>)."
                      " <b>Create one code per post</b> — reusing a code makes it impossible to tell which post worked.<br>"
                      "Use a <b>six-digit date</b>; without the year, next year's dates would clash with this year's."
                      " Always end with <b>a letter</b> — start with <code>a</code> on the first post, then"
                      " <code>b</code>, <code>c</code> for further posts to the same platform on the same day.<br>"
                      " For outside influencers, drop the date — <code>infl-tanaka</code> — so the code can be reused.<br>"
                      "<b>Landing</b> is the page people see when they follow your link."
                      " Match it to the job board you are posting about.",
        "ref_hint_2": "Once added, <code>{url}</code> becomes available.",
        "ref_stats_h2": "Results",
        "tab_all": "All time", "tab_this_month": "This month",
        "tab_last_month": "Last month", "tab_30d": "Last 30 days",
        "rth_code": "Code", "rth_channel": "Channel", "rth_owner": "Owner",
        "rth_visits": "Visits", "rth_purchases": "Purchases", "rth_cvr": "Conv.",
        "rth_active": "Still active", "rth_mrr": "MRR", "rth_state": "State",
        "rth_note": "Note", "rth_ops": "Actions",
        "ref_state_active": "Active", "ref_state_stopped": "Stopped",
        "ref_btn_stop": "Stop", "ref_btn_resume": "Resume", "ref_btn_copy": "Copy URL",
        "ref_empty": "No referral codes yet",
        "ref_note_1": "<b>Visits and purchases</b> cover the period you picked."
                      " <b>Still active and MRR</b> are current figures, whatever period is selected.",
        "ref_note_2": "Traffic identified as bots is excluded from the visit count (the number in brackets).",
        "ref_note_3": "A visit on a phone followed by a purchase on a laptop can't be tracked, so the real"
                      " contribution is higher than what you see. Use these numbers to compare trends,"
                      " not as exact totals.",
        "ref_csv_h2": "Download CSV",
        "ref_csv_sum": "Summary (selected period)", "ref_csv_detail": "Detail (one row per license)",
        "js_ref_need_code": "Please enter a code.",
        "js_ref_toggle_failed": "Could not change it.",
        "js_ref_copied": "Copied: ",
    },
}


def ui_text(who: dict) -> dict:
    """ログインしている人に合わせて画面文言の辞書を返す。"""
    return UI_TEXT["ja"] if who.get("role") == "admin" else UI_TEXT["en"]


def plan_label_ui(info: dict, en: bool) -> str:
    """プラン名を画面に出す形にする。英語では PLANS の label（日本語）から
    名称部分だけを取り出し、回数を英語で添える。プランが増えても
    ここを直さずに済むよう、定義から組み立てる。"""
    if not en:
        return info["label"]
    return f'{info["label"].split("（")[0]} ({info["monthly_cap"]}/month)'


def esc(v) -> str:
    """管理画面HTMLへ値を埋め込む際のエスケープ（属性値・テキスト共用）。"""
    return (
        str("" if v is None else v)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )

