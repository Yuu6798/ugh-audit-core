"""
tests/test_delta_e_variants.py
ΔE 3パターン計算のテスト
"""
from ugh_audit.scorer import UGHScorer
from ugh_audit.scorer.models import AuditResult


def test_delta_e_three_variants_exist():
    """AuditResultに3種のΔEフィールドが存在することを確認"""
    s = UGHScorer()
    result = s.score(
        question="テスト質問",
        response="テスト回答です。これは二文目。これは三文目。これは四文目。",
        reference="テストの参照回答全文です。核心を含む。",
        reference_core="テストの核心",
    )
    assert hasattr(result, "delta_e_core")
    assert hasattr(result, "delta_e_full")
    assert hasattr(result, "delta_e_summary")
    assert result.delta_e == result.delta_e_full  # プライマリ = full


def test_delta_e_defaults_zero_on_minimal():
    """minimal backendでは全ΔEが0.0"""
    s = UGHScorer()
    if s.backend != "minimal":
        return  # minimal以外ではスキップ
    result = s.score(
        question="Q", response="R",
        reference="ref", reference_core="core",
    )
    assert result.delta_e_core == 0.0
    assert result.delta_e_full == 0.0
    assert result.delta_e_summary == 0.0


def test_delta_e_fields_in_dataclass():
    """AuditResult dataclass に新フィールドが正しく設定される"""
    r = AuditResult(
        question="Q", response="R",
        delta_e=0.15,
        delta_e_core=0.20,
        delta_e_full=0.15,
        delta_e_summary=0.10,
    )
    assert r.delta_e == 0.15
    assert r.delta_e_core == 0.20
    assert r.delta_e_full == 0.15
    assert r.delta_e_summary == 0.10


def test_score_with_reference_core_param():
    """score() が reference_core パラメータを受け取れることを確認"""
    s = UGHScorer()
    result = s.score(
        question="AIは意味を持てるか？",
        response="AIは意味を処理できます。",
        reference="AIは意味位相空間で共振する動的プロセスである。",
        reference_core="共振する動的プロセス",
    )
    assert isinstance(result, AuditResult)
    assert 0.0 <= result.delta_e_core <= 1.0
    assert 0.0 <= result.delta_e_full <= 1.0
    assert 0.0 <= result.delta_e_summary <= 1.0


def test_extract_head_sentences():
    """_extract_head_sentences が先頭3文を正しく抽出する"""
    head = UGHScorer._extract_head_sentences("最初。二番目。三番目。四番目。")
    assert head == "最初。二番目。三番目。"

    head2 = UGHScorer._extract_head_sentences("一文だけ")
    assert head2 == "一文だけ"


def test_extract_head_sentences_english():
    """英語の文境界（.?!）にも対応する"""
    head = UGHScorer._extract_head_sentences("First. Second. Third. Fourth.")
    assert head == "First. Second. Third."

    head2 = UGHScorer._extract_head_sentences("What is AI? It processes meaning. Really!")
    assert head2 == "What is AI? It processes meaning. Really!"
