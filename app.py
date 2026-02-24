import os
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import requests
import stripe

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, abort, send_from_directory, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user, UserMixin
)
from flask_wtf import FlaskForm
from slugify import slugify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from wtforms import (
    StringField, PasswordField, BooleanField, TextAreaField,
    DecimalField, IntegerField, FileField, SelectField
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"


# -------------------------
# Helpers
# -------------------------
def now_utc():
    return datetime.now(timezone.utc)

def money(v) -> Decimal:
    if v is None:
        return Decimal("0.00")
    if isinstance(v, Decimal):
        return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def format_brl(v: Decimal) -> str:
    v = money(v)
    s = f"{v:.2f}"
    inteiro, dec = s.split(".")
    inteiro = re.sub(r"(?<!^)(?=(\d{3})+$)", ".", inteiro)
    return f"R$ {inteiro},{dec}"

def allowed_file(filename: str) -> bool:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return ext in {"png", "jpg", "jpeg", "webp"}

def secure_upload_name(prefix: str, filename: str) -> str:
    safe = secure_filename(filename)
    return f"{prefix}-{safe}"

def wa_link(phone: str, message: str) -> str:
    phone = re.sub(r"\D+", "", phone or "")
    return f"https://wa.me/{phone}?text={quote(message)}"


# -------------------------
# Cart (drawer)
# session["cart"] = {"<pid>:<size>": qty}
# -------------------------
def cart_get():
    return session.get("cart", {})

def cart_save(cart: dict):
    session["cart"] = cart
    session.modified = True

def cart_key(product_id: int, size: str | None):
    s = (size or "").strip().upper()
    return f"{int(product_id)}:{s}"

def cart_split_key(key: str):
    if ":" not in key:
        return int(key), ""
    pid, size = key.split(":", 1)
    return int(pid), (size or "").strip().upper()

def cart_count(cart: dict) -> int:
    return sum(int(q) for q in cart.values())

def cart_products_map(cart: dict):
    if not cart:
        return {}
    pids = []
    for k in cart.keys():
        pid, _ = cart_split_key(k)
        pids.append(pid)
    products = Product.query.filter(Product.id.in_(pids), Product.is_active.is_(True)).all()
    return {p.id: p for p in products}

def cart_subtotal(cart: dict) -> Decimal:
    if not cart:
        return money("0")
    pmap = cart_products_map(cart)
    total = money("0")
    for k, qty in cart.items():
        pid, _ = cart_split_key(k)
        p = pmap.get(pid)
        if not p:
            continue
        total += money(p.price) * int(qty)
    return money(total)

def get_setting(key: str, default: str = "") -> str:
    s = Setting.query.filter_by(key=key).first()
    if not s or s.value is None:
        return default
    return str(s.value)

def get_setting_decimal(key: str, default: str) -> Decimal:
    try:
        return money(get_setting(key, default))
    except Exception:
        return money(default)

def shipping_calc(subtotal: Decimal):
    # Pode ser melhorado com Correios/Melhor Envio depois
    free_over = get_setting_decimal("shipping_free_over", "299.90")
    flat = get_setting_decimal("shipping_flat", "9.90")
    if subtotal >= free_over:
        return money("0")
    return money(flat)

def cart_payload(app: Flask):
    cart = cart_get()
    pmap = cart_products_map(cart)
    items = []

    for k, qty in cart.items():
        pid, size = cart_split_key(k)
        p = pmap.get(pid)
        if not p:
            continue
        unit = money(p.price)
        line = unit * int(qty)
        items.append({
            "key": k,
            "product_id": p.id,
            "name": p.name,
            "slug": p.slug,
            "size": size,
            "qty": int(qty),
            "unit_price_brl": format_brl(unit),
            "line_total_brl": format_brl(line),
            "image_url": (url_for("uploads", filename=p.image_filename) if p.image_filename else ""),
            "stock": p.stock,
        })

    subtotal = cart_subtotal(cart)
    ship = shipping_calc(subtotal)
    total = subtotal + ship

    return {
        "count": cart_count(cart),
        "items": items,
        "subtotal_brl": format_brl(subtotal),
        "shipping_brl": format_brl(ship),
        "total_brl": format_brl(total),
        "free_over_brl": format_brl(get_setting_decimal("shipping_free_over", "299.90")),
        "store_name": get_setting("store_name", "VÉRACO"),
        "currency": "BRL",
    }


# -------------------------
# Models
# -------------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_utc)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, default="")

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(160), unique=True, nullable=False, index=True)
    icon = db.Column(db.String(120), default="")  # ex: "ring", "watch", "earrings"
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_utc)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)

    name = db.Column(db.String(180), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, default="")

    price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    stock = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)

    # tamanhos: "P,M,G,GG" (opcional)
    sizes = db.Column(db.String(80), default="")

    image_filename = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=now_utc)

    category = db.relationship("Category", lazy=True)

