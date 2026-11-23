"""Database schema for OpenPrintHQ.

Model = a design (geometry + photos + print settings).
Product = how a model is sold (SKU, price, Woo listing, BOM, color variants).
"""
from datetime import date, datetime, timezone
from sqlalchemy import (
    String, Integer, BigInteger, Boolean, Float, Text, Date, DateTime, ForeignKey, JSON, UniqueConstraint, Index,
)
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .tenant import TenantMixin


def now():
    return datetime.now(timezone.utc)


class Category(TenantMixin, Base):
    __tablename__ = "category"
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_category_team_name"),
                      UniqueConstraint("team_id", "slug", name="uq_category_team_slug"))
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140))
    woo_category_id: Mapped[int | None] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Designer(TenantMixin, Base):
    __tablename__ = "designer"
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_designer_team_name"),
                      UniqueConstraint("team_id", "slug", name="uq_designer_team_slug"))
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140))
    url: Mapped[str | None] = mapped_column(String(500))
    license_note: Mapped[str | None] = mapped_column(Text)
    handle: Mapped[str | None] = mapped_column(String(120))
    platform: Mapped[str | None] = mapped_column(String(40))          # makerworld | printables | patreon | own ...
    credit_template: Mapped[str | None] = mapped_column(Text)          # e.g. "Design by {designer}{profile}. {license_part}"
    always_credit: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    grants: Mapped[list["LicenseGrant"]] = relationship(back_populates="designer", cascade="all, delete-orphan",
                                                         order_by="LicenseGrant.starts_on.desc()")


class PrinterProfile(TenantMixin, Base):
    """One of your printers as the hub slices for it: a slicer engine (orca | prusa), a printer preset from that
    engine's catalog (or an imported one), its tools, and your own overrides."""
    __tablename__ = "printer_profile"
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_printer_profile_team_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))          # e.g. "H2D"
    farm_printer: Mapped[str | None] = mapped_column(String(80))          # the printer's name in the farm
    engine: Mapped[str] = mapped_column(String(10), default="orca")      # orca | prusa
    machine_preset: Mapped[str] = mapped_column(String(200))            # e.g. "Bambu Lab H2D 0.4 nozzle"
    machine_config: Mapped[str | None] = mapped_column(Text)            # imported printer preset (Orca JSON or PrusaSlicer INI)
    # [{"slot": 1, "nozzle": 0.4, "flow": "standard"|"hf", "material": "PLA", "color": "#RRGGBB", "preset": None}]
    tools: Mapped[list] = mapped_column(JSON, default=list)
    dispatch: Mapped[str] = mapped_column(String(20), default="none")   # none | farm: where approved files go
    hourly_rate: Mapped[float | None] = mapped_column(Float)            # machine time cost per hour (power, wear); None = team default
    filament_slots: Mapped[int | None] = mapped_column(Integer)         # filaments it can hold incl. AMS; None = one per tool
    bed_type: Mapped[str | None] = mapped_column(String(40))            # build plate always fitted (OrcaSlicer name); None = printer default
    # the plate on the printer right now (from the plate catalog); differs from bed_type when a plate is swapped in,
    # and then a job sliced for bed_type is resliced for this plate before it prints (services/plates.py)
    installed_plate_id: Mapped[int | None] = mapped_column(ForeignKey("build_plate.id", ondelete="SET NULL"))
    process_preset: Mapped[str | None] = mapped_column(String(200))     # default process
    filament_presets: Mapped[dict] = mapped_column(JSON, default=dict)  # {"PLA": "Bambu PLA Basic @BBL H2D", ...}
    machine_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    process_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    use_model_settings: Mapped[bool] = mapped_column(Boolean, default=True)   # apply the designer's walls/infill/supports
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BuildPlate(TenantMixin, Base):
    """A build plate you own: what the slicer calls it, whether a Bambu printer can recognise it, and which
    filaments it suits. A printer profile's installed_plate_id points here."""
    __tablename__ = "build_plate"
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_build_plate_team_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))                        # "Darkmoon CFX"
    maker: Mapped[str | None] = mapped_column(String(60))                # "Darkmoon", "Bambu Lab", "Prusa"
    surface: Mapped[str | None] = mapped_column(String(40))              # smooth_pei | textured_pei | g10 | ...
    slicer_type: Mapped[str] = mapped_column(String(40))                 # the slicer's plate type: "High Temp Plate"
    bambu_marker: Mapped[bool] = mapped_column(Boolean, default=False)   # a genuine Bambu plate the printer can detect
    materials: Mapped[list] = mapped_column(JSON, default=list)          # filament families it suits: ["PLA", "PETG", ...]
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LicenseGrant(TenantMixin, Base):
    """A commercial license you hold from a designer: a membership (valid while paid), a one-time purchase,
    or written permission. Models marked COMMERCIAL for that designer are sellable only while a grant is active."""
    __tablename__ = "license_grant"
    id: Mapped[int] = mapped_column(primary_key=True)
    designer_id: Mapped[int] = mapped_column(ForeignKey("designer.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))                       # e.g. "Patreon commercial tier"
    kind: Mapped[str] = mapped_column(String(20), default="membership")  # membership | purchase | permission
    starts_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)                # None = no end date
    auto_renews: Mapped[bool] = mapped_column(Boolean, default=False)
    renew_url: Mapped[str | None] = mapped_column(String(1000))
    covers_all: Mapped[bool] = mapped_column(Boolean, default=True)      # False: only models listed in model_codes
    model_codes: Mapped[list] = mapped_column(JSON, default=list)
    sales_need_active: Mapped[bool] = mapped_column(Boolean, default=True)  # prints may be sold only while active
    terms: Mapped[str | None] = mapped_column(Text)
    doc_rel: Mapped[str | None] = mapped_column(String(500))             # uploaded terms / receipt, under data_dir
    doc_sha256: Mapped[str | None] = mapped_column(String(64))
    last_alert: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    designer: Mapped["Designer"] = relationship(back_populates="grants")


class Model(TenantMixin, Base):
    __tablename__ = "model"
    __table_args__ = (UniqueConstraint("team_id", "code", name="uq_model_team_code"),
                      UniqueConstraint("team_id", "slug", name="uq_model_team_slug"))
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))          # e.g. M1185 or AP-0001 (unique per team)
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(320))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    designer_id: Mapped[int | None] = mapped_column(ForeignKey("designer.id"))
    source: Mapped[str] = mapped_column(String(40), default="own")       # deltaprints | own | other
    source_url: Mapped[str | None] = mapped_column(String(1000))
    patreon_url: Mapped[str | None] = mapped_column(String(1000))
    guide_url: Mapped[str | None] = mapped_column(String(1000))
    summary: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    guide_md: Mapped[str | None] = mapped_column(Text)
    model_dims: Mapped[str | None] = mapped_column(String(120))
    print_dims: Mapped[str | None] = mapped_column(String(120))
    print_settings: Mapped[dict] = mapped_column(JSON, default=dict)     # designer-recommended settings
    tags: Mapped[list] = mapped_column(JSON, default=list)
    license_ok: Mapped[bool] = mapped_column(Boolean, default=False)   # derived from license_code
    license_note: Mapped[str | None] = mapped_column(Text)
    license_code: Mapped[str | None] = mapped_column(String(30))
    license_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_override: Mapped[str | None] = mapped_column(Text)
    makerworld_id: Mapped[int | None] = mapped_column(Integer, index=True)
    makerworld_instance_id: Mapped[int | None] = mapped_column(Integer)
    source_platform: Mapped[str | None] = mapped_column(String(30))     # makerworld | printables | thingiverse | creality
    source_ext_id: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")     # draft | ready | archived
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source_folder: Mapped[str | None] = mapped_column(String(1000))      # relative to source_root
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    library_rel: Mapped[str | None] = mapped_column(String(1000))        # relative to library_root
    manyfold_model_id: Mapped[str | None] = mapped_column(String(64))
    dam_collection_ref: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    colors_needed: Mapped[int | None] = mapped_column(Integer)          # distinct colours the model is set up for
    inspect: Mapped[dict] = mapped_column(JSON, default=dict)           # slicer inspection: paint, flat faces
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    category: Mapped[Category | None] = relationship()
    designer: Mapped[Designer | None] = relationship()
    images: Mapped[list["Image"]] = relationship(back_populates="model", cascade="all, delete-orphan",
                                                 order_by="Image.sort")
    files: Mapped[list["ModelFile"]] = relationship(back_populates="model", cascade="all, delete-orphan",
                                                    order_by="ModelFile.filename")
    product: Mapped["Product | None"] = relationship(back_populates="model", uselist=False,
                                                     cascade="all, delete-orphan")


