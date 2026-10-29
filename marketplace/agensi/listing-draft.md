# Free listing draft — not submitted

Listing type: single skill. Price: Free ($0). Suggested category: Code Quality &
Review or Business & Operations. Suggested tags: Japanese, instruction coverage,
MCP, draft review. Name availability and marketplace approval have not been checked.

## English

**Title:** UGH Instruction Audit — Japanese requirement coverage

**Summary:** Check whether a short Japanese draft communicates your explicit
instructions, with requirement-level evidence and missing or contradictory wording.

**Description:** This skill guides your agent through a separately connected UGH
Audit MCP service. Supply a draft and a small list of human-reviewed requirements.
The result distinguishes communicated requirements, confirmed omissions or
contradictions, and unresolved assessments. It helps you decide what to revise;
it does not certify facts, general writing quality, real-world task execution, or
the completeness of the requirement list. Human review remains the final decision.

The pilot starts with short Japanese operational instructions and 2–5 reviewed
requirements. The saved synthetic study is not independent human validation or a
guarantee of future accuracy. Numerical legacy diagnostics do not control approval.

**Needs access to:** An independently operated UGH Audit HTTPS MCP endpoint, a
per-user invitation token, your own Anthropic API account/key (BYOK), and an agent
that supports Streamable HTTP with Authorization and custom secret HTTP headers.
Every audit sends your provider key as `X-UGH-Anthropic-Key`; never put it in chat
or tool arguments. Agensi provides the skill instructions, not the server or its credentials.
No hosted-service entitlement is included. The listing remains a preparation
draft on publication hold; the verified direct Codex connection is described below.

**Cost:** The skill is free on Agensi. Model calls are billed to your own Anthropic
API account, including audits with explicit requirements. Hosting costs are borne
by the server operator. No model credits or operator-funded API use are included.
Access is invitation based and subject to quotas; this is not a promise of
unlimited free API use. No outside paid upgrade or checkout is
offered by this listing.

**Data and deletion:** The question, draft and requirements reach the audit
operator; the requirements and draft also reach Anthropic for assessment. Your
provider key passes through the operator to Anthropic but is not saved to audit
history or application logs. Resolved audits, including their text and evidence,
may be saved. Read the operator's policy before supplying confidential material.
The creator-operated invitation beta's proposed terms are 30-day audit retention,
deletion within seven days after identity verification, and removal from backups
within 30 days after deletion. Automated retention is enabled and its initial
production cleanup has passed; manual request handling and the public effective
date still await acceptance. These terms do not describe every independently
hosted server. The creator-operated beta's
private support and deletion contact is [kkoo6798@gmail.com](mailto:kkoo6798@gmail.com).
Ordinary mailbox send/receive evidence is confirmed. Manual deletion intake, identity
checks, replies and the responsible operator still await acceptance, so invitations
and publication remain on hold. Do not email API keys,
invitation tokens or full audit text. Independently hosted services use their own
operator's contact. See
[data handling and deletion](../../docs/data-policy.md) and the
[release checklist](../../docs/beta-release-checklist.md).

**Compatibility:** English skill instructions; Japanese inputs and outputs are
supported. Both invite-token and BYOK secret-header configuration are required.
Codex CLI's direct MCP connection completed one synthetic production audit with two
real Anthropic calls and no retries; history retrieval, owner isolation and text/CSV
reports passed. Agensi skill delivery, automatic agent skill selection and real-model
requirement auto-generation remain unverified. OAuth is
used for Agensi sign-in and is separate from UGH's invitation and BYOK headers.
The audit server does not implement OAuth; general ChatGPT connector compatibility
is not established. Importing the skill does not configure either credential.

**Example task:** "Check whether this Japanese delivery notice communicates these
three requirements. Show the evidence, missing instructions and suggested edits."

## 日本語

**名称:** UGH Instruction Audit — 日本語の指示要件チェック

**概要:** 短い日本語の案内文が、明示した指示・制約を伝えているかを要件別に点検します。
根拠、欠落、矛盾、確認不能を区別し、修正の判断を支援します。

**対象:** 人が確認した2〜5件の要件と短い日本語の業務案内文。事実の正確さ、文章全体の
品質、業務の実行、要件抽出の網羅性を保証する機能ではありません。最終判断は人が行います。