class Banner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), default="")
    subtitle = db.Column(db.String(240), default="")
    cta_text = db.Column(db.String(60), default="Comprar agora")
    cta_link = db.Column(db.String(240), default="/produtos")
    image_filename = db.Column(db.String(255), default="")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_utc)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(40), default="Novo")  # Novo, Pagando, Pago, Separando, Enviado, Concluído, Cancelado

    customer_email = db.Column(db.String(190), default="")
    customer_name = db.Column(db.String(180), default="")
    customer_phone = db.Column(db.String(60), default="")

    cep = db.Column(db.String(20), default="")
    address = db.Column(db.Text, default="")
    notes = db.Column(db.Text, default="")

    subtotal = db.Column(db.Numeric(10, 2), default=0)
    shipping = db.Column(db.Numeric(10, 2), default=0)
    total = db.Column(db.Numeric(10, 2), default=0)

    payment_provider = db.Column(db.String(40), default="")  # stripe, mercadopago, manual
    payment_ref = db.Column(db.String(240), default="")      # session_id, preference_id, etc

    created_at = db.Column(db.DateTime, default=now_utc)

    items = db.relationship("OrderItem", backref="order", lazy=True, cascade="all, delete-orphan")

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)

    product_id = db.Column(db.Integer, nullable=True)
    product_name = db.Column(db.String(180), nullable=False)

    size = db.Column(db.String(20), default="")
    unit_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    line_total = db.Column(db.Numeric(10, 2), nullable=False, default=0)


# -------------------------
# Forms
# -------------------------
class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Length(max=190)])
    password = PasswordField("Senha", validators=[DataRequired(), Length(min=4, max=128)])
    remember = BooleanField("Manter conectado")

class CategoryForm(FlaskForm):
    name = StringField("Nome", validators=[DataRequired(), Length(max=120)])
    icon = StringField("Ícone (ex: ring, watch, earrings)", validators=[Optional(), Length(max=120)])
    is_active = BooleanField("Ativa")

class ProductForm(FlaskForm):
    category_id = SelectField("Categoria", coerce=int, validators=[Optional()])
    name = StringField("Nome", validators=[DataRequired(), Length(max=180)])
    description = TextAreaField("Descrição", validators=[Optional(), Length(max=8000)])
    price = DecimalField("Preço", validators=[DataRequired(), NumberRange(min=0)], places=2)
    stock = IntegerField("Estoque", validators=[DataRequired(), NumberRange(min=0)])
    sizes = StringField("Tamanhos (ex: P,M,G,GG) (opcional)", validators=[Optional(), Length(max=80)])
    is_active = BooleanField("Ativo")
    image = FileField("Imagem (png/jpg/webp)")

class BannerForm(FlaskForm):
    title = StringField("Título", validators=[Optional(), Length(max=180)])
    subtitle = StringField("Subtítulo", validators=[Optional(), Length(max=240)])
    cta_text = StringField("Texto do botão", validators=[Optional(), Length(max=60)])
    cta_link = StringField("Link do botão", validators=[Optional(), Length(max=240)])
    is_active = BooleanField("Ativo")
    image = FileField("Imagem (png/jpg/webp)")

class SettingsForm(FlaskForm):
    store_name = StringField("Nome da loja", validators=[DataRequired(), Length(max=120)])
    store_tagline = StringField("Slogan", validators=[Optional(), Length(max=180)])
    whatsapp = StringField("WhatsApp (DDI+DDD+Número)", validators=[Optional(), Length(max=40)])
    topbar_note = StringField("Aviso no topo", validators=[Optional(), Length(max=180)])
    shipping_free_over = DecimalField("Frete grátis acima de", validators=[DataRequired(), NumberRange(min=0)], places=2)
    shipping_flat = DecimalField("Frete fixo", validators=[DataRequired(), NumberRange(min=0)], places=2)
    primary_color = StringField("Cor primária (hex)", validators=[Optional(), Length(max=20)])
    accent_color = StringField("Cor destaque (hex)", validators=[Optional(), Length(max=20)])

