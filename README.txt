moonpicker_v3_30_20260823.zip
============================================================
製品名変更リリース v3.30.0（JobSearch → MOONpicker）
作成日: 2026-08-23
============================================================

■ このリリースでやったこと（1つだけ）
  サイト内の表示文字列 "JobSearch" を "MOONpicker" に置換した。
  46か所。大文字小文字を区別した完全一致置換のみ。
  それ以外の変更は main.py の APP_VERSION 1行のみ。

■ 変更内容
  1) "JobSearch" → "MOONpicker"  ... 46か所 / 14ファイル
  2) APP_VERSION "3.28.0" → "3.30.0"  ... main.py 1か所

  ※ 3.29.0 ではなく 3.30.0 にした理由:
     v3.29（sites.py + og:image）は main.py を同梱しなかったため
     APP_VERSION が 3.28.0 のまま本番に出ている。
     今回は v3.29 の後の新しいリリースなので 3.30.0 とし、
     欠番を作らずに版数を進める。
     デプロイ後 /health の version が 3.30.0 になれば反映確認できる。

■ 同梱ファイル（14）
  main.py                        5か所 + APP_VERSION
  mailer.py                      8か所  ★顧客のメールに出る
  admin_home.py                  2か所
  admin_licenses.py              1か所
  admin_referrals.py             1か所
  settings_admin.py              1か所
  staff_console.py               2か所
  frontend/index.html            3か所
  frontend/hub.html              4か所
  frontend/campaign.html         5か所
  frontend/landing_upwork.html   4か所
  frontend/landing_freelancer.html 4か所
  frontend/privacy.html          4か所
  frontend/thanks.html           2か所

■ 同梱していないファイル（変更なし・アップ不要）
  sites.py, plans.py, database.py, db_redesign.py, payments.py,
  evaluate.py, rate_limit.py, admin_prompts.py, admin_ui.py,
  requirements.txt, runtime.txt, .gitignore
  → いずれも "JobSearch"（大文字小文字一致）を含まないため。

■ 変更していないもの（意図的に残した）
  ・ドメイン  jobsearch.doublemoon.biz        12か所
    → 2026-09-06 の Polar 課金テスト完了後に moonpicker.com へ移行する。
      先に変えると決済・ライセンス配信が壊れる。
  ・メールアドレス
      jobsearch_support@doublemoon.biz  17か所
      js_license@doublemoon.biz          3か所
      js_campaign@doublemoon.biz         3か所
    → メールボックスの実体があるため、ドメイン移行と同時に扱う。
  ・ブラウザ保存キー（変えると既存利用者のライセンスが消える）
      ujs_license / ujs_profile / ujs_privacy_agreed / js_ref
  ・ライセンスキー接頭辞 DMJS（database.py）
    → 発行済みキーが無効になるため恒久的に変更しない。
  ・画像/動画ファイル名 jobsearch_demo*（9か所）
    → 動画の再作成時にまとめて扱う。
  ・upwork.doublemoon.biz（旧ドメイン・引継ぎメモ §9 で変更禁止）

■ 検証（すべて実行済み・結果は下記のとおり）
  1) 置換件数            46件（事前カウントと一致）
  2) 残存 "JobSearch"    0件
  3) "MOONpicker" 出現   46件
  4) 行数                全23ファイルで置換前後 完全一致（増減なし）
  5) バイト数            +46（9文字→10文字 × 46か所と一致）
  6) ast.parse()         .py 16ファイル 全て PASS
  7) node --check        HTML内 <script> 4ブロック（index / landing_upwork /
                         landing_freelancer / thanks）全て PASS
                         ※ campaign / hub / privacy にスクリプトは無い
  8) 不変文字列の出現数（置換前 → 置換後、全て一致）
       ujs_privacy_agreed                2 → 2
       ujs_license                       4 → 4
       ujs_profile                       2 → 2
       js_ref                           17 → 17
       DMJS                              4 → 4
       jobsearch.doublemoon.biz         12 → 12
       jobsearch_support@doublemoon.biz 17 → 17
       js_license@doublemoon.biz         3 → 3
       js_campaign@doublemoon.biz        3 → 3
       jobsearch_demo                    9 → 9
       upwork.doublemoon.biz             3 → 3
       Double Moon Job Search            1 → 1
  9) サーバ側プレースホルダ（index.html）
       {{SITE_ID}} {{SITE_LABEL}} {{CSV_FILENAME}}
       {{PASTE_HEADING}} {{PASTE_PLACEHOLDER}} {{PASTE_TIP}}
       いずれも 1 → 1 で無傷
 10) CSV の BOM 指定 "﻿"（index.html）  1 → 1 で無傷

■ Render の環境変数について（コード変更の効き方）
  MAIL_FROM_NAME   … 未設定 → mailer.py の既定値が生きている。
                      よって今回のコード変更で差出人名が
                      "JobSearch" → "MOONpicker" に実際に変わる。
  SUPPORT_EMAIL    … 未設定 → 既定値 jobsearch_support@ が生きている（今回は据置）
  MAIL_FROM        … 未設定 → SMTP_USER（js_license@）にフォールバック（今回は据置）
  BASE_URL         … 設定済み https://jobsearch.doublemoon.biz
                      → 今回は触らない。2026-09-06 以降に変更する。

■ デプロイ後の確認手順
  1) /health の version が "3.30.0"、service が "MOONpicker API"
  2) /            … hub のロゴが MOONpicker
  3) /for/upwork  … タイトルとヘッダーが MOONpicker
  4) /app/upwork  … ヘッダーが MOONpicker、貼り付け→採点が通ること
                    （ライセンスが残っていること＝保存キー無傷の確認）
  5) /privacy     … Service 欄が MOONpicker
  6) /admin, /staff … タイトルとヘッダーが MOONpicker

■ 次にやること（このZIPの範囲外）
  ・Polar の商品名
  ・X のアカウント名／ハンドル
  ・LinkedIn の About / Featured
  ・デモ動画内の文字
  ・ドメイン移行（2026-09-06 以降）
  ・メールアドレスの moonpicker.com 化（ドメイン移行と同時）