class Image(TenantMixin, Base):
    __tablename__ = "image"
    __table_args__ = (UniqueConstraint("model_id", "sha256"), Index("ix_image_model_sort", "model_id", "sort"))
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"))
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(400))
    role: Mapped[str] = mapped_column(String(20), default="gallery")    # hero | gallery | detail | cutout | archive
    sort: Mapped[int] = mapped_column(Integer, default=100)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    caption: Mapped[str | None] = mapped_column(String(500))
    source_rel: Mapped[str | None] = mapped_column(String(1000))
    store_rel: Mapped[str | None] = mapped_column(String(500))          # original kept under data_dir/originals
    dam_ref: Mapped[int | None] = mapped_column(Integer)
    dam_error: Mapped[str | None] = mapped_column(Text)
    woo_media_id: Mapped[int | None] = mapped_column(Integer)
    for_web: Mapped[bool] = mapped_column(Boolean, default=True)         # include when pushing to Woo
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    model: Mapped[Model] = relationship(back_populates="images")


class LicenseEvidence(TenantMixin, Base):
    """What the license was when we took the model: raw source data, page HTML, PDF record, archive link."""
    __tablename__ = "license_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))        # makerworld_snapshot | upload | note
    source_url: Mapped[str | None] = mapped_column(String(1000))
    license_code: Mapped[str | None] = mapped_column(String(30))
    license_raw: Mapped[str | None] = mapped_column(String(200))
    license_text: Mapped[str | None] = mapped_column(Text)
    json_rel: Mapped[str | None] = mapped_column(String(500))
    html_rel: Mapped[str | None] = mapped_column(String(500))
    pdf_rel: Mapped[str | None] = mapped_column(String(500))
    wayback_url: Mapped[str | None] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelFile(TenantMixin, Base):
    __tablename__ = "model_file"
    __table_args__ = (UniqueConstraint("model_id", "sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"))
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(20))      # stl | 3mf_project | 3mf_sliced | gcode | step | f3d | obj | other
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    library_rel: Mapped[str | None] = mapped_column(String(1000))
    slicer: Mapped[str | None] = mapped_column(String(60))     # BambuStudio | Prusa | Anycubic | CrealityPrint | Orca
    printer: Mapped[str | None] = mapped_column(String(120))
    plates: Mapped[int | None] = mapped_column(Integer)
    print_seconds: Mapped[int | None] = mapped_column(Integer)
    filament: Mapped[list] = mapped_column(JSON, default=list)   # [{slot, type, color, grams}]
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # The file's id in the farm's library (farm_kind "openprinthq"; any other value is a ref the farm cannot
    # use, so the file is uploaded again on first use: see shop.farm_ref()).
    farm_file_ref: Mapped[str | None] = mapped_column(String(80))
    farm_file_error: Mapped[str | None] = mapped_column(Text)
    farm_kind: Mapped[str | None] = mapped_column(String(20))
    is_print_file: Mapped[bool] = mapped_column(Boolean, default=False)   # chosen for production
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    model: Mapped[Model] = relationship(back_populates="files")


