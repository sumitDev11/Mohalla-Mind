"""SQLAlchemy tables (CLAUDE.md Section 5). All money is integer paise."""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Shop(Base):
    __tablename__ = "shops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_name: Mapped[str] = mapped_column(String(120))
    shop_type: Mapped[str] = mapped_column(String(20))  # kirana / dairy / general
    size: Mapped[str] = mapped_column(String(1))  # S / M / L
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    language: Mapped[str] = mapped_column(String(10), default="hi-IN")
    soundbox_id: Mapped[str] = mapped_column(String(40))


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name_en: Mapped[str] = mapped_column(String(80))
    name_hi: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(30))
    unit: Mapped[str] = mapped_column(String(20))
    mrp_paise: Mapped[int] = mapped_column(Integer)
    solo_cost_paise: Mapped[int] = mapped_column(Integer)
    shelf_life_days: Mapped[int] = mapped_column(Integer)
    perishable: Mapped[bool] = mapped_column(Boolean)


class PriceTier(Base):
    __tablename__ = "price_tiers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    min_qty: Mapped[int] = mapped_column(Integer)
    unit_price_paise: Mapped[int] = mapped_column(Integer)


class CalendarDay(Base):
    __tablename__ = "calendar"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    festival: Mapped[str | None] = mapped_column(String(40), nullable=True)
    weather: Mapped[str] = mapped_column(String(10))  # sunny / rain / hot / cold
    temp_c: Mapped[float] = mapped_column(Float)


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[int] = mapped_column(Integer)
    revenue_paise: Mapped[int] = mapped_column(Integer)
    upi_txn_count: Mapped[int] = mapped_column(Integer)


class InventoryBatch(Base):
    __tablename__ = "inventory_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    cost_price_paise: Mapped[int] = mapped_column(Integer)
    expiry_date: Mapped[date] = mapped_column(Date)
    received_date: Mapped[date] = mapped_column(Date)


class Forecast(Base):
    __tablename__ = "forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    qty_pred: Mapped[float] = mapped_column(Float)
    qty_low: Mapped[float] = mapped_column(Float)
    qty_high: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(30))


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # restock / pool / swap_give / swap_take / deal
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    savings_paise: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sim_date: Mapped[date] = mapped_column(Date, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_via: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Pool(Base):
    __tablename__ = "pools"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    total_qty: Mapped[int] = mapped_column(Integer)
    tier_price_paise: Mapped[int] = mapped_column(Integer)
    solo_price_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / executed / cancelled
    deadline: Mapped[datetime] = mapped_column(DateTime)
    sim_date: Mapped[date] = mapped_column(Date)


class PoolMember(Base):
    __tablename__ = "pool_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), index=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    qty: Mapped[int] = mapped_column(Integer)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"))


class Swap(Base):
    __tablename__ = "swaps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    to_shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[int] = mapped_column(Integer)
    distance_m: Mapped[int] = mapped_column(Integer)
    transfer_price_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed / executed / rejected / expired
    sim_date: Mapped[date] = mapped_column(Date)


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"))
    discount_pct: Mapped[int] = mapped_column(Integer)
    deal_price_paise: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer)
    qty_redeemed: Mapped[int] = mapped_column(Integer, default=0)
    qr_payload: Mapped[str] = mapped_column(String(120), default="")
    expires_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed / active / sold_out / expired / rejected
    sim_date: Mapped[date] = mapped_column(Date)


class Brief(Base):
    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    text_hi: Mapped[str] = mapped_column(Text)
    text_roman: Mapped[str] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="template")  # template / llm


class ConsentLog(Base):
    __tablename__ = "consent_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id"), index=True)
    scope: Mapped[str] = mapped_column(String(30))  # share_aggregates / join_pools
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SimState(Base):
    __tablename__ = "sim_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_date: Mapped[date] = mapped_column(Date)
    night_ran_for: Mapped[date | None] = mapped_column(Date, nullable=True)


class ImpactLog(Base):
    __tablename__ = "impact_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    metric: Mapped[str] = mapped_column(String(40))  # pool_savings_inr / stockouts_avoided / waste_saved_units / deal_revenue_inr
    value: Mapped[int] = mapped_column(Integer)  # paise for *_inr metrics, count otherwise
