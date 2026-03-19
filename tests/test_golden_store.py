"""
tests/test_golden_store.py
GoldenStore の日本語マッチングテスト
"""
from ugh_audit.reference.golden_store import GoldenStore, GoldenEntry


def _empty_store(tmp_path) -> GoldenStore:
    """初期データなしの空ストアを返す"""
    store = GoldenStore(path=tmp_path / "golden.json")
    # 初期データをクリアしてテスト用エントリのみにする
    store._store.clear()
    return store


def test_find_reference_exact_substring(tmp_path):
    store = _empty_store(tmp_path)
    store.add("test", GoldenEntry(
        question="AIは意味を持てるか",
        reference="AIは意味と共振する",
        source="test",
    ))
    # 部分一致
    ref = store.find_reference("AIは意味を持てるか？という問い")
    assert ref == "AIは意味と共振する"


def test_find_reference_bigram(tmp_path):
    store = _empty_store(tmp_path)
    store.add("por", GoldenEntry(
        question="PoRとは何か",
        reference="意味の発火点",
        source="test",
    ))
    # bigram マッチング
    ref = store.find_reference("PoRとは何ですか")
    assert ref == "意味の発火点"


def test_find_reference_no_match(tmp_path):
    store = _empty_store(tmp_path)
    # 空ストアでは完全に無関係な質問は None
    ref = store.find_reference("全く関係のない質問xyz")
    assert ref is None


def test_find_reference_empty(tmp_path):
    store = _empty_store(tmp_path)
    assert store.find_reference("") is None
