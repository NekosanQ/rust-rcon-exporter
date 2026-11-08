# rust-rcon-exporter

Rust サーバーの WebRCON から `serverinfo` を取得し、Prometheus のテキスト形式に
変換して `/metrics` に出力します。本体は `main.py` 1 本です。

## 起動

Linux の Docker Engine と Docker Compose 2.30.0 以上を使用します。
Rust 側で WebRCON（`rcon.web 1`）を有効にしてください。

```sh
cp .env.example exporter.env
chmod 600 exporter.env
```

`exporter.env` の `RCON_PASSWORD` と `RCON_PORT` を実際の値に変更して起動します。
値はそのまま渡すため、パスワードを引用符で囲む必要はありません。

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
実際のパスワードを含む `exporter.env` は Git に追加しないでください。

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

Prometheus には同じホストから取得する設定を追加します。

```yaml
scrape_configs:
  - job_name: rust-server
    static_configs:
      - targets: ["127.0.0.1:9187"]
```

## Python で直接実行

Python 3.10 以上でも実行できます。環境変数を設定して起動してください。
Python 自体は `exporter.env` を読み込みません。

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
