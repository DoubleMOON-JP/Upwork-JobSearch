JobSearch ソース一式  v3.27（2026-08-18）
====================================================================

■ このリリースの内容：再検索できないバグの修正（画面のみ）

  変更したのは frontend/index.html の1ファイルだけ（＋APP_VERSION）。
  サーバー側（evaluate.py / rate_limit.py / plans.py）は変更していない。

  ※ v3.26 は欠番。求人の取りこぼし追加対策（似た求人の重複判定）は
    保留中で、このパッケージには入っていない。中身は v3.25 と同じ。


■ 何が起きていたか

  「Clear & start a new search」を押した直後に検索すると、
  「Wait 26s」と表示されるのに結果が出ない。
  もう一度押すと、少し待って結果が出る。

  原因は2つ重なっていた。

  【1】Clear がサーバーの制限を無視して画面を「押せる」状態に戻していた

      採点は同じライセンスで120秒に1回まで（plans.py RATE_LIMIT_SECONDS）。
      ところが clearSearch() が

          clearInterval(countdownTimer);
          $("evalBtn").disabled = false;
          $("evalLabel").textContent = "Score these jobs";

      を実行していたため、制限中でもボタンが押せる見た目に戻っていた。
      サーバー側の120秒は当然そのまま残っている。

  【2】429を受けた時、カウントダウンの無効化が finally に打ち消されていた

      runEvaluate() は

          setEvalLoading(true);
          try{
            ... 429なら startCountdown() でボタンを無効化して return
          } finally { setEvalLoading(false); }   ← 必ず後に走る

      という構造だった。JavaScript では try の中で return しても
      finally が後から実行されるため、startCountdown() の無効化が
      即座に打ち消される。結果、

          ・ボタンは押せるまま
          ・ラベルだけが「Wait 30s」「Wait 29s」…と動く

      という状態になり、「処理中だから待てば結果が出る」と見えていた。
      実際にはサーバーは429を返しており、採点は1度も実行されていない
      （Gemini も呼ばれず、月間回数も消費されていない）。

      実機のスクリーンショットでは、finally の直後・最初の1目盛りが
      来る前の瞬間（メッセージは「Please wait 9 seconds」なのに
      ボタンは「Score these jobs」で押せる状態）が写っていた。


■ どう直したか

  カウントダウンという仕組みごと削除した。追加ではなく削除で直している。

  【削除】
      ・startCountdown() 関数
      ・countdownTimer 変数
      ・ボタンのラベルが「Wait NNs」に変わる動き
      ・clearSearch() の3行（clearInterval / disabled / textContent）

  【変更】
      ・「Clear & start a new search」→「Clear」
        押しても検索は始まらない。文言が動作と食い違っていた。
      ・連打制限のメッセージを、実行されていないことが先に伝わる文にした。
        黄色（注意）から赤（エラー）に変更。

            Not scored — you pressed too soon.
            Wait about 31 more seconds, then press "Score these jobs" again.

        残り秒数はサーバーが実測値（retry_after_sec）を返すので、
        それをそのまま1回表示するだけ。画面側では数えない。

  【変更なし】
      ・採点中のスピナー「Scoring…」
        これが「画面が止まっていない」ことを示す唯一の表示。
        今まではカウントダウンに上書きされて見えにくかった。
      ・月間上限のメッセージ
      ・サーバー側のすべて


■ 画面側に「120秒」を書かなかった理由

  「2分に1回です」と画面に書くと、plans.py の RATE_LIMIT_SECONDS と
  同じ値を2箇所で持つことになり、将来この値を変えたときに食い違う。
  実際に押せばサーバーが正確な残り秒数を返すので、それを表示している。

  もし「2分に1回」という文言を出したくなった場合は、画面に直書きせず、
  rate_limit.py の429応答に間隔の値を足して画面へ渡すこと。


■ 検証済みの内容（v3.27）

  index.html の <script> をそのまま取り出し、DOMを模した環境で実行した。

  ・連打制限に当たった時：ボタンはすぐ押せる状態に戻り、ラベルは
    「Score these jobs」のまま。Wait 表記もスピナーも出ない
  ・1.2秒後も表示が変わらない（タイマーが残っていないことの確認）
  ・メッセージが「Not scored」で始まり、赤で出る
  ・制限中に Clear を押しても、ボタンの状態を触らない
  ・採点成功時：結果が描画され、ボタンが戻る
  ・採点中：ボタンが無効になり、スピナーと Scoring… が出る
  ・月間上限のメッセージは従来どおり
  ・貼付欄が空のまま押した時は従来どおり黄色の注意
  ・JS構文エラーなし（node --check）
  ・JSが参照するID 30件がすべてHTML側に存在する
  ・サーバーが差し込むプレースホルダ6種がすべて残っている
  ・CSV出力の先頭BOM（U+FEFF）が実行時に出ることを確認
    （Excelで開いた時の文字化け防止。書き換えで壊していない）


