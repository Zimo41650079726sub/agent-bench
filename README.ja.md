# agent-bench

[English](README.md) | 日本語

**あなたのエージェントスキルを健全に完走できる最小のローカルLLMを見つける。**

agent-bench は多段のツール駆動能力（エージェント遂行能力）を、*決定論的な採点だけ*で測るベンチマークです — exit code・ファイル初出ターン・SHA256 のみ。LLMジャッジなし、完全ローカル、再現可能。測定対象の能力は「旗」（オリエンテーリング方式）として分解され、モデルが各旗に到達したか・何ターンで・どれだけ安定して（pass^k）到達したかを報告します。OpenAI互換サーバー（llama-server / Ollama / LM Studio）に向けるだけで動き、実物のスキルディレクトリをマウントすれば「このスキルにはモデルXのQ6以上が必要」という判定が得られます。

## 必要環境

- Python 3.12+（コアは標準ライブラリのみ — pip install 不要）
- Docker（サンドボックスコンテナ）

## 実行

```bash
docker build -t agent-bench:latest .
python3 tests/verify_harness.py   # LLMなしのセルフチェック（モックモデル）
python3 cli.py --model YOUR_MODEL --task debug_python_v1 --k 3 \
    --base-url http://localhost:8080/v1
```

実物のスキルをベンチする場合:

```bash
python3 cli.py --model YOUR_MODEL --task skill_run_v1 \
    --skill-dir ./examples/skills/report-skill --k 5 \
    --base-url http://localhost:8080/v1
```

## ダッシュボード

```bash
python3 webui.py          # -> http://127.0.0.1:8765/
```

`results/` を配信するローカルダッシュボード — 標準ライブラリのみ、ライト/ダーク対応:

- **実行パネル** — モデル（サーバーからライブ取得）・タスクまたはインストール済みスキル・k を選んで実行し、ターンごとの実況ログを見る。同時実行は1つ。
- モデル×タスクのマトリクス（pass^k・スコア・ターン数・所要時間・改竄・ハルシネーション呼び出し）と、スキルごとの旗到達率チャート。
- **行をクリックすると完全なトランスクリプト**: 全ツール呼び出しと exit code・展開可能な stdout/stderr・呼び出しの合間にモデルが言ったこと・最終回答。
- **成果物プレビュー** — モデルが正当に生成したファイルをコンテナ破棄前にエクスポート。HTML成果物はサンドボックス化された iframe で、画像もインラインで表示。各モデルが実際に作ったものを並べて見比べられます（`examples/skills/landing-page` 参照）。

## 実測結果

計測環境: Snapdragon X Elite・64GB RAM・LM Studio（llama.cppバックエンド）・サンドボックス = WSL2上のDocker。所要時間はこのハードウェアに対する相対値であり、絶対値ではなく比率として読んでください。量子化: qwen3.5-4b = Unsloth UD-Q4_K_XL、qwen3.6-35b-a3b = Unsloth UD-Q6_K_XL、Gemma 4 = 公式GGUFリリース（e2b/e4b/12b QAT、26b-a4b instruct）。`scripts/make_matrix.py` で生成。

| task | gemma-4-26b-a4b-it | gemma-4-e2b-it-qat | gemma-4-e4b-it-qat | gemma-4-12b-qat | qwen3.5-4b | qwen3.6-35b-a3b |
|---|---|---|---|---|---|---|
| context_manage_v1 | ✅ pass^3 · 10.0t · 93s | ✅ pass^3 · 11.0t · 51s | ✅ pass^3 · 8.0t · 24s | ✅ pass^3 · 12.0t · 5.5m | ✅ pass^3 · 11.0t · 52s | ✅ pass^3 · 5.0t · 2.0m |
| debug_python_v1 | ✅ pass^3 · 5.0t · 58s | ✅ pass^3 · 4.0t · 15s | ✅ pass^3 · 5.0t · 20s | ✅ pass^3 · 5.0t · 87s | ✗ 0.75 (k=10) · 17s | ✅ pass^3 · 4.0t · 53s |
| skill_run_v1_imitate_dashboard | ✅ pass^1 · 3.0t · 7.8m | ✅ pass^1 · 2.0t · 2.8m | ✅ pass^1 · 3.0t · 7.8m | ✗ 0.20 (k=1) · 181.3m | ✅ pass^1 · 6.0t · 6.0m | ✅ pass^1 · 12.0t · 32.6m |
| skill_run_v1_landing_page | ✅ pass^1 · 4.0t · 2.1m | ✅ pass^1 · 5.0t · 39s | ✗ 0.25 (k=3) · 4s | ✅ pass^1 · 5.0t · 2.7m | ✅ pass^1 · 6.0t · 94s | ✅ pass^1 · 4.0t · 4.5m |
| skill_run_v1_report | ✅ pass^3 · 4.0t · 53s | ✅ pass^3 · 5.0t · 14s | ✅ pass^3 · 5.0t · 21s | ✅ pass^3 · 4.0t · 68s | ✅ pass^3 · 6.0t · 20s | ✅ pass^3 · 5.67t · 62s |
| tdd_order_v1 | ✅ pass^3 · 4.0t · 74s | ✗ 0.44 (k=3) · 25s | ✗ 0.67 (k=3) · 28s | ✅ pass^3 · 6.0t · 4.0m | ✅ pass^3 · 4.0t · 49s | ✅ pass^3 · 4.0t · 96s |

