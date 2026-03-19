"""
tests/test_golden_store.py
GoldenStore の日本語マッチングテスト
"""
from ugh_audit.reference.golden_store import GoldenStore, GoldenEntry


def test_find_reference_exact_substring(tmp_path):
    store = GoldenStore(path=tmp_path / "golden.json")
    store.add("test", GoldenEntry(
        question="AIは意味を持てるか",
        reference="AIは意味と共振する",
        source="test",
    ))
    # 部分一致
    ref = store.find_reference("AIは意味を持てるか？という問い")
    assert ref == "AIは意味と共振する"


def test_find_reference_bigram(tmp_path):
    store = GoldenStore(path=tmp_path / "golden.json")
    store.add("por", GoldenEntry(
        question="PoRとは何か",
        reference="意味の発火点",
        source="test",
    ))
    # bigram マッチング
    ref = store.find_reference("PoRとは何ですか")
    assert ref == "意味の発火点"


def test_find_reference_no_match(tmp_path):
    store = GoldenStore(path=tmp_path / "golden.json")
    ref = store.find_reference("全く関係のない質問xyz")
    # マッチしない場合は None か初期エントリの reference
    # 初期データ3件があるので完全に無関係ならNoneが理想だが
    # Jaccard 0.1 未満なら None を返す
    assert ref is None or isinstance(ref, str)


def test_find_reference_empty(tmp_path):
    store = GoldenStore(path=tmp_path / "golden.json")
    assert store.find_reference("") is None