class Product(TenantMixin, Base):
    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("team_id", "sku", name="uq_product_team_sku"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"), unique=True)
    sku: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    price: Mapped[float | None] = mapped_column(Float)
    sale_price: Mapped[float | None] = mapped_column(Float)
    short_description: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    weight_g: Mapped[float | None] = mapped_column(Float)
    dims_cm: Mapped[str | None] = mapped_column(String(60))      # LxWxH
    color_mode: Mapped[str] = mapped_column(String(20), default="single")   # single | options
    target_buffer: Mapped[int] = mapped_column(Integer, default=2)    # units of supplies to keep on hand
    stock_cap: Mapped[int] = mapped_column(Integer, default=10)       # max stock shown on Woo
    live: Mapped[bool] = mapped_column(Boolean, default=False)        # desired state on the store
    woo_product_id: Mapped[int | None] = mapped_column(Integer)
    woo_status: Mapped[str | None] = mapped_column(String(20))        # draft | publish | private ...
    woo_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    woo_error: Mapped[str | None] = mapped_column(Text)
    woo_stock_pushed: Mapped[dict] = mapped_column(JSON, default=dict)   # {variant_id|"p": qty}
    farm_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    model: Mapped[Model] = relationship(back_populates="product")
    variants: Mapped[list["Variant"]] = relationship(back_populates="product", cascade="all, delete-orphan",
                                                     order_by="Variant.sort")
    filaments: Mapped[list["BomFilament"]] = relationship(back_populates="product", cascade="all, delete-orphan",
                                                          order_by="BomFilament.slot")
    parts: Mapped[list["BomPart"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Variant(TenantMixin, Base):
    __tablename__ = "variant"
    __table_args__ = (UniqueConstraint("product_id", "color_name"), UniqueConstraint("team_id", "sku", name="uq_variant_team_sku"))
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(80))
    color_name: Mapped[str] = mapped_column(String(80))
    color_hex: Mapped[str | None] = mapped_column(String(9))
    material: Mapped[str | None] = mapped_column(String(40))
    spoolman_filament_id: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    image_id: Mapped[int | None] = mapped_column(ForeignKey("image.id", ondelete="SET NULL"))
    woo_variation_id: Mapped[int | None] = mapped_column(Integer)
    product: Mapped[Product] = relationship(back_populates="variants")


class StoreListing(TenantMixin, Base):
    """A product on a store other than WooCommerce (services.stores). One row per product and store; a row with
    enabled false keeps the product off that store."""
    __tablename__ = "store_listing"
    __table_args__ = (UniqueConstraint("team_id", "product_id", "channel", name="uq_store_listing_product_channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(12))                     # shopify | etsy
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ext_id: Mapped[str | None] = mapped_column(String(60))               # the listing's id in that store
    status: Mapped[str | None] = mapped_column(String(20))               # draft | active | inactive
    error: Mapped[str | None] = mapped_column(Text)
    stock_pushed: Mapped[dict] = mapped_column(JSON, default=dict)       # {variant_id|"p": qty}, as for Woo
    refs: Mapped[dict] = mapped_column(JSON, default=dict)               # store ids: variants, inventory items, images
    options: Mapped[dict] = mapped_column(JSON, default=dict)            # per-product overrides of the store defaults
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    product: Mapped[Product] = relationship()


class BomFilament(TenantMixin, Base):
    """Filament needed per unit. If follows_variant, the color comes from the ordered variant."""
    __tablename__ = "bom_filament"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    slot: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str | None] = mapped_column(String(80))     # e.g. "Body", "Accent"
    follows_variant: Mapped[bool] = mapped_column(Boolean, default=False)
    material: Mapped[str] = mapped_column(String(40), default="PLA")
    color_name: Mapped[str | None] = mapped_column(String(80))
    color_hex: Mapped[str | None] = mapped_column(String(9))
    spoolman_filament_id: Mapped[int | None] = mapped_column(Integer)
    grams: Mapped[float] = mapped_column(Float, default=0)
    waste_pct: Mapped[float] = mapped_column(Float, default=5)
    product: Mapped[Product] = relationship(back_populates="filaments")


class BomPart(TenantMixin, Base):
    """Non-filament component per unit, backed by an InvenTree part."""
    __tablename__ = "bom_part"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variant.id", ondelete="CASCADE"))
    inventree_part_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    qty: Mapped[float] = mapped_column(Float, default=1)
    note: Mapped[str | None] = mapped_column(String(300))
    product: Mapped[Product] = relationship(back_populates="parts")


class PartCache(TenantMixin, Base):
    __tablename__ = "part_cache"
    __table_args__ = (UniqueConstraint("team_id", "inventree_part_id", name="uq_part_cache_team_part"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    inventree_part_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    ipn: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(200))
    units: Mapped[str | None] = mapped_column(String(40))
    in_stock: Mapped[float] = mapped_column(Float, default=0)
    allocated: Mapped[float] = mapped_column(Float, default=0)
    on_order: Mapped[float] = mapped_column(Float, default=0)
    minimum_stock: Mapped[float] = mapped_column(Float, default=0)
    image: Mapped[str | None] = mapped_column(String(500))
    suppliers: Mapped[list] = mapped_column(JSON, default=list)   # [{name, sku, link, pack_qty, price}]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SpoolCache(TenantMixin, Base):
    __tablename__ = "spool_cache"
    __table_args__ = (UniqueConstraint("team_id", "spool_id", name="uq_spool_cache_team_spool"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(Integer)
    filament_id: Mapped[int | None] = mapped_column(Integer)
    vendor: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    material: Mapped[str | None] = mapped_column(String(40))
    color_hex: Mapped[str | None] = mapped_column(String(9))
    remaining_g: Mapped[float] = mapped_column(Float, default=0)
    spool_weight_g: Mapped[float | None] = mapped_column(Float)
    location: Mapped[str | None] = mapped_column(String(120))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FilamentCatalog(TenantMixin, Base):
    """Filaments known to Spoolman (spool or not); used for picking and shopping lists."""
    __tablename__ = "filament_catalog"
    __table_args__ = (UniqueConstraint("team_id", "filament_id", name="uq_filament_catalog_team_filament"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    filament_id: Mapped[int] = mapped_column(Integer)
    vendor: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    material: Mapped[str | None] = mapped_column(String(40))
    color_hex: Mapped[str | None] = mapped_column(String(9))
    weight_g: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    article_number: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(1000))


class Order(TenantMixin, Base):
    __tablename__ = "shop_order"
    __table_args__ = (UniqueConstraint("team_id", "channel", "ext_id", name="uq_shop_order_team_channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(12), default="woo")       # woo | shopify | etsy (services.stores)
    ext_id: Mapped[str | None] = mapped_column(String(40))                # the order's id in that store
    woo_order_id: Mapped[int | None] = mapped_column(Integer)
    number: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    customer: Mapped[str | None] = mapped_column(String(200))
    total: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)      # parts deducted in InvenTree
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consume_error: Mapped[str | None] = mapped_column(Text)
    routed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # print jobs sent to the farm
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # every print job completed
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))       # when it should ship
    late_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


@sa_event.listens_for(Order, "before_insert")
def _order_ext_id(mapper, connection, target):
    """A Woo order is known by its Woo id: ext_id carries it too, so one unique key covers every store."""
    if target.ext_id is None and target.woo_order_id is not None:
        target.ext_id = str(target.woo_order_id)
    if not target.channel:
        target.channel = "woo"


class OrderLine(TenantMixin, Base):
    __tablename__ = "shop_order_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("shop_order.id", ondelete="CASCADE"))
    woo_line_id: Mapped[int | None] = mapped_column(Integer)
    ext_line_id: Mapped[str | None] = mapped_column(String(60))          # the line's id in a store other than Woo
    sku: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variant.id", ondelete="SET NULL"))
    farm_job_ids: Mapped[list] = mapped_column(JSON, default=list)       # farm jobs printing this line
    farm_error: Mapped[str | None] = mapped_column(Text)
    order: Mapped[Order] = relationship(back_populates="lines")


class Job(TenantMixin, Base):
    """Background work + audit trail. The worker claims rows with SKIP LOCKED."""
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_state_run", "state", "run_after"), Index("ix_job_team_state", "team_id", "state"))
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))
    ref: Mapped[str | None] = mapped_column(String(120))        # e.g. "model:12"
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="queued")   # queued | running | done | failed
    priority: Mapped[int] = mapped_column(Integer, default=5)            # lower runs first
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Event(TenantMixin, Base):
    """Human-readable history per model/product."""
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(120), index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    hash: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["read", "write"])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Mobile API keys (2026-10-21): an optional expiry, and an app instance the key is tied to. With bind_instance the
    # first request's X-Hub-Instance becomes the key's instance; afterwards only that instance may use it.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    instance_id: Mapped[str | None] = mapped_column(String(120))
    bind_instance: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(300))
    last_ip: Mapped[str | None] = mapped_column(String(64))


class OAuthClient(Base):
    """An app that registered itself to use the MCP endpoint (OAuth dynamic client registration)."""
    __tablename__ = "oauth_client"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(80), unique=True)
    secret_hash: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OAuthToken(Base):
    """Authorization codes, access tokens and refresh tokens for MCP clients (only hashes are stored)."""
    __tablename__ = "oauth_token"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))            # code | access | refresh
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"))
    hash: Mapped[str] = mapped_column(String(128), unique=True)
    client_id: Mapped[str] = mapped_column(String(80), index=True)
    grant: Mapped[str] = mapped_column(String(40), index=True)   # groups one sign-in's tokens for revocation
    user: Mapped[dict] = mapped_column(JSON, default=dict)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    redirect_uri: Mapped[str | None] = mapped_column(String(1000))
    code_challenge: Mapped[str | None] = mapped_column(String(200))
    resource: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Setting(TenantMixin, Base):
    """Per-team settings and small state. Use services.shop.setting()/set_setting(), not db.get()."""
    __tablename__ = "setting"
    __table_args__ = (UniqueConstraint("team_id", "key", name="uq_setting_team_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


# ---------------- teams ----------------

class Team(Base):
    """A workspace. Members share its models, printers, settings and connected services."""
    __tablename__ = "team"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    personal: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_prefix: Mapped[str] = mapped_column(String(120), default="")   # under library_root; "" only for the first team
    source_dirs: Mapped[dict] = mapped_column(JSON, default=dict)          # {"deltaprints": path, "inbox": path}; platform admins only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    members: Mapped[list["Membership"]] = relationship(back_populates="team", cascade="all, delete-orphan")


class Installation(Base):
    """Facts about the whole installation, in one row (id 1). The print farm is chosen here, once and explicitly,
    never inferred from which connections exist (invariant 1). First-run setup is recorded here too."""
    __tablename__ = "installation"
    id: Mapped[int] = mapped_column(primary_key=True)
    farm: Mapped[str | None] = mapped_column(String(20))               # openprinthq (the farm); None until setup connects it
    farm_source: Mapped[str | None] = mapped_column(String(20))        # setup | migrated | legacy
    setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    setup_token_hash: Mapped[str | None] = mapped_column(String(64))   # a generated first-run token, until setup completes
    installer_key_id: Mapped[int | None] = mapped_column(Integer)      # the API key setup minted (password links)
    crafts: Mapped[list | None] = mapped_column(JSON)                   # optional crafts that are on (services.crafts)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReachCheck(Base):
    """A one-time check that the hub is reachable through its public address (docs/setup-api.md). Whoever set
    the hub up asks for a token, requests it through the address being checked, and asks whether it arrived.
    Only a hash of the token is kept."""
    __tablename__ = "reach_check"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    via_proxy: Mapped[bool | None] = mapped_column(Boolean)   # arrived with X-Forwarded-* headers


class User(Base):
    __tablename__ = "hub_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    sub: Mapped[str] = mapped_column(String(200), unique=True)      # Authentik subject
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    username: Mapped[str | None] = mapped_column(String(150))
    name: Mapped[str | None] = mapped_column(String(200))
    platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prefs: Mapped[dict | None] = mapped_column(JSON)                  # personal choices: {"theme", "reduce_transparency"}
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def label(self):
        return self.name or self.username or self.email or f"user {self.id}"


ROLES = ("viewer", "member", "admin", "owner")
# What someone in a team may do. A role is a starting set; owners can tick extra permissions on or off per person.
PERMS = {
    "view": "See everything in the team",
    "models": "Add and edit models, photos, files, materials and designers",
    "slice": "Slice models and manage printer profiles",
    "inventory": "Adjust stock and create purchase orders",
    "store": "Push products to the store and set them live",
    "delete": "Permanently delete models",
    "licenses": "Record licenses and license documents",
    "settings": "Change team settings and model-site accounts",
    "connections": "Connect outside services",
    "api_keys": "Create API keys and see connected apps",
    "invite": "Invite people to the team",
    "people": "Change people's roles and permissions, remove people",
    "team": "Rename the team",
    "farm": "Run the print farm: plate checks and the job queue",
}
ROLE_PERMS = {
    "viewer": ["view"],
    "member": ["view", "models", "slice", "inventory", "farm"],
    "admin": ["view", "models", "slice", "inventory", "farm", "store", "delete", "licenses", "settings", "connections", "api_keys"],
    "owner": list(PERMS),
}
# API keys and connector sign-ins carry coarse scopes; each maps to a role's permission set.
SCOPE_PERMS = {"read": ROLE_PERMS["viewer"], "write": ROLE_PERMS["member"], "admin": ROLE_PERMS["admin"]}
ROLE_SCOPES = {"viewer": ["read"], "member": ["read", "write"], "admin": ["read", "write", "admin"],
               "owner": ["read", "write", "admin", "owner"]}


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_membership_team_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")
    perms: Mapped[list | None] = mapped_column(JSON)          # None = the role's set; owners always have all
    # The farm's printers this person may see and send to (services/printer_access.py): None = all of them, the
    # default; owners always have all.
    printers: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    team: Mapped[Team] = relationship(back_populates="members")

    @property
    def effective_perms(self) -> list[str]:
        if self.role == "owner":
            return list(PERMS)
        if self.perms is not None:
            return [p for p in PERMS if p in self.perms or p == "view"]
        return list(ROLE_PERMS.get(self.role, ["view"]))
    user: Mapped[User] = relationship(back_populates="memberships")


class PasswordLink(Base):
    """A one-time link to the hub's page where a person chooses the password of their LLDAP login: someone
    invited who had no login, or the hub's owner after the installer made theirs. Only the hash is kept."""
    __tablename__ = "password_link"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    uid: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invite(Base):
    """An invitation to the hub. kind: join (an existing team), new_team (they own a new team), individual
    (their own workspace). Authentik access is arranged when it is created (group or Authentik invitation)."""
    __tablename__ = "invite"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="join")
    team_name: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    perms: Mapped[list | None] = mapped_column(JSON)
    ak_invitation: Mapped[str | None] = mapped_column(String(64))     # Authentik invitation id, when they had no account
    ak_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(10), default="member")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="SET NULL"))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    team: Mapped[Team] = relationship()