`✅ pass^k · Nt · T` = k試行すべて合格・平均Nターン・1試行あたり平均実時間。`✗ S` = 少なくとも1試行が失敗し平均スコアS。(task, model) ごとに最新の結果。

### モデルTier（旗から機械的に導出）

| tier | 定義 | モデル |
|---|---|---|
| 1 | **完全な規律** — 全タスク合格、プロセス旗も含めて | gemma-4-26b-a4b-it (23.9m), qwen3.6-35b-a3b (53.7m) |
| 2 | **結果は出せる** — タスクは完了するがプロセス旗を落とす（例: 修正前に失敗を観測しない） | gemma-4-e2b-it-qat (8.7m), gemma-4-e4b-it-qat (12.6m), qwen3.5-4b (16.4m), gemma-4-12b-qat (220.4m) |
| 3 | **ツールを駆動できない** — エージェントループ自体が回らない | — |

各Tier内は総クリア時間（全ベンチタスク・全試行の合計）順。

面白いのは、各モデルの失敗が*特異的*かつ*再現的*であることです — 能力はパラメータ数に対して単調ではありません:

- **gemma-4-e2b-it-qat (2B)** は 5/6 タスクに合格 — 兄貴分たちがつまずく成果物系スキル両方（e4bが離脱するランディングページ、12bが崩壊するダッシュボード）を含む — しかも盤上最速（合計8.7分）。唯一の欠落は純粋なTDD規律: テストを書いた後、失敗を観測せずにそのまま実装に入る（`red_observed` 0/3）。
- **gemma-4-12b-qat** は 5/6 タスクに合格するがダッシュボードタスクで崩壊: HTMLペイロードが大きくなると `write_file` の `path` 引数を出力しなくなる — 「エラーは `path` 引数の欠落が原因」と自分で正しく診断した直後でさえ、9回連続で。一方はるかに小さい e4b は同じタスクを3ターンでクリアする。
- **gemma-4-e4b-it-qat** は重量級のダッシュボードタスクをクリアするのに、*より簡単な*ランディングページのスキルをターン1で放棄する（`.documentation` というパスをハルシネーションし、読みに行って失敗し、諦める — 3/3試行で同一）。さらにTDDでは*自分のテストファイルの中に* fizzbuzz のスタブを書いてしまい、自分で作った赤から抜け出せない。
- **qwen3.5-4b** がベンチ全体で落とす旗はちょうど1つ: バグったファイルを、コードを実行して失敗を観測することなく修正する（`error_interpret`）— 純粋なプロセス規律の欠落。

## 結果JSONの読み方

`results/result_{task_id}_{timestamp}.json` に書き出されます:

- `pass_all_k` — k試行すべてが合格（全旗到達 + 改竄なし）
- `skills.{flag}.reach_rate` / `avg_turns` — 旗ごとのモデルの癖の診断
- `invalid_tool_call_count` — ツール名ハルシネーション（小型モデルの支配的な失敗）
- `tamper_detected_count` — 範囲外のファイル変更で無効化された試行
- `trials[]` — 事後分析用の試行ごとの完全な `turn_logs`
- `environment` — サンドボックスイメージ + **サンプリングパラメータ**（temperature/seed/top_p）・サーバー・モデル。temperature=0 でもローカルサーバーは決定論を保証しないため記録必須: 本ベンチの立場は「パラメータを固定して記録し、pass^k の分散自体を安定性の指標として扱う」。
- `result_hash` — 破損・重複した結果ファイルの整合性チェック。署名ではなく、偽造は防ぎません。

## アンチチート機構

- ワークスペース全体のハッシュ差分: タスクの `allowed_writes` 外のあらゆる変更（こっそり追加された `conftest.py` のような*新規*ファイルを含む）は試行を無効化。
- 検証系の旗はモデルの exit code を信用しない（`pytest || true` は通用しない）: ハーネスが独立にチェックを再実行する。
- ファイル順序（テストファースト等）はターンごとのワークスペーススナップショットから判定するため、シェルの `echo > file` も write_file ツールと同じように数えられる。

## タスクの追加

`agent_bench/tasks/` で `BenchTask` をサブクラス化し、`setup` / `get_prompt` / `skills` / `allowed_writes` を定義して `tasks/__init__.py` に登録。実行ループと評価基盤には一切触れません。

## 自作スキルのマウント

スキルの `SKILL.md` の隣に `bench_manifest.json` を置き、`required_reads`・`required_commands`（正規表現）・`expected_artifacts`・`allowed_writes`、および重いスクリプト（画像生成等）を argv レコーダーに差し替える `stubs` を宣言します — 本ベンチが測るのはモデルが手順を正しく駆動できるかであって、出力の品質ではありません。`examples/skills/report-skill/` を参照。2つの旗は常に無料で計測されます: `no_tool_hallucination` と `step_completion`。
