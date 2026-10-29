# 招待制ベータの運用

この文書はコードと運用手順の説明。公開環境への配備、実データの移行、Agensiでの
接続・掲載審査の成功を示すものではない。無料掲載の素材と外部MCP設定テンプレートは
[`marketplace/agensi`](../marketplace/agensi) にある。

## アクセス方式

HTTPの既定値は `UGH_ACCESS_MODE=invite`。`GET /health` の生存確認を除き、REST、
OpenAPI、Swagger、MCPの発見とツール呼び出しに `Authorization: Bearer <招待キー>` が必要。
設定なし・破損時は503、キーなし・無効・失効時は401となる。キーをURLや監査本文に入れない。
ブラウザのCORS事前確認だけは匿名で応答し、実データの要求には必ず認証を適用する。
Cookie認証、一般公開の登録、OAuthログインは実装していない。
利用するMCPクライアントが外部サーバーへのBearerヘッダー設定に対応している必要がある。
特定のChatGPT/Agensi接続方法の互換性は、対象クライアントで別途確認する。

`UGH_ACCESS_MODE=local` は認証・利用上限を省略し、全履歴を扱う明示的な開発設定。
信頼できる端末のloopback専用とし、公開ホスト・共有データでは使用しない。
単体テストはこのモードを明示し、セキュリティの統合テストはinviteに切り替える。
Pythonの `db.list_recent(owner_id=None)` 等の読取APIは既存の管理者用途として非限定であり、
公開HTTPルートはこの経路を使用しない。

## サーバーの準備

Python 3.10以上、MCP SDK 1.30以上2未満を使う。以下はLinuxの例。
Windowsでは環境変数を `$env:変数名='値'` で設定し、ACLで秘密ファイルを保護する。
リポジトリ内、Web公開ディレクトリ、配布ZIPへ実キー・招待台帳・DBを置かない。

```bash
pip install -e ".[server]"
mkdir -p /srv/ugh-private
chmod 700 /srv/ugh-private
python -m ugh_audit.invites issue \
  --file /srv/ugh-private/invites.json --user alice \
  --token-out /srv/ugh-private/alice.token
export UGH_ACCESS_MODE=invite
export UGH_INVITE_FILE=/srv/ugh-private/invites.json
export UGH_AUDIT_DB=/srv/ugh-private/audit.db
export UGH_USAGE_DB=/srv/ugh-private/usage.db
export UGH_ALLOWED_HOSTS=audit.example.com
uvicorn ugh_audit.server:app --host 127.0.0.1 --port 8000
```

外部公開にはHTTPSリバースプロキシを用意し、`/mcp/` とRESTを同じ認証境界へ接続する。
Dockerの既存起動もこのアプリを使用する。コンテナには同じ永続ボリューム上のDBと
秘密ファイルをマウントし、設定を環境変数で渡す。アプリのポートをTLSなしで直接公開しない。
`python -m ugh_audit.mcp_server` も同じ認証済みASGIアプリを起動する。
MCP単体の `mcp.run()` を別サーバーとして配備しない。

MCPはHost/Originも検証する。`UGH_ALLOWED_HOSTS` は公開Host値（必要ならポート込み）
のカンマ区切り。localhostとloopbackは既定で許可される。クライアントがOriginを送る場合、
`UGH_ALLOWED_ORIGINS` に信頼する完全なOriginを追加する。無関係なホストを許可しない。
リバースプロキシでHostを保ち、接続数・ヘッダー・アクセスログの保護も設定する。

意味照合を実行するにはサーバーに `ANTHROPIC_API_KEY` が必要。ホスト管理者が秘密管理機構で
設定する。キー不足・API障害は `degraded` であり、監査成功ではない。
入力要件・回答は意味照合のためモデル提供者に送られ、確定した監査の本文・根拠はDBに保存される。
利用前にデータ取扱いと保存・削除方針を案内する。Agensiの無料掲載はホスト費やモデル利用料を
無料にするものではない。SBertは参考診断用で、初回取得または事前キャッシュの準備が必要。

## 招待キーの発行・失効

`issue` はランダムなキーを新規 `--token-out` ファイルへ保存し、台帳にはSHA-256のみ記録する。
キーを標準出力へ出さず、既存のキー出力ファイルは上書きしない。キーは利用者へ安全な経路で渡す。
有効期限を指定する場合は `--expires-at <UTCのUnix秒>` を使う。

```bash
python -m ugh_audit.invites revoke --file /srv/ugh-private/invites.json --user alice
```

失効は次のHTTPリクエストから有効。すでに実行中の処理を取り消すものではない。
同じuserへの追加発行は既存キーを維持し、同じ履歴・上限を共有する。
漏えい時のローテーションはそのuserをrevokeしてから、新しい出力ファイルへissueする。
user IDは安定した識別子として使い、別人へ再割当てしない（元の履歴が見えるため）。
台帳編集はCLIで直列化し、手作業による同時書込を避ける。
`.lock` が残った場合は操作プロセスが動いていないことを確認してから管理者が除去する。
台帳とキーのPOSIX権限は0600。Windowsでは管理者が同等のACLを確認する。

