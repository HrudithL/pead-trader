# panel_model.py
# Model for panel/sequence option data: main series + K other series with masks.
import math
import torch
import torch.nn as nn

# -------- Positional encoding (sinusoidal) --------
class SinusoidalPositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 2000):
        super().__init__()
        pe = torch.zeros(max_len, d_model, dtype=torch.float32)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0))  # [1, max_len, d_model]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [B, L, D]
        L = x.size(1)
        return x + self.pe[:, :L, :]

# -------- Context Attention over K options per time step --------
class ContextAttention(nn.Module):
    """
    For each time step t:
      - Query = projected main features: q_t
      - Keys/Values = projected others features across K: k_tj, v_tj
      - Mask M[B, L, K]: 1 if present, 0 if missing -> masked softmax
    Outputs a context vector per time step.
    """
    def __init__(self, main_dim: int, other_dim: int, d_model: int, n_heads: int = 4, dropout: float = 0.15):
        super().__init__()
        assert d_model % n_heads == 0
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads

        self.q_proj = nn.Linear(main_dim, d_model)
        self.k_proj = nn.Linear(other_dim, d_model)
        self.v_proj = nn.Linear(other_dim, d_model)
        self.o_proj = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x_main: torch.Tensor, x_other: torch.Tensor, m_other: torch.Tensor) -> torch.Tensor:
        """
        x_main:  [B, L, Fm]
        x_other: [B, L, K, Fo]
        m_other: [B, L, K]  (1=present, 0=missing)
        returns: [B, L, D]
        """
        # --- sanitize inputs (defensive) ---
        x_main = torch.nan_to_num(x_main, nan=0.0, posinf=0.0, neginf=0.0)
        x_other = torch.nan_to_num(x_other, nan=0.0, posinf=0.0, neginf=0.0)
        if m_other is not None:
            x_other = x_other * m_other.unsqueeze(-1)  # zero out missing others

        B, L, K, Fo = x_other.shape
        # Project to model dims
        Q = self.q_proj(x_main)                  # [B, L, D]
        Kmat = self.k_proj(x_other)              # [B, L, K, D]
        Vmat = self.v_proj(x_other)              # [B, L, K, D]

        # Reshape for multi-head: (B, L, H, ?, d_head)
        H, Dh = self.n_heads, self.d_head
        Q = Q.view(B, L, H, Dh).transpose(1, 2)              # [B, H, L, Dh]
        Kmat = Kmat.view(B, L, K, H, Dh).permute(0, 3, 1, 2, 4)  # [B, H, L, K, Dh]
        Vmat = Vmat.view(B, L, K, H, Dh).permute(0, 3, 1, 2, 4)  # [B, H, L, K, Dh]

        # Attention scores: (q * k^T) / sqrt(d)
        scores = torch.einsum("BHLD,BHLKD->BHLK", Q, Kmat) / math.sqrt(Dh)

        # Apply mask: where m_other==0, set to large negative
        if m_other is not None:
            mask = (m_other > 0).unsqueeze(1)  # [B, 1, L, K]
            scores = scores.masked_fill(~mask, float("-inf"))

        attn = torch.softmax(scores, dim=-1)  # [B, H, L, K]
        attn = self.drop(attn)

        # Weighted sum of values -> [B, H, L, Dh]
        ctx = torch.einsum("BHLK,BHLKD->BHLD", attn, Vmat)
        # Merge heads -> [B, L, D]
        ctx = ctx.transpose(1, 2).contiguous().view(B, L, H * Dh)
        return self.o_proj(ctx)

# -------- Main Model --------
class PanelForecastNet(nn.Module):
    def __init__(
        self,
        main_feat_dim: int,
        other_feat_dim: int,
        d_model: int = 128,
        n_heads: int = 4,
        n_layers: int = 3,
        dropout: float = 0.15,
        ff_mult: int = 4,
        use_mean_pool: bool = False,
    ):
        super().__init__()
        self.use_mean_pool = use_mean_pool

        self.main_in = nn.Sequential(
            nn.Linear(main_feat_dim, d_model),
        )
        self.ctx = ContextAttention(main_dim=main_feat_dim, other_dim=other_feat_dim, d_model=d_model, n_heads=n_heads, dropout=dropout)
        self.fuse = nn.Linear(2 * d_model, d_model)

        self.pos = SinusoidalPositionalEncoding(d_model)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=ff_mult * d_model,
            dropout=dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=n_layers)

        self.head = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, 1),
        )

    def forward(self, x_main: torch.Tensor, x_others: torch.Tensor, m_others: torch.Tensor) -> torch.Tensor:
        # sanitize (defensive)
        x_main = torch.nan_to_num(x_main, nan=0.0, posinf=0.0, neginf=0.0)
        x_others = torch.nan_to_num(x_others, nan=0.0, posinf=0.0, neginf=0.0)
        if m_others is not None:
            x_others = x_others * m_others.unsqueeze(-1)

        main_emb = self.main_in(x_main)                # [B, L, D]
        ctx_emb = self.ctx(x_main, x_others, m_others) # [B, L, D]
        z = torch.cat([main_emb, ctx_emb], dim=-1)     # [B, L, 2D]
        z = self.fuse(z)                               # [B, L, D]
        z = self.pos(z)
        z = self.encoder(z)                            # [B, L, D]

        z_pool = z.mean(dim=1) if self.use_mean_pool else z[:, -1, :]
        y_hat = self.head(z_pool).squeeze(-1)
        return y_hat
