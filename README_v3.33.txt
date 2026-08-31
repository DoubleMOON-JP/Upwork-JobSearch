MOONpicker v3.33 — 無料トライアル（10回・1か月）の導入
作成 2026-08-30
============================================================

■ 何をするリリースか

Polar の無料トライアルで申し込まれた契約に、専用のプラン（月10回）を
割り当てる。トライアルが終わって本課金へ移行したら、本来のプラン
（Basic 100回 / Pro 300回）へ自動で戻す。

広告向けの期間限定トライアルを想定している。トライアルを配るかどうかは
Polar のチェックアウトリンク側で決まるため、このリリースを入れても
リンクを作らなければ何も起きない（＝安全に先行デプロイできる）。


■ 差し替えるファイル（3本）

  plans.py
  payments.py
  mailer.py

database.py は変更しない（既存の update_license_plan() を呼ぶだけ）。


■ 変更の内容

【plans.py】
 ・"trial" プランを追加（monthly_cap=10 / price_usd=0 /
   label="トライアル（10回）"）。

【payments.py】
 ・TRIAL_PLAN 定数を追加。
 ・PaymentEvent に is_trial フラグを追加。
   Polar の status="trialing" を根拠に立てる。expires_at の有無では
   判定しない（日付の取得に失敗しても、トライアルである事実は変わらない）。
 ・発行時（ACTIVATE）：is_trial なら plan を "trial" に差し替えて発行。
 ・転換時（RENEW）：extend_license の直後、ライセンスの plan が "trial" の
   ときだけ update_license_plan() で本来のプランへ戻す。
 ・_notify_plan_change()：現在の plan が "trial" のときは何もしない。

【mailer.py】
 ・PLAN_TEXT_EN に "trial": ("Trial", 10) を追加。
 ・_plan_text()：trial のときだけ "per month" を落とす
   （毎月10回もらえると誤読されるため）。
 ・_trial_body() を追加し、send_license_key() が plan=="trial" のときに
   件名・本文を丸ごと差し替える。


■ ★ なぜ _notify_plan_change() に手を入れたか（重要）

AUTO_APPLY_PLAN_CHANGE = True のため、subscription.updated が届くと
「商品IDから解決したプラン」と「ライセンスのプラン」を比べて自動で
書き換える処理が走る。

トライアル中のライセンスは plan="trial"、商品は Basic のままなので、
ここでは必ず「trial → 1month」というズレとして見える。
subscription.updated はプラン変更以外でも飛ぶ（解約予約、カード情報の
更新など）ため、放置するとトライアル中に上限が 100 回へ増える。
apply_plan_change() は繰り越しも行うので、その月の実効上限は
10 + 100 = 110 回になる。

エラーは出ず、ログに PLAN CHANGE APPLIED と残るだけなので気づけない。
そのためトライアル中は自動変更を止め、本来のプランへ戻すのは
RENEW（本課金の入金）だけの仕事とした。

AUTO_APPLY_PLAN_CHANGE 自体は True のまま。Basic ⇔ Pro の変更では
正しく動いており、止める理由がない。


■ ★ なぜ update_license_plan() を使ったか（apply_plan_change ではなく）

apply_plan_change() は使い残しの繰り越しを行うため、転換月の上限が
10 + 100 = 110 回になる。$9 を払った月は素直に 100 回であるべきで、
繰り越しは「Basic → Pro に途中で上げた人が損をしない」ための仕組み。
トライアルからの転換に当てはめると、金額の説明がつかない上限になる。


■ 承知のうえで残している挙動

【1】月をまたぐと最大20回になる
  rate_limit.py の集計単位は暦月（period_month）。
  例：9/10 開始 → 9月分10回 ＋ 10月分10回 ＝ 20回。
  厳密に10回にするには集計方法の変更が必要だが、AIコストで約$0.15の
  ため許容する（2026-08-30 Koji 判断）。

【2】メールに課金開始日を書かない
  こちらが持っているのは trial_end を日付単位に丸めた値で、Polar が
  実際に課金する瞬間（時刻・UTC）とは1日ずれ得る。カード決済が失敗
  すると Polar 側だけが最長21日後ろへずれる。
  正確な日付は Polar の確認メールに任せる。

【3】管理画面のプラン選択肢に「トライアル（10回）」が現れる
  /admin・/admin/licenses・/staff のプラン欄は PLANS から生成している
  ため、trial も選べるようになる。
  ・利点：手動でトライアルキーを配れる。
  ・注意：有料顧客のプランを誤って trial に変えると上限が10回に落ちる。
  今回はコードを増やさない方針で、選択肢はそのままにしてある。

【4】同一メールでの複数トライアルは防いでいない
  重複チェックは subscription_id のみ。Polar のトライアルはカード登録が
  必須（2026-08-30 実測）のため、繰り返しの手間に見合わないと判断した。


■ デプロイ後にやること

 1. /health で version と commit を確認する。
 2. Polar のチェックアウトリンクで Free trial period を 30日 に設定する。
    ※ 1か月という期間は Polar 側の設定で決まる。
      10回という上限はコード側で決まる。役割が分かれている。
 3. 広告 No.23（実地確認）
    ・/admin/licenses でプランが「トライアル（10回）」になること
    ・有効期限がトライアル終了日になっていること
    ・Render のログに trial checkout -> issuing with plan=trial が出ること
    ・トライアル用のメールが届くこと
 4. ★転換の確認（別途）
    プランが trial → 1month に戻るかは、トライアルが終わらないと
    確認できない。30日待つのは現実的でないため、1日トライアルの
    リンクで一度通しておくこと。
    期待するログ：trial converted: license=... plan trial -> 1month
    ここが壊れていると、$9 を払った顧客が月10回で止まる。
 5. 広告 No.24（テスト用リンクの削除）


■ 別件・未対応（記録）

 ・LP の「Card and PayPal accepted.」という記載が実際と食い違う。
   2026-08-30 のチェックアウト画面には Card と Cash App Pay しか
   出ていなかった（請求先 Philippines / Japan の両方で確認）。
   landing_upwork.html / landing_freelancer.html の Basic・Pro 両方に
   同じ文言がある。広告で人を集める前に直すこと。
