# rust-rcon-exporter

A small, loopback-only Prometheus exporter for Rust Dedicated Server WebRCON.

Rust Dedicated Server の WebRCON に接続し、読み取りコマンド `serverinfo` の結果を
Prometheus 形式で公開します。Carbon・Oxide などのサーバープラグインは不要です。

## 特徴

- 1 本の WebSocket 接続を再利用し、標準で 5 秒ごとに取得
- HTTP のスクレイプはメモリ上の値を返すだけで、RCON コマンドを送信しない
- 通信失敗時は古いゲーム統計を削除し、最大 30 秒の指数バックオフで再接続
- RCON 接続先と HTTP 待受をローカルホストに制限
- パスワード・接続 URL・受信内容をアプリケーションログに出力しない
- 外部プロキシを経由せず、WebSocket のリダイレクトを追跡しない

## 必要条件

- Python 3.10 以上
- WebRCON を有効にした Rust Dedicated Server（`rcon.web 1`）
- exporter と Rust サーバーを同じホスト・ネットワーク名前空間で実行すること
- メトリクスを保存する場合は Prometheus

Rust サーバー側の `rcon.port` と `rcon.password` に合わせて設定してください。
WebRCON には管理者権限があるため、RCON 自体の公開範囲もファイアウォールなどで
制限してください。この exporter が Rust サーバー側の待受設定を変更することはありません。

## インストール

```sh
git clone https://github.com/NekosanQ/rust-rcon-exporter.git
cd rust-rcon-exporter
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Windows PowerShell:

```powershell
git clone https://github.com/NekosanQ/rust-rcon-exporter.git
cd rust-rcon-exporter
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

