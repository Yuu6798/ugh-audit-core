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
No hosted-service entitlement is included. The listing is a preparation draft;
the real endpoint and supported client must be verified before publication.

**Cost:** The skill is free on Agensi. Model calls are billed to your own Anthropic
API account, including audits with explicit requirements. Hosting costs are borne
by the server operator. No model credits or operator-funded API use are included.
Access is invitation based and subject to quotas; this is not a promise of
unlimited free API use. No outside paid upgrade or checkout is
offered by this listing.

**Compatibility:** English skill instructions; Japanese inputs and outputs are
supported. Both invite-token and BYOK secret-header configuration are required.
Agensi-connected client support for these headers has not been verified. OAuth is
not implemented by the audit server; general ChatGPT connector compatibility is
not established. Importing the skill does not configure either credential.

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
tool引数へ貼り付けません。実際のBYOK配備・対象クライアントの接続確認が掲載前に必要です。

**料金:** Agensiのスキルは無料です。明示要件の監査にもモデル呼出があり、そのAPI料金は
利用者自身のAnthropicアカウントに発生します。運営者がモデル料金を負担する無料API枠は
含みません。サーバー運営費は提供者負担です。招待と利用上限があり、無制限利用は約束しません。

**互換性:** 対応BYOKプロバイダーはAnthropicです。Agensi経由エージェントの秘密ヘッダー
対応は未確認です。OAuth未実装で、一般ChatGPTコネクタ対応も保証しません。

## Creator checks before submission

- Replace preparation status only after recording an actual endpoint and supported
  client check for both secret headers. Put the external service, invitation and
  user-owned Anthropic API key requirement in the listing's Needs access to field.
- Confirm with Agensi that the declared external invitation dependency is accepted;
  it must not be represented as an off-platform skill unlock or licence sale.
- Set Free explicitly. Do not add a separate refund promise, external checkout,
  compatibility badge or accuracy guarantee.
- Read [rights-and-withdrawal.md](rights-and-withdrawal.md), retain the MIT notice,
  and use the deterministic builder described in [package-notes.md](package-notes.md).

Official basis checked 2026-10-29 (JST): [selling formats and external servers](https://www.agensi.io/docs/what-you-can-sell),
[free and open-source listings](https://www.agensi.io/docs/creator-faq),
[terms](https://www.agensi.io/terms). These are drafting references, not confirmation
that this listing has been accepted or published.