■ 差し替え方

  frontend/index.html をリポジトリの同じファイルと入れ替える。
  リポジトリでルート直下に index.html を置いている場合は、そちらを
  入れ替えること（main.py は frontend/index.html → index.html の順で探す）。

  デプロイ後の確認は /health のコミットハッシュで行う。
  APP_VERSION も 3.27.0 に上げてある。

■ main.py に残っているもの（16ルート）

  すべて利用者向け、または全体に関わるもの。管理画面は1つも残っていない。

      /            /r/{code}      /for/{site}    /app
      /app/{site}  /privacy       /campaign      /thanks
      /health      /promo         /ping          /mypage
      /license/validate           /license/by-checkout/{checkout_id}
      /version/{component}        /download/excel

  ほかに：起動処理、CORS、旧ドメインのリダイレクト、
  認証と権限判定（verify_admin / verify_any）、各モジュールの登録。


■ ファイル構成

  main.py             ルーティング・画面配信・認証・各モジュールの登録   524行
  admin_ui.py         管理画面の共通部品（何もimportしない）           238行
  admin_home.py       管理トップ＋広告欄・担当者マスタ・配布ファイル      705行
  admin_licenses.py   ライセンス一覧＋操作API                        541行
  admin_referrals.py  紹介リンク管理                                470行
  admin_prompts.py    プロンプト管理                                224行
  staff_console.py    スタッフ用画面 /staff                         206行
  settings_admin.py   AI設定（為替レート）                          396行
  evaluate.py         サーバー側 Gemini 採点                        471行  ← 変更
  sites.py            対応求人サイト定義                            140行
  requirements.txt    依存パッケージ
  frontend/index.html 利用者向け採点画面                        ← 変更

  ※ 同梱していない（この期間に変更していない）ファイル
     database.py / db_redesign.py / rate_limit.py / payments.py /
     plans.py / mailer.py / runtime.txt / frontend/ の index.html 以外


■ 画面モジュールと権限の対応

  verify_any（管理者＋スタッフ）  admin_licenses / admin_referrals / staff_console
  verify_admin（管理者のみ）      admin_home / admin_prompts / settings_admin

  main.py の末尾で、それぞれに対応する認証関数を渡している。
  渡す関数を間違えると権限が緩む／厳しくなるため、追加時は必ず確認すること。


■ 画面を切り出すときの型

  1. モジュール直下に router = APIRouter() を置く
  2. 認証は main.py から関数を受け取る

        _verify = None
        def build_xxx_router(verify_fn):
            global _verify
            _verify = verify_fn
            return router
        def verify(credentials = Depends(security)):
            return _verify(credentials)

  3. main.py の末尾に2行足す
  4. 共通で使うもの（UI_TEXT / esc / ui_text / plan_label_ui）は
     admin_ui.py から import する

  【重要1】コードを移すときに再インデントしないこと。
  画面のHTMLは f-string で書かれており、インデントを変えると
  文字列の中身が変わってしまう。そのため router 方式にしている。

  【重要2】ルート定義の順序に注意。/admin/prompts/new は
  /admin/prompts/{prompt_id} より先に定義すること（逆にすると
  new が {prompt_id} として解釈される）。モジュール内の並び順が登録順になる。

  【重要3】環境変数を使う画面は os.environ から直接読む
  （main.py を import すると循環参照になる）。既定値を main.py と揃えること。
  admin_referrals.py の BASE_URL がその例。


■ 検証済みの内容（v3.25）

  ・プロンプトの差分が意図した1箇所のみ（Upwork・Freelancer.com 双方）
  ・採点基準・セキュリティ規則・応募要件の抽出指示・出力仕様・
    点数降順の指示は1文字も変わっていない
  ・貼付テキスト以降（フェンス〜末尾）が新旧で完全一致
  ・為替レート参照表の位置と内容が従来どおり（Freelancer.com）
  ・ユーザーの自由要望ブロックが従来どおり末尾に付く
  ・サイト固有のノイズ除去指示を消していないこと
  ・全10ファイルが構文エラーなし


■ 検証済みの内容（v3.24 から継続）

  ・ルート46本が変更前後で完全に一致（消失・重複なし）
  ・未定義の名前なし（8ファイルとも静的解析で確認）
  ・移動した806行のうち変更は26行のみ。すべて @app.→@router. と
    Depends(verify_admin)→Depends(verify) の置換。HTMLの変更は0行
  ・import 整理40件は「本体で使われていない名前」だけを機械的に削除し、
    削除後に未定義参照が出ないことを再確認
  ・スタブ環境で全モジュールを import し、ルーター組み立てと認証注入を確認
  ・/admin/prompts/new が {prompt_id} より先に定義されていることを確認


■ 注意（従来から変わらず）

  ・evaluate.py は全サイト共通。触ったらUpworkの回帰テストを行うこと。
  ・HTMLは f-string。波括弧は {{ }} にすること。
  ・polar-sdk は 2026-09-06 の更新課金テストが終わるまで上げないこと。
  ・リリースのたびに main.py の APP_VERSION を更新する（1行のみ）。