class CheckoutForm(FlaskForm):
    email = StringField("E-mail", validators=[DataRequired(), Length(max=190)])
    name = StringField("Nome", validators=[Optional(), Length(max=180)])
    phone = StringField("Telefone", validators=[Optional(), Length(max=60)])
    cep = StringField("CEP", validators=[Optional(), Length(max=20)])
    address = TextAreaField("Endereço", validators=[Optional(), Length(max=5000)])
    notes = TextAreaField("Observações", validators=[Optional(), Length(max=5000)])


# -------------------------
# Auth / Admin guard
# -------------------------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def require_admin():
    if not current_user.is_authenticated:
        abort(401)
    if not getattr(current_user, "is_admin", False):
        abort(403)


# -------------------------
# Seed / defaults
# -------------------------
DEFAULT_SETTINGS = {
    "store_name": "VÉRACO",
    "store_tagline": "A elegância que ilumina sua estação.",
    "whatsapp": "5531999999999",
    "topbar_note": "Aproveite frete grátis nas compras acima de R$299,90",
    "shipping_free_over": "299.90",
    "shipping_flat": "9.90",
    "primary_color": "#111111",
    "accent_color": "#B08D57",
}

def ensure_settings():
    for k, v in DEFAULT_SETTINGS.items():
        if not Setting.query.filter_by(key=k).first():
            db.session.add(Setting(key=k, value=str(v)))
    db.session.commit()

def seed_if_needed(app: Flask):
    if User.query.count() == 0:
        u = User(email="admin@local", is_admin=True)
        u.set_password("admin123")
        db.session.add(u)

    ensure_settings()

    if Category.query.count() == 0:
        cats = [
            ("Anéis", "ring"),
            ("Alianças", "rings"),
            ("Brincos", "earrings"),
            ("Pulseiras", "bracelet"),
            ("Colares", "necklace"),
            ("Relógios", "watch"),
            ("Pingentes", "pendant"),
            ("Óculos", "glasses"),
        ]
        for name, icon in cats:
            db.session.add(Category(name=name, slug=slugify(name), icon=icon, is_active=True))

    if Product.query.count() == 0:
        c_ring = Category.query.filter_by(slug="aneis").first()
        c_neck = Category.query.filter_by(slug="colares").first()
        c_ear = Category.query.filter_by(slug="brincos").first()

        demo = [
            ("Anel Ouro 18k Solitário", "Clássico atemporal em ouro 18k com brilho impecável.", "799.90", 10, "", c_ring.id if c_ring else None),
            ("Colar Ponto de Luz", "Minimalista e sofisticado — perfeito para elevar qualquer look.", "459.90", 12, "", c_neck.id if c_neck else None),
            ("Brinco Argola Premium", "Acabamento premium e presença marcante.", "329.90", 18, "", c_ear.id if c_ear else None),
            ("Aliança Clássica 18k", "Elegância diária com conforto e durabilidade.", "1290.00", 8, "14,15,16,17,18,19", None),
        ]
        for name, desc, price, stock, sizes, cat_id in demo:
            db.session.add(Product(
                category_id=cat_id,
                name=name,
                slug=slugify(name),
                description=desc,
                price=money(price),
                stock=int(stock),
                sizes=sizes,
                is_active=True,
                image_filename=""
            ))

    if Banner.query.count() == 0:
        db.session.add(Banner(
            title="Clássicos em Ouro 18k",
            subtitle="Luxo discreto. Linhas limpas. Brilho eterno.",
            cta_text="Comprar agora",
            cta_link="/produtos",
            image_filename="",
            is_active=True
        ))

    db.session.commit()


# -------------------------
# App factory
# -------------------------
def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(app.root_path, "instance"), exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    stripe.api_key = app.config.get("STRIPE_SECRET_KEY", "")

    with app.app_context():
        db.create_all()
        seed_if_needed(app)

    register_routes(app)
    return app