class Connection(TenantMixin, Base):
    """A team's link to an outside service (WooCommerce, the farm, DAM, InvenTree, Spoolman, Manyfold).
    Teams can point at the same instance or their own. Secrets are encrypted at rest."""
    __tablename__ = "connection"
    __table_args__ = (UniqueConstraint("team_id", "kind", name="uq_connection_team_kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    config: Mapped[dict] = mapped_column(JSON, default=dict)        # non-secret fields
    secrets_enc: Mapped[str | None] = mapped_column(Text)           # Fernet token of a JSON dict
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_msg: Mapped[str | None] = mapped_column(String(500))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SliceResult(TenantMixin, Base):
    """The state of one model on one printer. A new slice lands in review; approving it makes its files the ones
    that printer uses. It goes stale when anything that fed it changes (files, printer, settings, slicer version)."""
    __tablename__ = "slice_result"
    __table_args__ = (UniqueConstraint("model_id", "profile_id", name="uq_slice_result_model_profile"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("printer_profile.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(12), default="queued")    # queued | slicing | review | approved | failed | stale
    inputs_hash: Mapped[str | None] = mapped_column(String(64))         # of the candidate
    approved_hash: Mapped[str | None] = mapped_column(String(64))       # of the approved files
    engine: Mapped[str | None] = mapped_column(String(10))
    engine_version: Mapped[str | None] = mapped_column(String(40))
    orientation: Mapped[dict] = mapped_column(JSON, default=dict)       # {"mode": "as_is"|"largest_face"|"auto"|"face", "normal": [x,y,z]}
    spec: Mapped[dict] = mapped_column(JSON, default=dict)              # what was sent to the slicer
    stats: Mapped[dict] = mapped_column(JSON, default=dict)             # time_s, grams, tools[], plates, tower_g, warnings[]
    file_ids: Mapped[list] = mapped_column(JSON, default=list)          # candidate files
    approved_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    approved_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    previews: Mapped[list] = mapped_column(JSON, default=list)           # file names under data_dir/previews/<id>/
    problems: Mapped[list] = mapped_column(JSON, default=list)          # [{"code": ..., "msg": ...}]
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(Integer)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    sliced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(200))
    # locked: kept exactly as approved; never marked stale, re-sliced overnight or replaced by an auto-approve
    locked: Mapped[bool | None] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(12))               # None = sliced here; "file" = a file chosen by hand
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PlateSlice(TenantMixin, Base):
    """A model resliced for one printer and a plate other than the printer's usual one (bed_type): the cache the
    plate reslicer uses so the next print of that model on that plate needs no slicing. Kept apart from
    SliceResult so it never changes the production files or the bill of materials."""
    __tablename__ = "plate_slice"
    __table_args__ = (UniqueConstraint("model_id", "profile_id", "plate", name="uq_plate_slice_model_profile_plate"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("printer_profile.id", ondelete="CASCADE"), index=True)
    plate: Mapped[str] = mapped_column(String(40))                       # slicer plate type it was sliced for
    state: Mapped[str] = mapped_column(String(12), default="queued")    # queued | slicing | review | approved | failed
    inputs_hash: Mapped[str | None] = mapped_column(String(64))
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    engine: Mapped[str | None] = mapped_column(String(10))
    engine_version: Mapped[str | None] = mapped_column(String(40))
    orientation: Mapped[dict] = mapped_column(JSON, default=dict)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    file_ids: Mapped[list] = mapped_column(JSON, default=list)
    approved_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    previews: Mapped[list] = mapped_column(JSON, default=list)
    problems: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    sliced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrintRecord(TenantMixin, Base):
    """A real print of a sliced file: recorded by hand or reported by the print farm."""
    __tablename__ = "print_record"
    __table_args__ = (UniqueConstraint("team_id", "source", "external_id", name="uq_print_record_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("slice_result.id", ondelete="SET NULL"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("model.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("printer_profile.id", ondelete="SET NULL"))
    file_id: Mapped[int | None] = mapped_column(Integer)
    inputs_hash: Mapped[str | None] = mapped_column(String(64))         # which slice version was printed
    outcome: Mapped[str] = mapped_column(String(12))                    # ok | failed | cancelled
    source: Mapped[str] = mapped_column(String(20), default="manual")   # manual | farm
    external_id: Mapped[str | None] = mapped_column(String(80))
    printer_name: Mapped[str | None] = mapped_column(String(120))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(200))
    printed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FarmJob(TenantMixin, Base):
    """A print-farm job as the farm last reported it: a mirror of its queue, kept by webhooks and a periodic sync."""
    __tablename__ = "farm_job"
    __table_args__ = (UniqueConstraint("team_id", "external_id", name="uq_farm_job_ext"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    printer_name: Mapped[str | None] = mapped_column(String(120))
    priority: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(40))
    file_ref: Mapped[str | None] = mapped_column(String(80))           # the farm library file id, for reprints
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"), index=True)
    order_refs: Mapped[list] = mapped_column(JSON, default=list)
    material: Mapped[str | None] = mapped_column(String(40))
    filament_g: Mapped[float | None] = mapped_column(Float)            # the slicer's estimate for the whole job
    fil_plan: Mapped[list | None] = mapped_column(JSON)                # [{type, color, g}] per filament, from the file
    spools: Mapped[list | None] = mapped_column(JSON)                  # [{sm, wt, g}] spools loaded when it started
    used_g: Mapped[float | None] = mapped_column(Float)                # best known filament actually used
    used_source: Mapped[str | None] = mapped_column(String(12))        # printer | weighed | progress | estimate
    est_time_s: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[float | None] = mapped_column(Float)
    waiting_reason: Mapped[str | None] = mapped_column(Text)
    # plate reslicer state: {"state": slicing|swapped|review|unsuitable|no_source|failed, "printer", "plate",
    # "plate_slice_id", "msg", "at"} (services/plate_reslice.py)
    plate_reslice: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    last_event: Mapped[str | None] = mapped_column(String(60))
    created_ext: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PlateCheck(TenantMixin, Base):
    """After a print ends the printer waits for someone to clear the plate. The check records what came off it
    (passed, or failed and why) and, on request, tells the farm the printer is ready again."""
    __tablename__ = "plate_check"
    id: Mapped[int] = mapped_column(primary_key=True)
    printer_name: Mapped[str] = mapped_column(String(120), index=True)
    farm_job_id: Mapped[int | None] = mapped_column(ForeignKey("farm_job.id", ondelete="SET NULL"))
    external_job_id: Mapped[str | None] = mapped_column(String(40))
    job_name: Mapped[str | None] = mapped_column(String(400))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    job_outcome: Mapped[str | None] = mapped_column(String(12))        # completed | failed | cancelled (as the farm saw it)
    state: Mapped[str] = mapped_column(String(12), default="waiting")  # waiting | passed | failed | skipped
    reason: Mapped[str | None] = mapped_column(String(40))             # failure reason code
    note: Mapped[str | None] = mapped_column(Text)
    reprint_job_id: Mapped[str | None] = mapped_column(String(40))
    ready_set: Mapped[bool] = mapped_column(Boolean, default=False)
    ready_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # staggered: Ready will be set then
    ready_error: Mapped[str | None] = mapped_column(Text)
    photo: Mapped[str | None] = mapped_column(String(200))                       # finish photo, under data_dir/farm
    checked_by: Mapped[str | None] = mapped_column(String(200))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SpoolWeighIn(TenantMixin, Base):
    """A spool weighed (or corrected) in Spoolman: what the prints since the last weigh-in were thought to use, what
    the scale says they used, and the factor applied to those prints' filament."""
    __tablename__ = "spool_weigh_in"
    id: Mapped[int] = mapped_column(primary_key=True)
    spoolman_id: Mapped[int] = mapped_column(Integer, index=True)
    remaining_g: Mapped[float | None] = mapped_column(Float)
    tracked_g: Mapped[float | None] = mapped_column(Float)              # use recorded for the jobs on this spool
    measured_g: Mapped[float | None] = mapped_column(Float)             # tracked + what the scale adds or takes away
    factor: Mapped[float | None] = mapped_column(Float)
    jobs: Mapped[list] = mapped_column(JSON, default=list)              # farm_job ids adjusted
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaintPlan(TenantMixin, Base):
    """Recurring maintenance for a printer: every so many print hours and/or days. A task opens when it is due."""
    __tablename__ = "maint_plan"
    id: Mapped[int] = mapped_column(primary_key=True)
    printer_name: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200))
    every_hours: Mapped[float | None] = mapped_column(Float)          # print hours
    every_days: Mapped[float | None] = mapped_column(Float)
    parts: Mapped[list] = mapped_column(JSON, default=list)           # [{part_id, qty, name}] InvenTree spare parts
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    farm_schedule_id: Mapped[int | None] = mapped_column(Integer)          # the linked farm maintenance item
    farm_status: Mapped[str | None] = mapped_column(String(20))            # ok | due_soon | overdue | no_data
    farm_hours_since: Mapped[float | None] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaintTask(TenantMixin, Base):
    """One maintenance job on a printer: by hand, from a plan, or opened by a printer alarm."""
    __tablename__ = "maint_task"
    id: Mapped[int] = mapped_column(primary_key=True)
    printer_name: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(12), default="manual")   # manual | plan | alarm
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("maint_plan.id", ondelete="SET NULL"))
    alarm_code: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(12), default="open")      # open | done | dismissed
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parts: Mapped[list] = mapped_column(JSON, default=list)             # planned spare parts [{part_id, qty, name}]
    parts_used: Mapped[list] = mapped_column(JSON, default=list)
    parts_error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(200))
    out_of_service: Mapped[bool] = mapped_column(Boolean, default=False)   # printer marked out of service in the farm
    farm_note: Mapped[str | None] = mapped_column(Text)                      # what was recorded in the farm


