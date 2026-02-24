import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # SQLite por padrão
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{(BASE_DIR / 'instance' / 'app.db').as_posix()}"
    )
    # Render/Railway às vezes mandam postgres:// (antigo). SQLAlchemy quer postgresql://
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ✅ Identidade da loja
    STORE_NAME = "NEXOR"
    STORE_TAGLINE = "A elegância que ilumina sua estação."
    TOPBAR_NOTE = "Aproveite frete grátis nas compras acima de R$299,90"
    STORE_WHATSAPP = "5531999999999"

    # ✅ Cores (luxo claro)
    PRIMARY_COLOR = "#111111"
    ACCENT_COLOR = "#B08D57"

    # ✅ Frete (usado no carrinho/checkout)
    SHIPPING_FREE_OVER = "299.90"
    SHIPPING_FLAT = "9.90"

    # Uploads
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "static" / "uploads"))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8MB

    # Pagamentos (opcional)
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "sk_test_51T4Ma41MtKdLooO6PFfBAkrqzQnGkd18Vij5HejZnA8DLCluISP5vkk3fzGXMawBQeGQYElswb4D1pQchgdV3z6R009qH3PSKl")
    STRIPE_PUBLIC_KEY = os.environ.get("STRIPE_PUBLIC_KEY", "pk_test_51T4Ma41MtKdLooO65fRJ3z1x90Bjk0B2Q77Xam4YnfcSEhs1dxi80AkBt9M3KTEEDSXHrwmUshwC92uSQFU6KAlG00kXcW1596")
    MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")