# -------------------------
# Routes
# -------------------------
def register_routes(app: Flask):

    @app.context_processor
    def inject_globals():
        store_name = get_setting("store_name", "VÉRACO")
        tagline = get_setting("store_tagline", "")
        topbar_note = get_setting("topbar_note", "")
        accent = get_setting("accent_color", "#B08D57")
        primary = get_setting("primary_color", "#111111")

        cart = cart_get()
        subtotal = cart_subtotal(cart)
        ship = shipping_calc(subtotal)
        total = subtotal + ship

        return dict(
            STORE_NAME=app.config.get("STORE_NAME", "NEXOR"),
            STORE_TAGLINE=app.config.get("STORE_TAGLINE", ""),
            TOPBAR_NOTE=topbar_note,
            ACCENT_COLOR=accent,
            PRIMARY_COLOR=primary,
            CART_COUNT=cart_count(cart),
            CART_SUBTOTAL=format_brl(subtotal),
            CART_TOTAL=format_brl(total),
        )

    # ---------- uploads ----------
    @app.route("/uploads/<path:filename>")
    def uploads(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    # ---------- public ----------
    @app.route("/")
    def index():
        banner = Banner.query.filter_by(is_active=True).order_by(Banner.created_at.desc()).first()
        categories = Category.query.filter_by(is_active=True).order_by(Category.created_at.asc()).all()
        products = Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).limit(12).all()
        return render_template("index.html", banner=banner, categories=categories, products=products)

    @app.route("/produtos")
    def produtos():
        cat = (request.args.get("cat") or "").strip()
        q = (request.args.get("q") or "").strip()
        sort = (request.args.get("sort") or "new").strip()

        query = Product.query.filter_by(is_active=True)

        if cat:
            c = Category.query.filter_by(slug=cat).first()
            if c:
                query = query.filter_by(category_id=c.id)

        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Product.name.ilike(like), Product.description.ilike(like)))

        if sort == "price_asc":
            query = query.order_by(Product.price.asc())
        elif sort == "price_desc":
            query = query.order_by(Product.price.desc())
        else:
            query = query.order_by(Product.created_at.desc())

        products = query.all()
        categories = Category.query.filter_by(is_active=True).order_by(Category.created_at.asc()).all()
        return render_template("produtos.html", products=products, categories=categories, cat=cat, q=q, sort=sort)

    @app.route("/p/<slug>")
    def produto(slug):
        p = Product.query.filter_by(slug=slug, is_active=True).first_or_404()
        cat = Category.query.filter_by(id=p.category_id).first() if p.category_id else None
        sizes = [s.strip() for s in (p.sizes or "").split(",") if s.strip()]
        return render_template("produto.html", p=p, cat=cat, sizes=sizes)

    # ---------- cart API ----------
    @app.get("/api/cart")
    def api_cart():
        return jsonify(cart_payload(app))

    @app.post("/api/cart/add")
    def api_cart_add():
        data = request.get_json(silent=True) or {}
        product_id = int(data.get("product_id", 0))
        qty = int(data.get("qty", 1))
        size = (data.get("size") or "").strip().upper()

        qty = max(1, min(99, qty))

        p = db.session.get(Product, product_id)
        if not p or not p.is_active:
            return jsonify({"ok": False, "error": "Produto não encontrado."}), 404
        if p.stock <= 0:
            return jsonify({"ok": False, "error": "Sem estoque."}), 400

        sizes = [s.strip().upper() for s in (p.sizes or "").split(",") if s.strip()]
        if sizes and (not size or size not in sizes):
            return jsonify({"ok": False, "need_size": True, "sizes": sizes}), 400

        cart = cart_get()
        k = cart_key(product_id, size)
        current = int(cart.get(k, 0))
        new_qty = min(p.stock, current + qty)

        cart[k] = new_qty
        cart_save(cart)

        return jsonify({"ok": True, "message": "Adicionado ao carrinho!", "cart": cart_payload(app)})

    @app.post("/api/cart/update")
    def api_cart_update():
        data = request.get_json(silent=True) or {}
        key = (data.get("key") or "").strip()
        qty = int(data.get("qty", 1))
        qty = max(0, min(99, qty))

        cart = cart_get()
        if key not in cart:
            return jsonify({"ok": False, "error": "Item não encontrado."}), 404

        pid, _size = cart_split_key(key)
        p = db.session.get(Product, pid)
        if not p or not p.is_active:
            cart.pop(key, None)
            cart_save(cart)
            return jsonify({"ok": True, "cart": cart_payload(app)})

        if qty == 0:
            cart.pop(key, None)
        else:
            cart[key] = min(qty, p.stock)

        cart_save(cart)
        return jsonify({"ok": True, "cart": cart_payload(app)})

    @app.post("/api/cart/remove")
    def api_cart_remove():
        data = request.get_json(silent=True) or {}
        key = (data.get("key") or "").strip()
        cart = cart_get()
        cart.pop(key, None)
        cart_save(cart)
        return jsonify({"ok": True, "cart": cart_payload(app)})

    @app.post("/api/cart/clear")
    def api_cart_clear():
        cart_save({})
        return jsonify({"ok": True, "cart": cart_payload(app)})

    # ---------- checkout (3 etapas visual) ----------
    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():
        cart = cart_get()
        if not cart:
            flash("Seu carrinho está vazio.", "warning")
            return redirect(url_for("produtos"))

        form = CheckoutForm()
        payload = cart_payload(app)

        if form.validate_on_submit():
            # cria pedido
            subtotal = cart_subtotal(cart)
            ship = shipping_calc(subtotal)
            total = subtotal + ship

            order = Order(
                status="Novo",
                customer_email=form.email.data.strip(),
                customer_name=(form.name.data or "").strip(),
                customer_phone=(form.phone.data or "").strip(),
                cep=(form.cep.data or "").strip(),
                address=(form.address.data or "").strip(),
                notes=(form.notes.data or "").strip(),
                subtotal=subtotal,
                shipping=ship,
                total=total,
                payment_provider="",
                payment_ref="",
            )
            db.session.add(order)
            db.session.flush()

            pmap = cart_products_map(cart)
            for k, qty in cart.items():
                pid, size = cart_split_key(k)
                p = pmap.get(pid)
                if not p:
                    continue
                qty_i = int(qty)
                unit = money(p.price)
                line = unit * qty_i

                db.session.add(OrderItem(
                    order_id=order.id,
                    product_id=p.id,
                    product_name=p.name,
                    size=size,
                    unit_price=unit,
                    quantity=qty_i,
                    line_total=line,
                ))
                # baixa estoque
                p.stock = max(0, p.stock - qty_i)

            db.session.commit()
            cart_save({})

            return redirect(url_for("pagamento", order_id=order.id))

        return render_template("checkout.html", form=form, payload=payload)

    @app.route("/pagamento/<int:order_id>")
    def pagamento(order_id):
        order = db.session.get(Order, order_id)
        if not order:
            abort(404)

        stripe_enabled = bool(app.config.get("STRIPE_SECRET_KEY"))
        mp_enabled = bool(app.config.get("MP_ACCESS_TOKEN"))
        return render_template("pagamento.html", order=order, stripe_enabled=stripe_enabled, mp_enabled=mp_enabled)

    # ---------- Stripe ----------
    @app.post("/pay/stripe/<int:order_id>")
    def pay_stripe(order_id):
        order = db.session.get(Order, order_id)
        if not order:
            abort(404)
        if not app.config.get("STRIPE_SECRET_KEY"):
            flash("Stripe não configurado. Adicione STRIPE_SECRET_KEY no servidor.", "danger")
            return redirect(url_for("pagamento", order_id=order_id))

        # Stripe Checkout Session
        items = []
        for it in order.items:
            name = it.product_name + (f" ({it.size})" if it.size else "")
            items.append({
                "price_data": {
                    "currency": "brl",
                    "product_data": {"name": name},
                    "unit_amount": int(money(it.unit_price) * 100),
                },
                "quantity": int(it.quantity),
            })

        # adicionar frete como item
        if money(order.shipping) > 0:
            items.append({
                "price_data": {
                    "currency": "brl",
                    "product_data": {"name": "Frete"},
                    "unit_amount": int(money(order.shipping) * 100),
                },
                "quantity": 1,
            })

        session_stripe = stripe.checkout.Session.create(
            mode="payment",
            line_items=items,
            success_url=url_for("pay_success", order_id=order.id, _external=True),
            cancel_url=url_for("pagamento", order_id=order.id, _external=True),
            customer_email=order.customer_email or None,
        )

        order.payment_provider = "stripe"
        order.payment_ref = session_stripe.id
        order.status = "Pagando"
        db.session.commit()

        return redirect(session_stripe.url, code=303)

    @app.route("/pay/success/<int:order_id>")
    def pay_success(order_id):
        order = db.session.get(Order, order_id)
        if not order:
            abort(404)

        # Em produção, o correto é confirmar via Webhook do Stripe.
        order.status = "Pago"
        db.session.commit()

        flash("Pagamento confirmado! Pedido registrado.", "success")
        return redirect(url_for("pedido_view", order_id=order_id))

    # ---------- Mercado Pago (Preference) ----------
    @app.post("/pay/mp/<int:order_id>")
    def pay_mp(order_id):
        order = db.session.get(Order, order_id)
        if not order:
            abort(404)

        token = app.config.get("MP_ACCESS_TOKEN", "")
        if not token:
            flash("Mercado Pago não configurado. Adicione MP_ACCESS_TOKEN no servidor.", "danger")
            return redirect(url_for("pagamento", order_id=order_id))

        items = []
        for it in order.items:
            name = it.product_name + (f" ({it.size})" if it.size else "")
            items.append({
                "title": name,
                "quantity": int(it.quantity),
                "currency_id": "BRL",
                "unit_price": float(money(it.unit_price)),
            })

        if money(order.shipping) > 0:
            items.append({
                "title": "Frete",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": float(money(order.shipping)),
            })

        preference = {
            "items": items,
            "payer": {
                "email": order.customer_email or "comprador@email.com"
            },
            "back_urls": {
                "success": url_for("pay_success", order_id=order.id, _external=True),
                "failure": url_for("pagamento", order_id=order.id, _external=True),
                "pending": url_for("pagamento", order_id=order.id, _external=True),
            },
            "auto_return": "approved",
        }

        r = requests.post(
            "https://api.mercadopago.com/checkout/preferences",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=preference,
            timeout=25
        )
        if r.status_code >= 300:
            flash("Erro ao criar pagamento no Mercado Pago.", "danger")
            return redirect(url_for("pagamento", order_id=order_id))

        data = r.json()
        order.payment_provider = "mercadopago"
        order.payment_ref = data.get("id", "")
        order.status = "Pagando"
        db.session.commit()

        init_point = data.get("init_point") or data.get("sandbox_init_point")
        return redirect(init_point)

    # ---------- Pedido público ----------
    @app.route("/pedido/<int:order_id>")
    def pedido_view(order_id):
        order = db.session.get(Order, order_id)
        if not order:
            abort(404)
        return render_template("admin_pedido.html", order=order, public_view=True)

    # ---------- auth ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        form = LoginForm()
        if form.validate_on_submit():
            user = User.query.filter_by(email=form.email.data.strip().lower()).first()
            if not user or not user.check_password(form.password.data):
                flash("Email ou senha inválidos.", "danger")
                return redirect(url_for("login"))
            login_user(user, remember=bool(form.remember.data))
            return redirect(url_for("admin_dashboard"))
        return render_template("login.html", form=form)

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Você saiu.", "info")
        return redirect(url_for("index"))

    # ---------- admin ----------
    @app.route("/admin")
    @login_required
    def admin_dashboard():
        require_admin()
        stats = {
            "produtos": Product.query.count(),
            "categorias": Category.query.count(),
            "pedidos": Order.query.count(),
            "novos": Order.query.filter_by(status="Novo").count(),
        }
        return render_template("admin_dashboard.html", stats=stats)

    # ---- settings ----
    @app.route("/admin/settings", methods=["GET", "POST"])
    @login_required
    def admin_settings():
        require_admin()
        form = SettingsForm()

        if request.method == "GET":
            form.store_name.data = get_setting("store_name", DEFAULT_SETTINGS["store_name"])
            form.store_tagline.data = get_setting("store_tagline", DEFAULT_SETTINGS["store_tagline"])
            form.whatsapp.data = get_setting("whatsapp", DEFAULT_SETTINGS["whatsapp"])
            form.topbar_note.data = get_setting("topbar_note", DEFAULT_SETTINGS["topbar_note"])
            form.shipping_free_over.data = money(get_setting("shipping_free_over", DEFAULT_SETTINGS["shipping_free_over"]))
            form.shipping_flat.data = money(get_setting("shipping_flat", DEFAULT_SETTINGS["shipping_flat"]))
            form.primary_color.data = get_setting("primary_color", DEFAULT_SETTINGS["primary_color"])
            form.accent_color.data = get_setting("accent_color", DEFAULT_SETTINGS["accent_color"])

        if form.validate_on_submit():
            pairs = {
                "store_name": form.store_name.data.strip(),
                "store_tagline": (form.store_tagline.data or "").strip(),
                "whatsapp": (form.whatsapp.data or "").strip(),
                "topbar_note": (form.topbar_note.data or "").strip(),
                "shipping_free_over": str(money(form.shipping_free_over.data)),
                "shipping_flat": str(money(form.shipping_flat.data)),
                "primary_color": (form.primary_color.data or "").strip() or "#111111",
                "accent_color": (form.accent_color.data or "").strip() or "#B08D57",
            }
            for k, v in pairs.items():
                s = Setting.query.filter_by(key=k).first()
                if not s:
                    s = Setting(key=k, value=str(v))
                    db.session.add(s)
                else:
                    s.value = str(v)
            db.session.commit()
            flash("Configurações salvas.", "success")
            return redirect(url_for("admin_settings"))

        return render_template("admin_settings.html", form=form)

    # ---- categories ----
    @app.route("/admin/categorias")
    @login_required
    def admin_categorias():
        require_admin()
        categories = Category.query.order_by(Category.created_at.desc()).all()
        return render_template("admin_categorias.html", categories=categories)

    @app.route("/admin/categorias/nova", methods=["GET", "POST"])
    @login_required
    def admin_categoria_nova():
        require_admin()
        form = CategoryForm()
        if request.method == "GET":
            form.is_active.data = True

        if form.validate_on_submit():
            name = form.name.data.strip()
            base = slugify(name)
            slug = base
            i = 2
            while Category.query.filter_by(slug=slug).first():
                slug = f"{base}-{i}"
                i += 1

            c = Category(name=name, slug=slug, icon=(form.icon.data or "").strip(), is_active=bool(form.is_active.data))
            db.session.add(c)
            db.session.commit()
            flash("Categoria criada.", "success")
            return redirect(url_for("admin_categorias"))

        return render_template("admin_categoria_form.html", form=form, mode="novo")

    @app.route("/admin/categorias/<int:cat_id>/editar", methods=["GET", "POST"])
    @login_required
    def admin_categoria_editar(cat_id):
        require_admin()
        c = db.session.get(Category, cat_id)
        if not c:
            abort(404)
        form = CategoryForm(obj=c)

        if form.validate_on_submit():
            c.name = form.name.data.strip()
            c.icon = (form.icon.data or "").strip()
            c.is_active = bool(form.is_active.data)
            db.session.commit()
            flash("Categoria atualizada.", "success")
            return redirect(url_for("admin_categorias"))

        return render_template("admin_categoria_form.html", form=form, mode="editar", c=c)

    @app.post("/admin/categorias/<int:cat_id>/delete")
    @login_required
    def admin_categoria_delete(cat_id):
        require_admin()
        c = db.session.get(Category, cat_id)
        if not c:
            abort(404)
        db.session.delete(c)
        db.session.commit()
        flash("Categoria removida.", "info")
        return redirect(url_for("admin_categorias"))

    # ---- products ----
    @app.route("/admin/produtos")
    @login_required
    def admin_produtos():
        require_admin()
        products = Product.query.order_by(Product.created_at.desc()).all()
        categories = Category.query.order_by(Category.name.asc()).all()
        cmap = {c.id: c for c in categories}
        return render_template("admin_produtos.html", products=products, cmap=cmap)

    def _product_form_choices(form: ProductForm):
        cats = Category.query.filter_by(is_active=True).order_by(Category.name.asc()).all()
        choices = [(0, "— Sem categoria —")] + [(c.id, c.name) for c in cats]
        form.category_id.choices = choices

    @app.route("/admin/produtos/novo", methods=["GET", "POST"])
    @login_required
    def admin_produto_novo():
        require_admin()
        form = ProductForm()
        _product_form_choices(form)
        if request.method == "GET":
            form.is_active.data = True
            form.category_id.data = 0

        if form.validate_on_submit():
            name = form.name.data.strip()
            base = slugify(name)
            slug = base
            i = 2
            while Product.query.filter_by(slug=slug).first():
                slug = f"{base}-{i}"
                i += 1

            image_filename = ""
            file = request.files.get("image")
            if file and file.filename:
                if not allowed_file(file.filename):
                    flash("Imagem inválida. Use png/jpg/webp.", "danger")
                    return redirect(url_for("admin_produto_novo"))
                image_filename = secure_upload_name(slug, file.filename)
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))

            cat_id = int(form.category_id.data or 0) or None

            p = Product(
                category_id=cat_id,
                name=name,
                slug=slug,
                description=form.description.data or "",
                price=money(form.price.data),
                stock=int(form.stock.data),
                sizes=(form.sizes.data or "").strip(),
                is_active=bool(form.is_active.data),
                image_filename=image_filename
            )
            db.session.add(p)
            db.session.commit()
            flash("Produto criado.", "success")
            return redirect(url_for("admin_produtos"))

        return render_template("admin_produto_form.html", form=form, mode="novo")

    @app.route("/admin/produtos/<int:pid>/editar", methods=["GET", "POST"])
    @login_required
    def admin_produto_editar(pid):
        require_admin()
        p = db.session.get(Product, pid)
        if not p:
            abort(404)
        form = ProductForm(obj=p)
        _product_form_choices(form)
        if request.method == "GET":
            form.category_id.data = int(p.category_id or 0)

        if form.validate_on_submit():
            p.name = form.name.data.strip()
            p.description = form.description.data or ""
            p.price = money(form.price.data)
            p.stock = int(form.stock.data)
            p.sizes = (form.sizes.data or "").strip()
            p.is_active = bool(form.is_active.data)
            p.category_id = int(form.category_id.data or 0) or None

            file = request.files.get("image")
            if file and file.filename:
                if not allowed_file(file.filename):
                    flash("Imagem inválida. Use png/jpg/webp.", "danger")
                    return redirect(url_for("admin_produto_editar", pid=pid))
                image_filename = secure_upload_name(p.slug, file.filename)
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))
                p.image_filename = image_filename

            db.session.commit()
            flash("Produto atualizado.", "success")
            return redirect(url_for("admin_produtos"))

        return render_template("admin_produto_form.html", form=form, mode="editar", p=p)

    @app.post("/admin/produtos/<int:pid>/delete")
    @login_required
    def admin_produto_delete(pid):
        require_admin()
        p = db.session.get(Product, pid)
        if not p:
            abort(404)
        db.session.delete(p)
        db.session.commit()
        flash("Produto removido.", "info")
        return redirect(url_for("admin_produtos"))

    # ---- banners ----
    @app.route("/admin/banners")
    @login_required
    def admin_banners():
        require_admin()
        banners = Banner.query.order_by(Banner.created_at.desc()).all()
        return render_template("admin_banners.html", banners=banners)

    @app.route("/admin/banners/novo", methods=["GET", "POST"])
    @login_required
    def admin_banner_novo():
        require_admin()
        form = BannerForm()
        if request.method == "GET":
            form.is_active.data = True
            form.cta_text.data = "Comprar agora"
            form.cta_link.data = "/produtos"

        if form.validate_on_submit():
            image_filename = ""
            file = request.files.get("image")
            if file and file.filename:
                if not allowed_file(file.filename):
                    flash("Imagem inválida. Use png/jpg/webp.", "danger")
                    return redirect(url_for("admin_banner_novo"))
                image_filename = secure_upload_name("banner", file.filename)
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))

            b = Banner(
                title=(form.title.data or "").strip(),
                subtitle=(form.subtitle.data or "").strip(),
                cta_text=(form.cta_text.data or "").strip() or "Comprar agora",
                cta_link=(form.cta_link.data or "").strip() or "/produtos",
                image_filename=image_filename,
                is_active=bool(form.is_active.data)
            )
            db.session.add(b)
            db.session.commit()
            flash("Banner criado.", "success")
            return redirect(url_for("admin_banners"))

        return render_template("admin_banner_form.html", form=form, mode="novo")

    @app.route("/admin/banners/<int:bid>/editar", methods=["GET", "POST"])
    @login_required
    def admin_banner_editar(bid):
        require_admin()
        b = db.session.get(Banner, bid)
        if not b:
            abort(404)
        form = BannerForm(obj=b)

        if form.validate_on_submit():
            b.title = (form.title.data or "").strip()
            b.subtitle = (form.subtitle.data or "").strip()
            b.cta_text = (form.cta_text.data or "").strip() or "Comprar agora"
            b.cta_link = (form.cta_link.data or "").strip() or "/produtos"
            b.is_active = bool(form.is_active.data)

            file = request.files.get("image")
            if file and file.filename:
                if not allowed_file(file.filename):
                    flash("Imagem inválida. Use png/jpg/webp.", "danger")
                    return redirect(url_for("admin_banner_editar", bid=bid))
                image_filename = secure_upload_name("banner", file.filename)
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))
                b.image_filename = image_filename

            db.session.commit()
            flash("Banner atualizado.", "success")
            return redirect(url_for("admin_banners"))

        return render_template("admin_banner_form.html", form=form, mode="editar", b=b)

    @app.post("/admin/banners/<int:bid>/delete")
    @login_required
    def admin_banner_delete(bid):
        require_admin()
        b = db.session.get(Banner, bid)
        if not b:
            abort(404)
        db.session.delete(b)
        db.session.commit()
        flash("Banner removido.", "info")
        return redirect(url_for("admin_banners"))

    # ---- orders ----
    @app.route("/admin/pedidos")
    @login_required
    def admin_pedidos():
        require_admin()
        status = (request.args.get("status") or "").strip()
        q = Order.query
        if status:
            q = q.filter_by(status=status)
        orders = q.order_by(Order.created_at.desc()).limit(300).all()
        return render_template("admin_pedidos.html", orders=orders, status=status)

    @app.route("/admin/pedidos/<int:oid>", methods=["GET", "POST"])
    @login_required
    def admin_pedido(oid):
        require_admin()
        order = db.session.get(Order, oid)
        if not order:
            abort(404)

        if request.method == "POST":
            new_status = (request.form.get("status") or "").strip()
            allowed = {"Novo","Pagando","Pago","Separando","Enviado","Concluído","Cancelado"}
            if new_status in allowed:
                order.status = new_status
                db.session.commit()
                flash("Status atualizado.", "success")
            return redirect(url_for("admin_pedido", oid=oid))

        return render_template("admin_pedido.html", order=order, public_view=False)


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)