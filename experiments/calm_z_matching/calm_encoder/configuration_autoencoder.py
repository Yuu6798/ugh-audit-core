# coding=utf-8
# From https://github.com/shaochenze/calm (MIT License)
# Original: Copyright 2022 EleutherAI and the HuggingFace Inc. team.
from transformers.configuration_utils import PretrainedConfig


class AutoencoderConfig(PretrainedConfig):
    model_type = "autoencoder"

    def __init__(
        self,
        ae_dropout=0.15,
        kl_clamp=0.5,
        kl_weight=1e-3,
        patch_size=4,
        vocab_size=32000,
        hidden_size=512,
        intermediate_size=1280,
        num_encoder_layers=2,
        num_decoder_layers=2,
        latent_size=128,
        hidden_act="silu",
        max_position_embeddings=2048,
        initializer_range=0.02,
        rms_norm_eps=1e-6,
        pad_token_id=None,
        bos_token_id=1,
        eos_token_id=2,
        pretraining_tp=1,
        tie_word_embeddings=False,
        mlp_bias=False,
        num_attention_heads=8,
        num_key_value_heads=8,
        attention_bias=False,
        attention_dropout=0.0,
        **kwargs,
    ):
        self.ae_dropout = ae_dropout
        self.kl_clamp = kl_clamp
        self.kl_weight = kl_weight
        self.patch_size = patch_size
        self.vocab_size = vocab_size
        self.latent_size = latent_size
        self.max_position_embeddings = max_position_embeddings
        self.hidden_size = hidden_size
        self.intermediate_size = intermediate_size
        self.num_encoder_layers = num_encoder_layers
        self.num_decoder_layers = num_decoder_layers
        self.num_attention_heads = num_attention_heads
        self.num_key_value_heads = num_key_value_heads
        self.hidden_act = hidden_act
        self.initializer_range = initializer_range
        self.rms_norm_eps = rms_norm_eps
        self.pretraining_tp = pretraining_tp
        self.mlp_bias = mlp_bias
        self.attention_bias = attention_bias
        self.attention_dropout = attention_dropout

        super().__init__(
            pad_token_id=pad_token_id,
            bos_token_id=bos_token_id,
            eos_token_id=eos_token_id,
            tie_word_embeddings=tie_word_embeddings,
            **kwargs,
        )
