JobSearch ソース一式  v3.21（2026-08-18 時点）
====================================================================

このZIPには、本番にデプロイ済みのファイルがそのまま入っています。
展開してGitHubへ上げれば、現在の本番と同じ状態になります。
（ファイル名は本番と同じです。リネームは不要です）


■ 同梱ファイル

  main.py             FastAPIアプリ本体・管理画面
  evaluate.py         サーバー側 Gemini 採点（POST /evaluate）
  sites.py            対応求人サイト定義
  settings_admin.py   AI設定（為替レート）の管理画面   ← v3.20 で新規
  requirements.txt    依存パッケージ

  ※ 同梱していないファイル（この期間に変更していないもの）
     database.py / db_redesign.py / rate_limit.py / payments.py /
     plans.py / mailer.py / runtime.txt / frontend/ 配下


■ v3.19 からの変更点

  1. 為替レートをプロンプトからDBへ移設（v3.20）
     ・レートの数値は ai_settings の exchange_rates に一本化
     ・換算が要るサイトかは sites.py の multi_currency で判定
     ・管理画面 /admin/settings を新設（settings_admin.py）
     ・値の向きは「1 USD = N 通貨」

  2. /health にデプロイ版数と実コミットを表示（v3.21）
     ・APP_VERSION（宣言値）と GIT_COMMIT（RENDER_GIT_COMMIT）に分離
     ・デプロイが反映されたかを /health だけで判定できる

  3. polar-sdk のバージョンを 0.9.3 に固定（v3.21）
     ・9月6日の初回更新課金テストの直前に変動させないため


■ リリースのたびに更新が必要な箇所

  main.py の APP_VERSION（1行のみ）。
  更新を忘れても commit は必ず変わるため、致命的ではありません。


■ 注意

  ・evaluate.py は全サイト共通です。触ったらUpworkの回帰テストを行うこと。
  ・main.py のHTMLはf-stringです。波括弧は {{ }} にすること。
  ・polar-sdk は 2026-09-06 の更新課金テストが終わるまで上げないこと。