class Quote(TenantMixin, Base):
    """An instant quote for a customer's own model, from the store. Sliced by the hub, priced from the slice; once
    paid it waits for review before it goes to the farm."""
    __tablename__ = "quote"
    __table_args__ = (UniqueConstraint("token", name="uq_quote_token"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="slicing")   # slicing | ready | failed | ordered | approved | refused | expired
    filename: Mapped[str] = mapped_column(String(255))
    stored: Mapped[str] = mapped_column(String(300))                      # under data_dir/quotes
    bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    printer_profile_id: Mapped[int | None] = mapped_column(ForeignKey("printer_profile.id", ondelete="SET NULL"))
    size_mm: Mapped[list | None] = mapped_column(JSON)
    grams: Mapped[float | None] = mapped_column(Float)                     # as sliced (first offered material)
    time_s: Mapped[int | None] = mapped_column(Integer)
    sliced_material: Mapped[str | None] = mapped_column(String(40))
    previews: Mapped[list] = mapped_column(JSON, default=list)
    material_key: Mapped[str | None] = mapped_column(String(40))          # the customer's choice
    colour: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("shop_order.id", ondelete="SET NULL"))
    order_line_id: Mapped[int | None] = mapped_column(Integer)
    farm_file_ref: Mapped[str | None] = mapped_column(String(80))
    farm_job_ids: Mapped[list] = mapped_column(JSON, default=list)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(TenantMixin, Base):
    """One problem or update the hub raised (services/alert_inbox.py): opened from a notification, an email or the
    dashboard, then fixed or dismissed. `ref` ties repeats of the same problem to one record."""
    __tablename__ = "alert"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    ref: Mapped[str | None] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    subject: Mapped[dict | None] = mapped_column(JSON)        # what it is about: {"job": ..} or {"printer": ..}
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(12), default="open", index=True)   # open | fixed | dismissed | resolved
    count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(150))
    fix: Mapped[str | None] = mapped_column(String(300))