**利用条件:** Agensi経由のエージェントでスキルを読み込み、別途用意された外部UGH Audit
MCPサーバーへ接続します。利用者向けのZIPダウンロードは不要です。外部サーバーの招待
トークン、利用者自身のAnthropic APIキー（BYOK）、秘密HTTPヘッダーを設定できる
クライアントが必要です。APIキーは `X-UGH-Anthropic-Key` ヘッダーに設定し、チャットや
tool引数へ貼り付けません。Codex CLIの直接接続は下記の範囲で確認済みですが、掲載は保留中です。

**料金:** Agensiのスキルは無料です。明示要件の監査にもモデル呼出があり、そのAPI料金は
利用者自身のAnthropicアカウントに発生します。運営者がモデル料金を負担する無料API枠は
含みません。サーバー運営費は提供者負担です。招待と利用上限があり、無制限利用は約束しません。

**データと削除:** 質問・回答・要件は監査運営者へ送られ、意味照合の要件・回答は
Anthropicへ送られます。APIキーも運営者を経由しますが、監査履歴やアプリのログには
保存しません。判定できた監査の本文・根拠は保存される場合があります。提供者の招待制
ベータは、監査履歴30日、本人確認後7日以内の削除、削除後30日以内のバックアップ消去
を運営案としています。自動削除は有効化され、本番の初回清掃を確認済みです。手動の依頼対応と
対外的な適用開始日は受入待ちです。独自運営のすべてのサーバーへの保証ではありません。
提供者のベータの非公開問い合わせ・削除依頼先は
[kkoo6798@gmail.com](mailto:kkoo6798@gmail.com)です。通常メールの送受信実績は確認済みです。
削除依頼の受付・本人確認・返信手順と担当者の受入が完了するまで、招待開始・掲載は保留します。
キー・招待トークン・監査本文全体はメールへ記載しないでください。独自運営の場合はその運営者の窓口を使います。
[保存・削除方針](../../docs/data-policy.md)を確認してからデータを送ってください。

**互換性:** 対応BYOKプロバイダーはAnthropicです。Codex CLIの直接MCP接続で、実モデルを
使った合成監査1件（Anthropic呼出2回・再試行なし）、本人履歴の再取得、別利用者の取得拒否、
text/CSVの整合を確認済みです。Agensi経由の配信、エージェントの自動スキル選択、実モデルによる
要件自動生成は未確認です。AgensiへのOAuthログインと、UGHへの招待認証・BYOK設定は別です。
UGHサーバーはOAuth未実装で、一般ChatGPTコネクタ対応も保証しません。

## Creator checks before submission

- Codex CLI is the selected client; direct MCP checks and one real synthetic
  model audit passed. Automatic agent skill selection remains untested.
  Agensi serves only live listings: record the OAuth → `get_skill` →
  UGH audit end-to-end check separately after publication. Do not claim that
  post-publication check has passed based on local tests or a ZIP build.
  [Agensi creator FAQ](https://www.agensi.io/docs/creator-faq).
- Keep the recorded endpoint and Codex checks scoped to direct MCP use; they do not
  establish Agensi delivery. Put the external service, invitation and
  user-owned Anthropic API key requirement in the listing's Needs access to field.
- Disclose the external invitation dependency during Agensi's listing review;
  it must not be represented as an off-platform skill unlock or licence sale.
  The creator-received support guidance permits free listings linked to a
  separately operated MCP server when the advertised function and prerequisites
  are accurate and setup templates/instructions are bundled. It does not establish
  approval of this particular listing.
- Complete the [release checklist](../../docs/beta-release-checklist.md), including
  acceptance of manual deletion intake, identity checks, replies, the responsible
  operator, records outside automated cleanup, the public policy effective date and
  the creator's publication decision. Initial production cleanup and the approved
  real-BYOK audit are recorded there. Keep submission and publication
  on hold while those conditions are unresolved. Delisting does not establish
  immediate revocation of existing users' Agensi access.
- Set Free explicitly. Do not add a separate refund promise, external checkout,
  compatibility badge or accuracy guarantee.
- Read [rights-and-withdrawal.md](rights-and-withdrawal.md), retain the MIT notice,
  and use the deterministic builder described in [package-notes.md](package-notes.md).

Official basis checked 2026-10-29 (JST): [selling formats and external servers](https://www.agensi.io/docs/what-you-can-sell),
[free and open-source listings](https://www.agensi.io/docs/creator-faq),
[terms](https://www.agensi.io/terms). These are drafting references, not confirmation
that this listing has been accepted or published.
