# `paper/` — NLP2027 投稿論文 作業ディレクトリ

言語処理学会 第 33 回年次大会 (NLP2027, 2027 年 3 月予定) への投稿論文の
LaTeX ソース。

## 投稿先と locked-in 設定

| 項目 | 決定 |
|---|---|
| Venue | NLP2027 (言語処理学会 年次大会) |
| 投稿言語 | 和文 |
| 貢献 (1 文) | PoR/ΔE による LLM 出力評価フレームワークと HA48 における予備検証 |
| 著者表記 | 査読中は anonymize、最終調整時に置換 |
| ページ数 | 8 ページ (年次大会標準) |

詳細な戦略は `.claude/memory/` の関連サマリーを参照。

## ディレクトリ構成

```
paper/
├── README.md            # この文書
├── main.tex             # エントリポイント
├── references.bib       # 参考文献
├── sections/            # 各 section の独立ファイル
│   ├── abstract.tex
│   ├── introduction.tex
│   ├── related_work.tex
│   ├── method.tex
│   ├── experiments.tex
│   ├── discussion.tex
│   └── conclusion.tex
└── figures/             # 図表 (PDF / PNG)
```

## ビルド

```bash
cd paper/
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

または `latexmk -pdf main.tex`。

## TODO の追跡規約

各 section ファイルの先頭コメントに段階別 TODO を記載:

- `TODO[(a)]` — skeleton フェーズ (本コミットで完了)
- `TODO[(d)]` — Abstract / 貢献文 確定
- `TODO[(b)]` — Related Work survey
- `TODO[(c)]` — Method 数式転写
- `TODO[NLP2027-template]` — 公式テンプレート公開後の置換
- `TODO[最終調整]` — 投稿直前の最終調整 (著者情報、謝辞、Data Availability)

## NLP2027 公式テンプレートへの移行

現状は `jsarticle` ベースの暫定構成。NLP2027 CFP (2026 年秋頃公開予定)
で公式 LaTeX テンプレートが配布された時点で、`main.tex` の `documentclass`
と `usepackage` を置換する。section ファイル (`sections/*.tex`) は
基本的にそのまま再利用可能な構成にしてある。

## 査読中匿名化チェックリスト

投稿前に以下を確認:

- [ ] `main.tex` の `\author{...}` が Anonymous のまま
- [ ] 本文中に `ugh-audit-core` リポジトリ URL を記載していない
- [ ] 本文中に GitHub username (`Yuu6798` 等) を記載していない
- [ ] 図表のメタデータに作者名が含まれていない
- [ ] BibTeX 内の self-citation が `Anonymous et al.` 形式になっている