class PushDevice(TenantMixin, Base):
    """A phone that gets alerts through the push relay (services/push.py). The hub keeps the phone's public key and
    its push token; the private key never leaves the phone. Removed when the person turns notifications off, removes
    the phone, or the push service says the token is gone."""
    __tablename__ = "push_device"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"), index=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_key.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(8))                 # apns | fcm
    token: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    public_key: Mapped[str] = mapped_column(String(64))              # X25519, base64url
    apns_env: Mapped[str | None] = mapped_column(String(12))         # production | sandbox
    apns_topic: Mapped[str | None] = mapped_column(String(200))
    label: Mapped[str | None] = mapped_column(String(120))
    app_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))


class PhonePairing(Base):
    """A one-time QR pairing for a phone (2026-10-22). The QR carries a random code (only its hash is stored); the phone
    shows what it will connect to, and only when the person confirms does the hub make a key tied to that phone.
    Not team-scoped on purpose: the phone looks it up by code before it has any key."""
    __tablename__ = "phone_pairing"
    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"))
    made_by: Mapped[str | None] = mapped_column(String(200))
    access: Mapped[str] = mapped_column(String(10), default="act")          # view | act | all
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    key_days: Mapped[int | None] = mapped_column(Integer)                  # the key's life; None = no expiry
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # the QR's life (10 minutes)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    device: Mapped[str | None] = mapped_column(String(120))
    instance_id: Mapped[str | None] = mapped_column(String(120))
    key_id: Mapped[int | None] = mapped_column(ForeignKey("api_key.id", ondelete="SET NULL"))


