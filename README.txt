moonpicker_v3_32_20260827.zip
============================================================
無料トライアルの有効期限を正しく発行する（広告準備タスク No.2）  v3.32.0
作成日: 2026-08-27
============================================================

■ このリリースでやったこと（1つだけ）
  無料トライアル付きで申し込まれたとき、ライセンスの有効期限を
  「トライアル終了日」にする。従来はプランの月数（1か月）で発行していた。

■ なぜ必要か（2026-08-25〜27 の実測）
  1日トライアルのチェックアウトリンクを作って2件購入したところ、
  どちらも有効期限が +31日 で発行された。

    契約1  8/25 申込（1日トライアル） → 有効期限 2026-09-25
    契約2  8/25 申込（1日トライアル） → 有効期限 2026-09-25

  原因は create_license() がプラン名しか見ておらず、トライアルの
  情報がコードに一切入ってきていなかったこと。

  さらに 8/26 のトライアル終了時、order.paid が
  billing_reason="subscription_cycle" で届き extend_license() が走った。
  その基点が 9/25 だったため、期限は 10/25 になった。

    → $9 を1回払って 8/25〜10/25 の約2か月ぶんのアクセス。

  7日トライアルで広告を出すと、7日で解約した人にも
  約1か月ぶんの期限が入ったライセンスが残ることになる。
  （実際にはトライアル解約時に subscription.revoked が届いて
    無効化されるため即座の実害はない。8/26 に実測で確認済み。
    ただし「期限が正しくない」状態は残り、返金・past_due・
    Webhook 欠落など revoked が来ない経路では穴になる。）

■ 変更内容（3ファイル・全10か所）

  main.py（1か所）
    APP_VERSION "3.31.0" -> "3.32.0"

  database.py（1か所・create_license のみ）
    引数に expires_at を追加した。既定は None。
      ・None のとき  … 従来どおり date.today() + plan_months(plan) か月
      ・日付のとき   … その日付をそのまま有効期限にする（当日を含む）
      ・過去日のとき … 今日まで引き上げる
    ★既定値があるため、既存の呼び出しは1行も変えずにそのまま動く。
      admin_licenses.py は create_license(email=..., plan=..., note=...) と
      キーワード引数で呼んでいるので影響なし。

  payments.py（8か所）
    1) from datetime import date, datetime を追加
    2) PaymentEvent に expires_at フィールドを追加（既定 None）
    3) _parse_utc_date() を追加
         "2026-08-26T01:44:05.647917Z" -> date(2026, 8, 26)
         解釈できなければ None を返し、従来の計算に落ちる
    4) verify_and_parse の冒頭で expires_at = None を初期化
    5) ACTIVATE の分岐で status == "trialing" のとき
         trial_end（無ければ current_period_end）を拾う
    6) PaymentEvent に expires_at を渡す
    7) create_license(..., expires_at=ev.expires_at) で発行
    8) 発行ログに expires と TRIAL を出す（デプロイ後の確認用）

■ 触っていないもの（意図的）
  ・extend_license()          … base = max(current, today) は元から正しい。
                                入口が直れば結果も自動的に正しくなる。
  ・ORDER_PAID_EVENTS の条件  … billing_reason=="subscription_cycle" のままでよい。
                                8/26 の実測で、トライアル終了時の課金が
                                この値で届くことを確認済み。
  ・購読イベントの追加        … subscription.active / subscription.cycled は
                                購読しなくてよい。order.paid で足りる。
  ・トライアル日数の設定      … コードではなく Polar のチェックアウトリンク側。
                                商品には設定しない（商品IDが変わらないため
                                PRODUCT_TO_PLAN の突合が壊れない）。

■ Polar の実測データ（この修正の根拠）
  subscription.created（2026-08-25 01:44 UTC）
    "status": "trialing"
    "trial_start":         "2026-08-25T01:44:20.471279Z"
    "trial_end":           "2026-08-26T01:44:05.647917Z"
    "current_period_end":  "2026-08-26T01:44:05.647917Z"   ← trial_end と同値
    "product.trial_interval": null                          ← 商品側は未設定

  order.paid（2026-08-26 01:44 UTC）
    "billing_reason": "subscription_cycle"
    "total_amount": 900
    "subscription.status": "active"
    "subscription.current_period_end": "2026-09-26T01:44:05.647917Z"

