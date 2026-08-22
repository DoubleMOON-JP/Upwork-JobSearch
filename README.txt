JobSearch ソース一式  v3.29（2026-08-22）
====================================================================

■ このリリースの内容：2件

  【1】貼り付け手順の文言修正（sites.py）
       Upwork の案内に "Best matches" が抜けていた。
       あわせて実物のタブ表記（小文字）と操作（タブを選ぶ）に合わせた。

  【2】LPに og:image を追加（HTML 3ファイル）
       SNSでリンクを貼ったときに、カードへ画像が入るようにする。


■ 変更したファイルと差分（合計5行の追加、2行の削除）

  sites.py                        追加 2 行 / 削除 2 行
  frontend/hub.html               追加 1 行 / 削除 0 行
  frontend/landing_upwork.html    追加 1 行 / 削除 0 行
  frontend/landing_freelancer.html 追加 1 行 / 削除 0 行

  【重要】GitHubでコミットするとき、差分が上記のとおりであることを
  確認してください。それ以外の行が出たら適用しないでください。


■ 【1】sites.py の変更（1箇所）

  SITES["upwork"]["paste_placeholder"] の冒頭2行だけを差し替えた。

  変更前
      "Go to Upwork's job search page (not Saved Jobs or Invites) and sort by "
      "\"Most Recent\". Scroll to the bottom and wait until every listing has "

  変更後
      "Go to Upwork's job search page (not Saved Jobs or Invites) and pick the "
      "\"Best matches\" or \"Most recent\" tab. Scroll to the bottom and wait until every listing has "

  結合後の実値（貼り付け欄に出る文言）

      Go to Upwork's job search page (not Saved Jobs or Invites) and pick the
      "Best matches" or "Most recent" tab. Scroll to the bottom and wait until
      every listing has loaded — Upwork loads them a few at a time, and anything
      not yet on screen will not be copied. Then press Ctrl/⌘+A to select the
      page and Ctrl/⌘+C to copy. Paste it here — multiple pages are fine,
      noise is ignored.

  ● なぜ直したか

    1. "Best matches" が抜けていた。実際には Best matches / Most recent の
       どちらでも使える。案内が片方しか示していなかった。

    2. 表記が実物と違っていた。Upworkの実物は "Most recent"（小文字r）。
       文言は "Most Recent"（大文字R）だった。

    3. 「sort by（並べ替え）」ではなく、実際は「タブを選ぶ」操作。
       直前の (not Saved Jobs or Invites) も同じタブ列を指しているので、
       「タブを選ぶ」と書いたほうが文全体として筋が通る。

  ● 変えていないもの

    ・paste_tip（貼り付け欄の下の補足）… ソート順の記述が無いため変更不要
    ・prompt_noise_hint / trim_start_after / trim_end_before / multi_currency
    ・csv_filename / prompt_source_label / label / paste_heading / enabled
    ・freelancer の定義すべて
    ・4つの関数（is_valid_site / get_site / site_label / enabled_sites）

  ● 検証済みの内容

    ・assert old in s and s.count(old)==1 のガードを通した
    ・ast.parse() で構文エラーなし
    ・新旧の SITES を実際に実行して機械的に比較し、
      upwork.paste_placeholder 以外の全キーが2サイトとも一致することを確認
    ・行数は 139 行のまま変わっていない


■ 【2】og:image の追加（HTML 3ファイル・各1行）

  各ファイルの <head> の og:url の直後に、次の1行を追加した。

      <meta property="og:image" content="https://jobsearch.doublemoon.biz/static/jobsearch_demo_poster.jpg" />

  3ファイルとも同じURL。画像は既に /static/ で配信されているので、
  ファイルの追加は不要。

  ● なぜ必要か

    og:image が無いと、SNSでリンクを貼ったときにカードが画像なしになる。
    LinkedIn の Featured セクションで実際に灰色のカードになることを確認した。
    素材は既存のデモ動画のポスター画像（86KB）をそのまま使う。

  ● 変えていないもの

    3ファイルとも、追加した1行以外は1文字も変更していない。
    CSS・本文・価格・FAQ・フッター・紹介コードのスクリプトはすべて元のまま。


■ 同梱していないファイル（この期間に変更していない）

  main.py / evaluate.py / database.py / db_redesign.py / rate_limit.py /
  payments.py / plans.py / mailer.py / admin_*.py / staff_console.py /
  settings_admin.py / requirements.txt / runtime.txt /
  frontend/index.html / frontend/static/


■ 【要対応】main.py の APP_VERSION

  main.py は同梱していないため、APP_VERSION は 3.28.0 のままになる。
  リリースのたびに更新する運用なので、必要なら main.py の1行を

      APP_VERSION = "3.29.0"

  に手で更新すること。忘れても /health の commit は変わるため、
  デプロイの反映判定には影響しない。


■ デプロイ後の確認

  1. /health の commit が変わったことを確認する

  2. 文言の反映確認
     /app/upwork を開き、貼り付け欄のプレースホルダーに
     "Best matches" と "Most recent" が出ていること

  3. og:image の反映確認
     SNSにリンクを貼ってカードに画像が出ることを確認する。
     ※ LinkedIn や X はカードをキャッシュするため、
       既に貼ったリンクはすぐには変わらない。新しく貼り直すこと。


■ 未対応（同じ箇所を次に触るときの候補）

  ・twitter:card の追加
      <meta name="twitter:card" content="summary_large_image" />
    これが無いと、X ではカードが小さい正方形になる。
    入れると横長の大きいカードになる。各ファイル1行。

  ・"Saved Jobs" → "Saved jobs" の表記統一
    Upworkの実物は小文字のj。sites.py の paste_placeholder と
    paste_tip の2箇所。

  ・会社名の英語表記の統一
    現在3種類が混在している。
      campaign.html      : DoubleMoonTrading Co.（有限会社ダブルムーントレーディング）
      landing_*.html     : DoubleMoon Trading Co., Ltd.
      LinkedIn（設定済み）: DoubleMOON Co.

  ・/campaign から Reddit のタグを削除
    Reddit (r/Upwork, r/freelance) をシェア先候補として表示しているが、
    r/Upwork はツールの宣伝を規約で禁止しており、
    従った顧客がアカウント停止になりうる。