class TvLink(Base):
    """A TV signing in (2026-11-08): the TV shows a short code, someone approves it on the hub or the phone app, and
    the TV, asking every few seconds, gets its own view-only key exactly once. The key is made when the TV collects
    it, so no key is ever stored. Only the TV's secret's hash is kept. Not team-scoped: the approver picks the team."""
    __tablename__ = "tv_link"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_code: Mapped[str] = mapped_column(String(9), index=True)          # XXXX-XXXX, shown on the TV
    device: Mapped[str] = mapped_column(String(60), default="TV")         # Apple TV, Android TV
    name: Mapped[str | None] = mapped_column(String(120))                 # "Print room TV"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 10 minutes to approve
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("hub_user.id", ondelete="CASCADE"))
    approved_by: Mapped[str | None] = mapped_column(String(200))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[int | None] = mapped_column(ForeignKey("api_key.id", ondelete="SET NULL"))


class FilamentBarcode(TenantMixin, Base):
    """A product barcode (UPC/EAN) on a filament box or spool, learned when someone confirms what it is (2026-10-23).
    filament_id is the filament in the team's spool library (the farm's Spoolman when OpenPrintHQ is the farm)."""
    __tablename__ = "filament_barcode"
    __table_args__ = (UniqueConstraint("team_id", "barcode", name="uq_filament_barcode_team_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    barcode: Mapped[str] = mapped_column(String(32))
    filament_id: Mapped[int | None] = mapped_column(Integer)
    vendor: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(200))
    material: Mapped[str | None] = mapped_column(String(40))
    color_hex: Mapped[str | None] = mapped_column(String(9))
    weight_g: Mapped[float | None] = mapped_column(Float)
    diameter_mm: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="learned")     # learned | upc | migrated
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class UpcLookup(Base):
    """Online UPC/EAN lookups (UPCitemdb), cached for every team: public product data, never team data."""
    __tablename__ = "upc_lookup"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FarmLibraryMigration(TenantMixin, Base):
    """What spools.migrate_to_farm copied from the hub's own Spoolman into the farm's library: one row per vendor,
    filament or spool, so a second run skips what is already there (2026-10-23). The engine's API has no way to
    write Spoolman extra fields, so hub_migrated_from lives here instead."""
    __tablename__ = "farm_library_migration"
    __table_args__ = (UniqueConstraint("team_id", "kind", "source_id", name="uq_farm_lib_mig_src"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))            # vendor | filament | spool
    source_id: Mapped[int] = mapped_column(Integer)          # id in the hub's Spoolman
    farm_id: Mapped[int | None] = mapped_column(Integer)     # id in the farm library
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrcaShare(Base):
    """A shared folder for OrcaSlicer in the browser (routers/shares.py): each person's own desktop opens it under
    Files, Shared, read-only or read and write by its two LLDAP groups. One installation, one team: not per team.
    Marked for removal (deleting), it stays until the installation removed its files and forgot it."""
    __tablename__ = "orca_share"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    deleting: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