台帳の形式（このハッシュは使えない説明用値）:

```json
{"users":[{"user_id":"alice","token_sha256":"REPLACE_WITH_GENERATED_SHA256","enabled":true}]}
```

## 履歴・既存DB

起動時にNULL可能な `owner_id` 列と索引を追加する。移行前にSQLiteの整合したバックアップを
取り、コピーで起動を確認する。既存行はNULLのまま保持し、招待利用者には見せない。
既存行を推測で利用者へ割り当てない。所有者を指定した履歴、ID取得、session集計、driftは
SQLで所有者を絞ってから集計・件数制限する。同名sessionでも混在しない。

`retry_of` は同じ所有者の実在行だけを認める。入力検査と、保存トランザクション内の
再検査を行う。他人のIDと存在しないIDは同じnot foundになる。degradedは通常履歴へ保存せず、
利用上限の消費は保持する。招待の失効は保存データの削除ではない。
退会・保持期間到来時の削除は、管理者がバックアップも含めた運用手順で実施する。

## 利用上限

| 環境変数 | 既定値 | 意味 |
|---|---:|---|
| `UGH_REQUESTS_PER_MINUTE` | 60 | 利用者あたり直近60秒の認証済みHTTPリクエスト数 |
| `UGH_AUDITS_PER_DAY` | 100 | 利用者あたりUTC日付の監査開始数 |
| `UGH_GLOBAL_AUDITS_PER_DAY` | 500 | 全利用者合計のUTC日付の監査開始数 |
| `UGH_CONCURRENT_PER_USER` | 1 | 利用者あたり同時監査数 |
| `UGH_CONCURRENT_GLOBAL` | 4 | 全利用者合計の同時監査数 |
| `UGH_USAGE_DB` | 監査DBと同じディレクトリのusage.db | 永続的な利用台帳 |

すべて1〜1,000,000の整数。失敗・degraded・切断された監査も開始数に含め、返却しない。
開始前に拒否された監査は日次枠を消費しない。履歴取得やMCP初期化もHTTP回数に含む。
監査1回に複数のモデル呼出が含まれるため、金額・トークン数の厳密な予算制御ではない。
モデル提供者側の予算設定も併用する。

RESTの超過は429と `Retry-After`。MCPのHTTP回数超過は同じHTTP応答、ツール内の監査超過は
MCPのtool errorに理由コードと `retry_after` を含める。台帳が使えない場合は実行しない。
SQLiteの即時トランザクションにより同じ台帳を使用する複数プロセスでも共有する。
独立したディスクを持つ複数ホストは上限を共有しないため、このベータは単一ホストで運用する。
ネットワークファイルシステムでのSQLite共有を前提にしない。

実処理中は30秒ごとに予約を更新し、プロセス停止後は300秒で回収する。
クライアント切断だけでは枠を解放せず、workerの終了を待つ。台帳障害後の新規実行は拒否するが、
既に送信した外部モデル呼出を強制停止する機構ではない。

HTTP本文は262,144bytes、受信は15秒。質問8,000文字、回答・reference32,000文字、
session ID128文字、JSONメタデータ131,072bytesを上限とし、超過は413/422。
意味照合の16要件等の制限は引き続き適用される。

## リモート中継

`UGH_REMOTE_API` 使用時も入口の認証・上限を適用し、利用者のBearerキーをHTTPSの
上流へ転送する。両側で同じキーとuser対応を管理し、転送先を管理者が確認する。
認証付き転送はリダイレクトを追わない。利用者のキーを共通サービスキーへ置換しない。
信頼するlocalモードの中継だけは `UGH_REMOTE_TOKEN` で上流の専用キーを設定できる。
入口と上流のそれぞれで上限を数える。独立サービスには独立の利用台帳を割り当てる。

## 掲載・招待開始前の確認

1. この版を配備し、DBコピーで移行・復元を確認する。秘密設定・永続ボリュームを確認する。
2. 実HTTPS環境で、匿名401、正しいキー200、無効化後401を確認する。
3. 別利用者のhistory・ID・session・drift・retryが混ざらず、低い検証用上限で拒否されることを確認する。
4. 本文と明示要件を用いた意味照合、保存・再取得、CSV/textレポートを確認する。
5. 使用するMCPクライアントで接続・認証・tool errorを確認してから、そのクライアントを対応対象に記載する。
6. 無料掲載文、外部MCPの招待条件、費用・データ取扱い・保存方針、問い合わせ窓口を確定する。

CIのテストでは外部ネットワークを遮断し、モデル応答の検証用doubleを使用する。
したがってCI成功は実運用のモデルAPI接続やAgensi承認の確認を代替しない。