■ 有効期限は「当日を含む」
  database.py の validate_license は if today > expires: で失効判定している。
  get_license_stats / search_licenses も expires_at >= CURRENT_DATE。
  よって trial_end の日付をそのまま入れれば、トライアル最終日いっぱいまで
  使える。厳密には Polar のトライアル終了時刻からその日の23:59まで
  最大22時間の余剰が出るが、その入口で必ず
  「課金成功→延長」か「解約済み→revoked で無効化」のどちらかが起きるため
  実害はない。カード失敗の場合はPolarがリトライ中であり、猶予として妥当。

■ 検証（すべて実行済み）
  1) 置換     すべて assert old in s and s.count(old)==1 で1か所一致を保証
  2) ast.parse   main.py / database.py / payments.py  3ファイル PASS
  3) 行数     main 545->545 ／ database 707->721 ／ payments 456->505
  4) 不変文字列の出現数   DMJS / LICENSE_KEY_PREFIX / extend_license /
              STRICT_PRODUCT_MAPPING / subscription_cycle / ACTIVATE_EVENTS /
              ORDER_PAID_EVENTS / REVOKE_EVENTS / jobsearch.doublemoon.biz /
              validate_license / _can_delete   すべて増減なし
              （plan_months のみ 2->3。docstring 内の言及が1件増えたため）
  5) 動作テスト  実際のコードを読み込み、実測した Polar の生JSONを流した。
                 21項目 すべて PASS。

     ・_parse_utc_date … 実測形式 / オフセット表記 / 日付のみ / None /
                         空文字 / 壊れた値
     ・トライアルの subscription.created → expires_at = 2026-08-26
     ・通常購入（status=active）        → expires_at = None（従来どおり）
     ・trial_end 欠落                   → current_period_end で代替
     ・両方欠落                         → None（従来の計算に落ちる）
     ・order.paid subscription_create   → 無視（$0のトライアル請求）
     ・order.paid subscription_cycle    → RENEW
     ・create_license 引数なし          → 今日+1か月（従来と同じ）
     ・create_license 7日指定           → 今日+7日
     ・create_license 過去日            → 今日
     ・7日トライアルの通し
         発行  → 今日+7日
         課金  → (今日+7日)+1か月   ※Polar の current_period_end と一致

■ 同梱していないファイル（変更なし）
  sites.py / plans.py / db_redesign.py / evaluate.py / rate_limit.py /
  mailer.py / admin_*.py / settings_admin.py / staff_console.py /
  requirements.txt / runtime.txt / frontend/ 一式

■ デプロイ後の確認手順
  1) /health の version が "3.32.0" になっている
  2) 既存の動作が壊れていないこと
     ・/admin/licenses が開く
     ・/app/upwork でサインイン済みのライセンスがそのまま使える
  3) トライアルの実地確認（任意・$0）
     Polar でテスト用チェックアウトリンクを作り
     （Free trial period = 7 Days、商品は既存の Basic を選ぶ）、
     別のメールアドレスで申し込む。
       ・/admin/licenses の有効期限が「今日+7日」になっていること
         （+31日 になっていたら修正が効いていない）
       ・Render のログに
           trial subscription <id> -> licence expires 2026-XX-XX
           license issued DMJS-... (ref=-, expires=2026-XX-XX TRIAL)
         の2行が出ていること
     確認後、契約を解約（End of current period）してリンクを削除する。
  4) 通常購入が壊れていないことの確認
     9/6 に更新課金が走る既存契約（ai@moon.am / 8/6 申込）で、
     有効期限が 9/6 -> 10/6 に延びること。トライアルを経ていない
     契約が従来どおり延長されるかの回帰確認になる。

■ 次にやること（このZIPの範囲外）
  ・トライアル日数の決定（7日を想定）と、広告用チェックアウトリンクの作成
  ・payments.py のコメント修正（動作には影響しない・未実施）
      112行目/209行目 … 「自動反映しない」と書いてあるが
                         AUTO_APPLY_PLAN_CHANGE = True なので実際は自動反映する
      408行目         … 「checkout_id は order.paid にも含まれる」と書いてあるが、
                         更新時の order.paid では checkout_id が null だった（8/26 実測）
  ・subscription.past_due の購読追加の検討
      現在は未購読。トライアル終了時のカード失敗に気づくのが遅れる。
  ・order.refunded の扱いの見直し
      一部返金でも order.refunded は飛ぶ。現在のコードは金額を見ずに
      ライセンスを無効化するため、$3 だけ返金しても全体が止まる。