実行時の外部依存は [`websocket-client`](https://pypi.org/project/websocket-client/)
のみです。互換用に `python -m pip install -r requirements.txt` も利用できます。

## 起動

Linux/macOS（仮想環境を有効にしたシェル）:

```sh
read -r -s -p 'RCON password: ' RCON_PASSWORD; printf '\n'
export RCON_PASSWORD
rust-rcon-exporter
```

上のパスワード入力例は Bash 用です。継続運用には後述の systemd 設定例を利用できます。

Windows PowerShell:

```powershell
$password = Read-Host 'RCON password' -AsSecureString
$env:RCON_PASSWORD = [System.Net.NetworkCredential]::new('', $password).Password
.\.venv\Scripts\rust-rcon-exporter.exe
```

リポジトリ内では `python main.py`、インストール後は
`python -m rust_rcon_exporter` でも起動できます。停止は `Ctrl+C`、Linux のサービス運用では
`SIGTERM` に対応しています。

起動後、別のターミナルで確認します。

```sh
curl http://127.0.0.1:9187/metrics
```

PowerShell では `(Invoke-WebRequest http://127.0.0.1:9187/metrics).Content` を使えます。
設定エラー時の終了コードは `2`、HTTP 待受などの OS エラー時は `1`、正常終了時は `0` です。
RCON の通信エラーはプロセスを終了させず再試行します。

## 設定

すべて環境変数で指定します。`.env` ファイルの自動読み込みは行いません。
[`.env.example`](.env.example) は設定内容の見本で、実際の認証情報を含みません。

| 環境変数 | デフォルト | 内容 |
| --- | --- | --- |
| `RCON_PASSWORD` | なし・必須 | Rust サーバー側で設定した WebRCON パスワード |
| `RCON_HOST` | `127.0.0.1` | `127.0.0.1`、`localhost`、`::1` のいずれか |
| `RCON_PORT` | `28016` | WebRCON ポート。1〜65535 |
| `RCON_POLL_INTERVAL` | `5` | 取得周期。2〜60 秒、小数可 |
| `METRICS_PORT` | `9187` | HTTP 待受ポート。1024〜65535 |

HTTP の待受アドレスは `127.0.0.1` 固定です。RCON の応答待ちは 6 秒です。
取得周期は各コマンドの開始時点を基準にするため、応答が遅い場合は設定周期を超えます。
失敗後は接続を閉じ、1、2、4、8、16、30 秒の順で待機します。正常な `serverinfo` を
取得すると待機時間をリセットします。

`localhost` の名前解決や Rust 側の待受設定によって IPv4/IPv6 の接続可否が変わります。
接続できない場合は `127.0.0.1` または `::1` を明示してください。

## メトリクス

すべて Gauge です。`entities_total` も現在値であり、累積カウンターではありません。
既存メトリクス名との互換性のため、プレフィックスは付けていません。

| メトリクス | `serverinfo` フィールド | 内容 |
| --- | --- | --- |
| `rcon_up` | — | 直近の取得が成功なら `1`、起動直後・失敗後は `0` |
| `server_fps` | `Framerate` | サーバー FPS |
| `players_online` | `Players` | 接続中のプレイヤー数 |
| `entities_total` | `EntityCount` | 現在のエンティティ数 |
| `players_queued` | `Queued` | 接続待ち人数（任意） |
| `players_joining` | `Joining` | 接続処理中の人数（任意） |
| `players_capacity` | `MaxPlayers` | 最大プレイヤー数（任意） |
| `server_uptime_seconds` | `Uptime` | Rust サーバーの稼働秒数（任意） |
| `rcon_response_seconds` | — | 直近の正常なコマンドの所要秒数。接続確立時間は含まない |
| `rcon_last_success_timestamp_seconds` | — | 最後に取得できた Unix 時刻。初回成功までは出力しない |

`Framerate`、`Players`、`EntityCount` は必須です。欠落・負数・真偽値・文字列・非有限数の
いずれかを含む応答は失敗として扱います。任意フィールドの異常は、そのメトリクスだけを省略します。

起動直後は `rcon_up` のみを返します。通信失敗を検知するとゲーム統計と応答時間を削除し、
最終成功時刻だけを保持します。失敗検知までは直近の成功値を返します。
RCON 障害中も `/metrics` は HTTP `200` を返すため、Prometheus 自体の `up` と
`rcon_up` はそれぞれ HTTP の到達性と RCON の取得結果を表します。

## Prometheus

同じホスト上の Prometheus に、次の scrape 設定を追加します。
[設定ファイル例](examples/prometheus.yml) も用意しています。

```yaml
scrape_configs:
  - job_name: rust-server
    scrape_interval: 5s
    static_configs:
      - targets: ["127.0.0.1:9187"]
```

確認用の PromQL:

```promql
server_fps{job="rust-server"}
rcon_up{job="rust-server"} == 0
time() - rcon_last_success_timestamp_seconds{job="rust-server"}
```

最後の式は初回取得前には結果を返しません。初回取得前も検知するには `rcon_up` を使ってください。

Prometheus が別ホストや別コンテナにある場合、その `127.0.0.1` は exporter を指しません。
コンテナ運用ではネットワーク名前空間を合わせるか、ローカルのメトリクス収集エージェントから
転送する構成が必要です。本プロジェクトには外部待受・認証・TLS の機能はありません。

## systemd での運用例（Linux）

以下は `/opt/rust-rcon-exporter` に配置する場合の例です。コマンドはリポジトリ直下から実行します。
Python の `venv` が使えることを確認してください（Debian/Ubuntu では `python3-venv` が必要な場合があります）。

```sh
sudo install -d -m 755 /opt/rust-rcon-exporter
sudo python3 -m venv /opt/rust-rcon-exporter/.venv
sudo /opt/rust-rcon-exporter/.venv/bin/python -m pip install .
sudo install -m 600 .env.example /etc/rust-rcon-exporter.env
sudoedit /etc/rust-rcon-exporter.env
sudo install -m 644 examples/rust-rcon-exporter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rust-rcon-exporter
```

環境ファイルのパスワード・RCON ポートを実際の値に変更してから起動してください。
特殊文字を含む値は systemd の
[`EnvironmentFile` の構文](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#EnvironmentFile=)
に従って引用します。

```sh
sudo systemctl status rust-rcon-exporter
sudo journalctl -u rust-rcon-exporter -n 50 --no-pager
```

[サービス定義](examples/rust-rcon-exporter.service) は `DynamicUser` とファイルシステム保護を使用します。
仮想環境を含む `/opt/rust-rcon-exporter` はサービスから読み取り・実行できる必要があります。
`/home` 配下の仮想環境を参照する構成には書き換えないでください。
環境ファイルを変更したら `sudo systemctl restart rust-rcon-exporter` で反映します。

## トラブルシューティング

| 症状 | 確認箇所 |
| --- | --- |
| `Configuration error` | 必須パスワード、ローカルホスト制限、各設定の数値範囲 |
| `Missing websocket-client dependency` | 実行する Python の仮想環境に `python -m pip install .` でインストールしたか |
| `Cannot run exporter` | HTTP ポートの重複、OS の待受制限 |
| HTTP は返るが `rcon_up 0` | `rcon.web 1`、RCON ポート・パスワード、Rust サーバー起動状態、IPv4/IPv6 |
| `ValueError` で再試行 | 対象の `serverinfo` が必須数値フィールドを返しているか |
| 任意のメトリクスが出ない | 対象バージョンの `serverinfo` に、そのフィールドが含まれるか |

エラーは認証情報を保護するため例外の種類だけを記録します。Issue にログを添付する場合も、
RCON パスワード、接続 URL、環境変数一覧、実サーバーの非公開情報を含めないでください。
この exporter は管理用パスワードをプロセス環境に保持します。環境ファイルを Git に追加せず、
OS アカウントとファイルのアクセス権を適切に管理してください。

## 開発と検証

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python -m build
```

テストは設定検証、応答解析、Prometheus 形式、HTTP、再試行、ログへの秘密情報の漏出防止、
疑似 WebRCON を使った通信とプロセス終了を確認します。実際の Rust サーバーの認証情報は不要です。
CI では Linux/Windows と Python 3.10/3.14 の組み合わせで実行します。
疑似サーバーの検証と実際の Rust サーバーでの互換性確認は別です。

```text
rust_rcon_exporter/
  config.py   環境変数の読み込みと検証
  metrics.py  serverinfo の解析と共有メトリクス
  rcon.py     WebRCON の接続・取得・再試行
  http.py     /metrics の応答
  cli.py      CLI と起動・終了処理
examples/     Prometheus と systemd の設定例
tests/        単体テストと疑似 WebRCON の結合テスト
```

## ライセンス

[MIT License](LICENSE)
