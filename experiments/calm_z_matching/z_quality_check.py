#!/usr/bin/env python3
"""z変換品質チェック — 日本語テキストの言い換えがz空間で近傍に落ちるか確認."""
from __future__ import annotations

import json
import numpy as np
import torch

torch.manual_seed(42)
np.random.seed(42)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def main() -> None:
    from calm_encoder.configuration_autoencoder import AutoencoderConfig
    from calm_encoder.modeling_autoencoder import Autoencoder
    from transformers import AutoTokenizer
    from huggingface_hub import hf_hub_download
    import safetensors.torch

    # --- モデルロード ---
    model_id = "cccczshao/CALM-Autoencoder"
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    config_path = hf_hub_download(model_id, "config.json")
    with open(config_path) as f:
        cfg_dict = json.load(f)
    config = AutoencoderConfig(**{
        k: v for k, v in cfg_dict.items()
        if k in AutoencoderConfig.__init__.__code__.co_varnames
    })
    model = Autoencoder(config)
    weights_path = hf_hub_download(model_id, "model.safetensors")
    state_dict = safetensors.torch.load_file(weights_path)
    model.load_state_dict(state_dict, strict=False)
    model.eval()

    patch_size = config.patch_size
    latent_size = config.latent_size

    def encode_pooled(text: str) -> np.ndarray:
        tokens = tokenizer.encode(text, add_special_tokens=False)
        if not tokens:
            return np.zeros(latent_size, dtype=np.float32)
        remainder = len(tokens) % patch_size
        if remainder:
            tokens += [tokenizer.pad_token_id or 0] * (patch_size - remainder)
        with torch.no_grad():
            ids = torch.tensor([tokens], dtype=torch.long)
            latent = model.encoder(ids)
            mean = latent[:, :, :latent_size]
        return mean.squeeze(0).mean(axis=0).cpu().numpy()

    # --- テスト1: 日本語トークン化の粒度 ---
    print("=" * 60)
    print("テスト1: 日本語トークン化の粒度")
    print("=" * 60)
    test_texts = [
        "PoRは共鳴度であり誠実性の十分条件ではない",
        "AIの透明性と説明責任",
        "機械学習モデルの過学習を防ぐ方法",
        "Hello, world!",
    ]
    for text in test_texts:
        tokens = tokenizer.encode(text, add_special_tokens=False)
        decoded = [tokenizer.decode([t]) for t in tokens]
        n_patches = (len(tokens) + patch_size - 1) // patch_size
        print(f"\n  '{text}'")
        print(f"  → {len(tokens)} tokens, {n_patches} patches")
        print(f"  → tokens: {decoded[:20]}{'...' if len(decoded) > 20 else ''}")

    # --- テスト2: 同一命題の言い換えペア ---
    print("\n" + "=" * 60)
    print("テスト2: 言い換えペアのz空間距離")
    print("=" * 60)
    paraphrase_pairs = [
        ("PoRは誠実性の十分条件ではない", "PoR単体では誠実性を保証しない"),
        ("AIの透明性が重要である", "AIは透明であるべきだ"),
        ("複合評価が必要", "単一指標では不十分で多面的評価を要する"),
        ("過学習を防ぐ", "オーバーフィッティングを回避する"),
    ]
    unrelated_pairs = [
        ("PoRは誠実性の十分条件ではない", "天気予報は晴れです"),
        ("AIの透明性が重要である", "猫が机の上にいる"),
        ("複合評価が必要", "来月の予定を確認する"),
    ]

    print("\n  言い換えペア（近いはず）:")
    for a, b in paraphrase_pairs:
        za, zb = encode_pooled(a), encode_pooled(b)
        sim = cosine_sim(za, zb)
        print(f"    cos={sim:.4f}  '{a}' ↔ '{b}'")

    print("\n  無関係ペア（遠いはず）:")
    for a, b in unrelated_pairs:
        za, zb = encode_pooled(a), encode_pooled(b)
        sim = cosine_sim(za, zb)
        print(f"    cos={sim:.4f}  '{a}' ↔ '{b}'")

    # --- テスト3: 英語 vs 日本語の同一概念 ---
    print("\n" + "=" * 60)
    print("テスト3: 英日同一概念の距離")
    print("=" * 60)
    cross_pairs = [
        ("transparency of AI", "AIの透明性"),
        ("overfitting prevention", "過学習の防止"),
        ("composite evaluation is necessary", "複合評価が必要"),
    ]
    for a, b in cross_pairs:
        za, zb = encode_pooled(a), encode_pooled(b)
        sim = cosine_sim(za, zb)
        print(f"    cos={sim:.4f}  '{a}' ↔ '{b}'")


if __name__ == "__main__":
    main()
