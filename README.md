# rust-rcon-exporter

Rust サーバーの WebRCON から `serverinfo` を取得し、Prometheus のテキスト形式に
変換して `/metrics` に出力します。返された数値・真偽値の項目をすべて自動で出力します。
本体は `main.py` 1 本です。
実行時の Python 依存は `websocket-client` のみです。

## 構成

```text
main.py               RCON 取得・変換・HTTP 出力
requirements.txt      実行時の依存
Dockerfile            実行用イメージ
compose.yaml          コンテナの起動設定
.env.example          接続設定の見本
tests/test_main.py    動作確認用テスト
requirements-dev.txt  テスト・コード確認用の依存
.github/workflows/ci.yml  CI
```

## 起動

Linux の Docker Engine と Docker Compose v2 以上を使用します。
Rust 側で WebRCON（`rcon.web 1`）を有効にしてください。

```sh
cp .env.example .env
chmod 600 .env
```

`.env` の `RCON_PASSWORD` と `RCON_PORT` を実際の値に変更して起動します。
パスワードはシングルクォートで囲むと、`$`・`#`・空白をそのまま扱えます。
パスワード中の `'` は `\'` と記述してください。囲みの引用符はパスワードに含まれません。

```dotenv
RCON_PASSWORD='your-password$with#symbols'
```

```sh
docker compose up -d --build
curl http://127.0.0.1:9187/metrics
```

ホストネットワークを使用します。WebRCON にはホストの localhost で接続し、
HTTP は `127.0.0.1` でのみ待ち受けます。Rust が別コンテナの場合は、
その WebRCON をホストのループバックに公開してください。

```sh
docker compose logs --tail=50 exporter
docker compose down
```

設定を変更した場合は `docker compose up -d --force-recreate`、
コードを変更した場合は `docker compose up -d --build` で反映します。

## 設定

| 環境変数 | デフォルト | 内容 |
| --- | --- | --- |
| `RCON_PASSWORD` | 必須 | WebRCON パスワード |
| `RCON_HOST` | `127.0.0.1` | `127.0.0.1` / `localhost` / `::1` |
| `RCON_PORT` | `28016` | WebRCON ポート（1〜65535） |
| `RCON_POLL_INTERVAL` | `5` | 取得後、次の取得まで待つ秒数（2〜60） |
| `METRICS_PORT` | `9187` | HTTP ポート（1024〜65535） |

設定ファイルは Docker Compose が環境変数として渡します。
実際のパスワードを含む `.env` は Git に追加しないでください。

## 出力

`serverinfo` の直下にある数値・真偽値を、項目ごとの `rust_server_*` Gauge として出力します。
項目の固定リストはなく、新しい数値項目も自動で出力します。
名前は `NetworkIn → rust_server_network_in` のように自動変換します。
`Framerate` は使いやすい `rust_server_fps` にしています。
真偽値は `true=1`、`false=0` に変換します。文字列・配列・オブジェクト・null・非有限数は出力しません。

出力例（項目と値はサーバーによって異なります）:

```prometheus
# TYPE rust_server_fps gauge
rust_server_fps 60
# TYPE rust_server_players gauge
rust_server_players 3
# TYPE rust_server_memory gauge
rust_server_memory 123.5
# TYPE rust_server_collections gauge
rust_server_collections 42
# TYPE rust_server_network_in gauge
rust_server_network_in 100
# TYPE rust_server_network_out gauge
rust_server_network_out 200
```

値や単位はサーバーの報告どおりで、メモリのバイト換算や通信速度の計算は行いません。
`Collections` もサーバーが返す値をそのまま扱います。
FPS の取得は Prometheus や Grafana のクエリ欄に `rust_server_fps` と入力します。
旧形式の `rust_serverinfo{field="Framerate"}` などを使用している場合はクエリを変更してください。

名前には英数字と `_` を使います。変換後の名前が重複する場合は `_2`、`_3` などで区別し、
同じプロセスの稼働中は、項目が一時的に消えたり順序が変わったりしても名前を保持します。

exporter 側では次の項目も出力します。

| メトリクス | 内容 |
| --- | --- |
| `rcon_up` | 正常取得で `1`、起動直後・失敗後は `0` |
| `rcon_response_seconds` | 正常なコマンドの送信から応答までの秒数。接続確立時間は含まない |
| `rcon_last_success_timestamp_seconds` | 最後に正常取得した Unix 時刻。初回成功までは出力しない |
| `rcon_poll_failures_total` | 接続・取得・解析の累積失敗回数（Counter）。exporter 再起動でリセット |

数値・真偽値の項目が1つもない応答は取得失敗として扱います。失敗時は古いサーバーの値と応答時間を削除し、
最終成功時刻と失敗回数を保持して、設定した間隔で再接続します。
WebSocket 接続は再利用し、HTTP は直近の取得結果を返します。

Prometheus には同じホストから取得する設定を追加します。設定例は以下のとおりです。

```yaml
scrape_configs:
  - job_name: rust-server
    static_configs:
      - targets: ["127.0.0.1:9187"]
```

## Python で直接実行

Python 3.10 以上でも実行できます。環境変数を設定して起動してください。
Python 自体は `.env` を読み込みません。

```sh
python -m pip install -r requirements.txt
python main.py
```

開発時の確認（CI でも同じ静的チェック、テスト、イメージビルドを実行します）:

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check --isolated .
python -m ruff format --isolated --check .
python -m unittest discover -s tests -v
docker build .
```

## ライセンス

[MIT](LICENSE)
