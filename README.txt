moonpicker_v3_31_20260824.zip
============================================================
紹介動画の差し替え（対応予定 No.29）  v3.31.0
作成日: 2026-08-24
============================================================

■ このリリースでやったこと
  1) 紹介動画を新しいものに差し替えた
  2) ファイル名を jobsearch_demo* → moonpicker_demo* に改名した
  3) 動画に合わせてLPの文言を2箇所直した
  4) APP_VERSION を 3.31.0 に更新した

■ 同梱ファイル（6）
  main.py                             ← APP_VERSION の1行だけ変更
  frontend/hub.html                   ← og:image 1か所
  frontend/landing_upwork.html        ← 5か所
  frontend/landing_freelancer.html    ← 5か所
  frontend/static/moonpicker_demo.mp4         ← 新規（2.93MB）
  frontend/static/moonpicker_demo_poster.jpg  ← 新規（77KB）

■ 動画の仕様（実測）
                        変換前            変換後
  長さ                 31.73秒           31.77秒
  解像度               1920x1080         1280x720
  フレームレート        60fps             30fps
  音声トラック          あり（無音）      なし
  サイズ               12.51MB           2.93MB（-77%）

  変換コマンド:
    ffmpeg -i 元.mp4 -an -r 30 -vf "scale=1280:-2:flags=lanczos" \
      -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p -movflags +faststart 出力.mp4

  ※ 音声は元から完全な無音（最大 -91dB）だったためトラックごと削除した。
  ※ 60fps は画面収録には不要。30fps でも動きは滑らかに見える。

■ ポスター画像
  moonpicker_demo_poster.jpg（1280x720 / 77KB）
  動画の 25.0秒地点。MOONpicker のヘッダー、テロップ
  「29 jobs scored. 6 worth applying to」、Apply判定のスコアカードが
  1枚に収まる。

  ★この画像は og:image を兼ねている（v3.29 から）。
    LinkedIn や X にLPを貼ったとき、カードに出るのはこの画像。
    動画を撮り直すときは必ずポスターも作り直すこと。

■ ファイル名の改名（9か所）
  jobsearch_demo.mp4         → moonpicker_demo.mp4
  jobsearch_demo_poster.jpg  → moonpicker_demo_poster.jpg

  内訳:
    hub.html                 og:image                    1
    landing_upwork.html      og:image / poster / source / download href  4
    landing_freelancer.html  og:image / poster / source / download href  4

  【旧ファイルについて】
  frontend/static/ の jobsearch_demo.mp4 と jobsearch_demo_poster.jpg は
  このZIPに含めていない。サーバー上に残るが、どのHTMLからも参照されなくなる。
  切り戻しの可能性がある間は消さないこと。落ち着いたら削除してよい。

■ LPの文言修正（2ファイル × 2箇所）
  1) 見出し
     旧: Forty-five seconds, start to finish.
     新: About thirty seconds, start to finish.
     理由: 動画が 44.9秒 → 31.77秒 になったため。
           「Thirty seconds」と言い切らず About を付けたのは実測が31.77秒のため。

  2) 動画下の注記
     旧: … using a test licence, so the monthly allowance shown on screen
         is not one of the plans sold below.
     新: … on the Basic plan — the monthly allowance shown on screen
         is the plan sold below.
     理由: 旧動画は月400回のテストライセンスで撮っていたため言い訳が必要だった。
           新しい動画は Quota left 95 /100 ＝ 販売中の Basic プランそのもの。
           言い訳が不要になり、逆に「販売しているプランで撮った」と言えるようになった。

■ 検証（すべて実行済み）
  1) 残存 jobsearch_demo        0件（3ファイルとも）
  2) moonpicker_demo の出現     9件（旧の出現数と一致）
  3) 行数                       3ファイルとも変更前後で一致
                                （hub 251／upwork 577／freelancer 586）
  4) 差分                       意図した箇所のみ。hub 1行／各LP 5行／main.py 1行
  5) node --check               landing_upwork / landing_freelancer の
                                <script> ブロック PASS（hub にscriptは無い）
  6) ast.parse()                main.py PASS
  7) 不変文字列の出現数         js_ref 2→2 ／ ujs_* 0→0 ／
                                jobsearch.doublemoon.biz 8→8 ／
                                jobsearch_support@ 6→6  すべて一致
  8) 動画の音声トラック数       0（ffprobe で確認）
  9) 動画のフレーム数           953（31.77秒 × 30fps）

■ 同梱していないファイル（変更なし）
  index.html / privacy.html / campaign.html / thanks.html
  sites.py / plans.py / database.py / db_redesign.py / payments.py /
  evaluate.py / rate_limit.py / mailer.py / admin_*.py / settings_admin.py /
  staff_console.py / requirements.txt / runtime.txt

■ デプロイ後の確認手順
  1) /health の version が "3.31.0" になっている
  2) /for/upwork を開く
     ・見出しが "About thirty seconds, start to finish."
     ・動画のポスター画像が「29 jobs scored.」の画面になっている
     ・再生して 31秒ほどで終わること／音が出ないこと
     ・注記が "on the Basic plan …" に変わっている
  3) /for/freelancer も同様
  4) /static/moonpicker_demo_poster.jpg を直接開いて表示されること
  5) SNSカードの確認（任意）
     https://www.opengraph.xyz/ などに /for/upwork のURLを入れて、
     カード画像が新しいポスターになっているか見る。
     ※ LinkedIn や X は og:image をキャッシュするため、
       すぐには切り替わらないことがある。

■ 次にやること（このZIPの範囲外）
  ・Freelancer.com 版の動画を撮る（現在は Upwork の画面を流用中）
  ・ドメイン移行時、og:image の絶対URLも moonpicker.com に変わる（No.31）
