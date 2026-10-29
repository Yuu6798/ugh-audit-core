# 開発への参加

不具合修正、テスト、ドキュメントの改善を歓迎します。製品の概要は
[README](README.md)、判定の契約は
[意味照合仕様](docs/semantic_matching_v2.md)、実装上の規約は
[CLAUDE.md](CLAUDE.md) を参照してください。

## Fork から PR まで

1. [リポジトリ](https://github.com/Yuu6798/ugh-audit-core) を自分のアカウントに Fork します。
2. 以下の `YOUR_ACCOUNT` を自分のアカウント名に置き換え、作業ブランチを作成します。

   ```bash
   git clone https://github.com/YOUR_ACCOUNT/ugh-audit-core.git
   cd ugh-audit-core
   git remote add upstream https://github.com/Yuu6798/ugh-audit-core.git
   git fetch upstream
   git switch -c claude/fix-summary upstream/main
   ```

3. 変更と検証を行い、`fix:`、`docs:` などの接頭辞でコミットします。日本語でも構いません。
4. `git push -u origin claude/fix-summary` で自分の Fork に push し、GitHub で元リポジトリの `main` 宛てに PR を作成します。

PR には解決する問題、変更後の動作、実行した検証と結果を書いてください。
関連 Issue があればリンクし、未実施の検証や残る制約も記載します。
挙動の変更には、その問題を再現できるテストを添えてください。

## 開発環境

Git と Python 3.10～3.12（CI の対象）が必要です。以下は Python 3.12 の例です。
リポジトリのルートで実行します。

**Bash**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

**PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

PowerShell でスクリプトの有効化が許可されていない場合は、有効化を省略し、
以降の `python` を `.\.venv\Scripts\python.exe` に置き換えて実行できます。

## 変更の確認

```bash
python -m ruff check .
python scripts/doc_consistency_check.py --strict
python -m pytest -q --tb=short
```

回帰テストに本番の API キーや招待トークンは不要で、外部のモデル API は呼びません。
[tests/conftest.py](tests/conftest.py) が外部ネットワークを制限し、
認証や意味照合のテストには合成データと制御された応答を使います。
通常のテストのために `.env` や実環境の認証情報を用意しないでください。
`.[dev]` には SBert が含まれず、その依存が必要なテストはスキップされます。

### SBert を含める場合（任意）

SBert 関連の変更では、追加依存と固定モデルを準備して確認します。
依存のインストールと初回のモデル取得には外部ネットワークとディスク容量を使います。
モデルの取得・ローカル推論には、外部のモデル API のキーは不要です。

```bash
python -m pip install -e ".[dev,full]"
python scripts/prepare_sbert_cache.py --cache .cache/ugh-hf-cache
```

取得後は、同じキャッシュを指定してオフラインでテストします。

**Bash**

```bash
export HF_HOME="$PWD/.cache/ugh-hf-cache"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
python -m pytest -q --tb=short -rs
```

**PowerShell**

```powershell
$env:HF_HOME = Join-Path $PWD ".cache/ugh-hf-cache"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
python -m pytest -q --tb=short -rs
```

PR の検証条件は [.github/workflows/ci.yml](.github/workflows/ci.yml) が基準です。
Python の複数バージョン、SBert を含むテストのほか、README の行数、
配布パッケージ、保存済み評価資料も検証します。ローカルの結果とあわせて CI を確認してください。

## ドキュメントと不具合報告

新しい `docs/*.md` を追加したら、README と CLAUDE.md の両方のドキュメント索引に
リンクを追加します。仕様変更では利用者向けの説明・例も更新し、
`doc_consistency_check.py --strict` を実行してください。

不具合は [Issues](https://github.com/Yuu6798/ugh-audit-core/issues) へ、
対象コミット、OS・Python バージョン、導入した追加依存、再現手順、期待した結果と
実際の結果を添えて報告してください。入力は最小限の合成データに置き換え、
API キー、招待トークン、認証ヘッダー、環境ファイル、本番の DB・個人情報を含めないでください。

本リポジトリは [MIT License](LICENSE) で公開しています。
追加するコードや文書も、このライセンスで提供できるものを提出してください。
