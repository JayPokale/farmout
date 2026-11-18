# farmout

[English](../README.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md) · [Español](README.es.md)

**上限に当たる回数を減らし、当たっても作業を止めない。**

farmout は、すでに契約している AI サブスクリプション（Claude、Codex、Gemini、GitHub Copilot、DeepSeek、2 つ目の Claude アカウント、ローカルモデル）に作業を任せます。長文の読み込みやレビューを任せればメインのモデルのクォータが長持ちし、どれかが利用上限に達したりログインが切れたりすると、タスクは会話ごと次のモデルに引き継がれます。どのターンからでも分岐できる会話、並列コードレビュー、テストが勝者を決めるレース、パイプラインを、ターミナルからでも Claude Code の中からでも。デフォルトで読み取り専用、依存関係ゼロ。

[![CI](https://github.com/JayPokale/farmout/actions/workflows/ci.yml/badge.svg)](https://github.com/JayPokale/farmout/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![MCP](https://img.shields.io/badge/MCP-stdio-6e56cf)
![license](https://img.shields.io/badge/license-MIT-blue)

> **注:** この翻訳は抄訳です。すべての機能（DeepSeek ワーカー、スキル、フック、ストア、設定など）は[英語の README](../README.md) をご覧ください。

```bash
pipx install git+https://github.com/JayPokale/farmout
cd your-repo && farmout bench --share   # あなたのコードの実際のバグを一番見つけるのはどの AI か？
```

<p align="center"><img src="../assets/limits-demo.gif" width="900" alt="実際の実行の録画（待ち時間だけ早送り）: farmout run claude で会話を始め、Claude をログアウトさせると farmout send が次のターンを Codex に渡し、Codex はそれまでの会話をもとに答える。続いて farmout bench が過去の 3 件のバグ修正で Claude Opus と Sonnet を比べ、--apply で勝者をデフォルトにする"></p>

- **上限でも止まらない**: サブスクリプションが利用上限に達したりログアウトしていたりすると、farmout のタスク・会話・パイプラインのステージは、それまでの会話を持ったまま次に使えるモデルへ移ります。意図的に切り替えることもできます（`farmout send cache -w codex`、または `--model opus`）。farmout が移せるのは farmout 自身のタスクと会話で、すでにクォータを使い切った Claude Code の対話セッションは救えません。
- **サブスクリプション 1 つでも十分**: 1 つのプランの複数モデルでレーンを埋められます（`farmout review -w claude:opus,claude:sonnet`）。`farmout bench --apply` は、あなたのコードで最も成績の良かったモデルをデフォルトにします。
- **会話をグラフで**: `farmout fork cache --at 2 -w codex "..."` でどのターンからでも別のモデルに分岐できます。`farmout ui` はすべての会話をターミナル上の分岐グラフとして描き、任意のターンを読む・続ける・そこから分岐することができます（`farmout tree` は同じグラフを出力）。フォルダの名前を変えたり移動したりしても、会話はそのまま続けられます。
- **`/farmout:review`**: Codex、Gemini (Antigravity 経由)、GitHub Copilot、2 つ目の Claude アカウントが、あなたの diff を読み取り専用で同時にレビューします。Claude が結果を統合し、各問題を指摘したモデル数を集計して、すべての指摘をコードと照合します。[ベンチマーク](../bench/README.md)では、32 件のバグ (仕込んだ 23 件と、オープンソースプロジェクトの実際のリグレッション 9 件) のすべてが、4 モデル中 3 モデル以上に指摘されました。
- **`/farmout:race <task>`**: 各モデルが同じ修正を、それぞれ専用の git worktree で試みます。勝者はテストで決まり、あなたが承認するまで作業ツリーには一切変更が入りません。
- **日常的な委譲**: Claude が長文の読み込みやボイラープレートの作成を、適したサブスクリプションに任せます。この方法で論文 3 本を要約したところ、同じ回答を得るのに Claude のトークン消費は **59% 減**(コストは 74% 減)でした。

Claude には課金している。ChatGPT や Google AI、2 つ目の Claude シート、あるいは GPU 上のオープンモデルもあるかもしれません。それなのに、最良のモデルのクォータは PDF の読み込みやボイラープレートの作成に消え、他のモデルは遊んでいます。

farmout は Claude Code を**ヘッド**にします。ヘッドは計画を立て、ジョブを自己完結したサブタスクに分割し、それぞれを十分な能力を持つモデルに送って、自分のクォータは難しい部分のために温存します。

- 60 ページの論文は **Antigravity** (Gemini) が読み、150 語にまとめて返します
- 「`parse_date()` のテストを書いて」は **Codex** が、テストの作成と実行まで行います
- 中程度の複雑さの作業は **2 つ目の Claude アカウント**に任せます
- 難しい変更へのセカンドオピニオンは、複数ベンダーのモデルを提供する **GitHub Copilot** に任せます
- 2,000 行の分類は**任意の OpenAI 互換モデル**(Ollama、vLLM、OpenRouter、Qwen など)に任せます

これらは 1 回のツール呼び出しで同時に実行されます。各ワーカーはベンダー純正の CLI で、あなた自身のサブスクリプションでログインして使うため、CLI ワーカーに API キーは不要です。

## コマンドライン

farmout は CLI としても使えます。リポジトリをクローンして `pipx install` でインストールし（PyPI 版は近日公開）、`farmout doctor --live` で各ワーカーを確認します：

```bash
# 全モデルが変更をレビューし、何モデルが一致したかを付けて指摘をまとめます
farmout review
# 各モデルが専用の worktree で挑戦し、あなたのテストが勝者を決めます
farmout race "tests/test_sync.py の不安定なテストを決定的にする" --test "pytest tests/test_sync.py -x"
# 会話を覚えるセッション
farmout run codex "src/dates.py の parse_date() に単体テストを追加" --isolate -n dates
farmout send dates "うるう年もカバーして"
# パッチを作業ツリーに適用（または --pr）
farmout apply dates
# 会話のどのターンからでも、別のモデルで分岐
farmout fork dates --at 1 -w claude "タイムゾーン付きの入力もテストして"
# すべての会話をターミナル上の分岐グラフとして閲覧・続行・分岐
farmout ui
# レビュー → 2 モデル以上が一致した問題を修正 → 修正をレビュー
farmout pipe run fix
# 各サブスクリプションがしてくれたこと
farmout stats
```

セッション、パイプライン（ステージ・ゲート・承認を持つ `.toml` または `.json` ファイル）、リポジトリごとの設定（`.farmout/settings.toml`：コードを見てよいプロバイダー、書き込み禁止、Web 禁止）、利用上限に達したワーカーの自動切り替え、すべてのコミットをレビューする git フックについては、[英語版 README](../README.md) を参照してください。

## クイックスタート

必要なもの: Python 3.9+、Linux (動作確認済み) または macOS (未検証)、[Claude Code](https://github.com/anthropics/claude-code)、および 1 つ以上のワーカー:

| ワーカー | 必要なもの | 向いている用途 |
|---|---|---|
| `antigravity` | [Antigravity CLI](https://antigravity.google) (`agy`)、ログイン済み | 長い PDF、論文、多数ファイルの読み込み、Web 調査。Gemini のほか `agy models` に表示される他のモデルも使えます |
| `codex` | [Codex CLI](https://github.com/openai/codex)、ログイン済み | 仕様が明確なコーディング、テスト、リファクタリング、レビュー |
| `claude` | 2 つ目の Claude Code アカウント + `FARMOUT_CLAUDE_DIR`、またはメインのログイン + `FARMOUT_CLAUDE_MAIN=1` | 別クォータでの汎用的な作業 |
| `copilot` | [GitHub Copilot CLI](https://github.com/github/copilot-cli)、ログイン済み | コードレビュー、焦点を絞ったコーディングの質問、セカンドオピニオン。複数ベンダーのモデルを利用可能 |
| `api` | `FARMOUT_API_BASE` + `FARMOUT_API_MODEL` | 任意の OpenAI 互換モデルによる大量作業や単純作業 |

```bash
git clone https://github.com/JayPokale/farmout ~/.local/share/farmout
claude mcp add -s user farmout -- python3 ~/.local/share/farmout/server.py
claude mcp list        # farmout: ... ✔ Connected
```

Claude Code プラグインとしてインストールすることもできます。`claude plugin update farmout` で最新の状態を保てます。

```bash
claude plugin install farmout --marketplace JayPokale/farmout
```

プラグインのサーバーは設定を環境変数から読み込みます。そのため、プラグインを使う場合は `-e` を渡す代わりに、以下の `FARMOUT_*` 変数をシェルのプロファイルで `export` してください。インストール方法は 1 つだけにし、両方は使わないでください。

**他のヘッド。** Codex CLI と Copilot CLI も farmout を操作でき、Claude や Gemini、あるいは互いに作業を任せられます (どちらも `claude` ワーカーとの往復で動作確認済み)。

```bash
codex mcp add farmout -- python3 ~/.local/share/farmout/server.py
copilot mcp add farmout -- python3 ~/.local/share/farmout/server.py
```

Codex は MCP ツールを呼び出すたびに確認を求めます。`codex exec` で使う場合は、`~/.codex/config.toml` の `[mcp_servers.farmout]` に `default_tools_approval_mode = "approve"` を追加してください。スラッシュコマンドは Claude Code 専用です。

Claude Code を再起動してください。ワーカーは起動時に検出されます。どれが使用可能か、残りをどう有効にするかは `python3 server.py doctor` (プラグインなら `/farmout:setup`) で確認できます。`doctor --live` は、各ワーカーに短いプロンプトも送り、ログインが機能していることを確かめます。

## 使い方

### スラッシュコマンド (プラグインインストール時)

| コマンド | 内容 |
|---|---|
| `/farmout:review [--base <ref>] [focus]` | 全ワーカーが未コミットの変更 (または `<ref>` 以降のブランチ) を並列かつ読み取り専用でレビューします。Claude が指摘を統合し、各指摘を挙げたモデル数を集計して、コードと照合します |
| `/farmout:ask <question>` | 同じ質問を全ワーカーに送ります。合意点、意見の相違点、Claude の判断が得られます |
| `/farmout:race <task>` | 書き込み可能な全ワーカーが、同じ変更をそれぞれ専用の git worktree で並列に試みます。Claude が各結果に対してテストを実行してパッチを比較し、あなたが同意すれば最良のものを適用します |
| `/farmout:setup` | 使用可能なワーカーと、未設定のワーカーを有効にするための具体的な手順を表示します |

## 安全性

| ワーカー | 読み取り専用 (デフォルト) | `write: true` |
|---|---|---|
| `antigravity` | `agy -p … --mode plan` | `--mode accept-edits --sandbox` |
| `codex` | `codex exec -s read-only` | `-s workspace-write` |
| `claude` | `claude -p --tools Read,Grep,Glob,WebSearch` | `--permission-mode acceptEdits` |
| `copilot` | `copilot -p … --allow-all-tools --available-tools view rg glob web_fetch` | `apply_patch edit create` を追加。シェルは一切不可 |
| `api` | ツールなし | 該当なし |

---

この翻訳は抄訳です。設定、`delegate` ツール、フィールドノート、FAQ、ベンチマークについては、英語版の [README](../README.md) が正となります。
