# rust-rcon-exporter

Rust サーバーの WebRCON から `serverinfo` を取得し、Prometheus のテキスト形式に
変換して `/metrics` に出力します。本体は `main.py` 1 本です。
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

| メトリクス | `serverinfo` の項目 |
| --- | --- |
| `server_fps` | `Framerate` |
| `players_online` | `Players` |
| `entities_total` | `EntityCount` |
| `players_queued` | `Queued` |
| `players_joining` | `Joining` |
| `players_capacity` | `MaxPlayers` |
| `server_uptime_seconds` | `Uptime` |

すべて現在値（Gauge）です。`Framerate`・`Players`・`EntityCount` は必須で、
それ以外は取得できる項目だけを出力します。
`rcon_up` は取得成功時に `1`、起動直後や失敗時に `0` です。
失敗時は古いゲーム統計を出力せず、設定した間隔で再接続します。
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

開発時の確認:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## ライセンス

[MIT](LICENSE)
