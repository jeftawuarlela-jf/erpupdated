"""
Pricing Monitor — Streamlit App
================================
Monthly ERP price change processor.

THREE shortlist types with priority: Discount > COGS Increase > Revert
If a SKU+UOM appears in multiple lists, highest priority wins.

  ┌──────────────────┬─────────────────────┬──────────────────────────────────┬─────────────────┐
  │ Type             │ Item Price          │ Pricing Rule Margin              │ valid_upto      │
  ├──────────────────┼─────────────────────┼──────────────────────────────────┼─────────────────┤
  │ 🟢 Discount      │ Promo Price         │ Non Member − Promo Price         │ Last day of mth │
  │ 🔴 COGS Increase │ Next Member         │ Next Non Member − Next Member    │ Blank (forever) │
  │ 🔵 Revert        │ Member Price        │ Non Member − Member Price        │ Blank (forever) │
  └──────────────────┴─────────────────────┴──────────────────────────────────┴─────────────────┘

Matching key: SKU + UOM Inofarma (one row per pair).

Output schemas match ERP bulk import exactly:
  1. item_price_updates.csv   → name, Item Code, Item Name, UOM, Price List, Valid Upto
  2. item_price_inserts.csv   → Item Name, UOM, Rate, Price List, Valid From, Valid Upto
  3. pricing_rule_updates.csv → name, Valid Up, Item Code (Apply Rule On),
                                 UOM (Apply Rule On Item Code)
  4. pricing_rule_inserts.csv → ID (blank), Title, Disable, Apply On, Price or Product,
                                 Currency, Margin Rate or Amount, Margin Type,
                                 Valid From, Valid Upto
"""

import calendar
import io
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Pricing Monitor", page_icon="💰", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    .stTabs [data-baseweb="tab"] { font-size: 13px; font-weight: 600; }
    .section-header {
        font-size: 12px; font-weight: 700; letter-spacing: .07em;
        text-transform: uppercase; color: #888; margin: 16px 0 6px 0;
    }
</style>
""", unsafe_allow_html=True)

st.title("💰 Pricing Monitor")
st.caption("Monthly ERP price change processor — INSERT / UPDATE classifier for bulk import")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def last_day_of_month(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def load_csv(uploaded) -> pd.DataFrame:
    df = pd.read_csv(uploaded, dtype=str)
    df.columns = df.columns.str.strip()
    df = df.fillna("")
    return df


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def find_active_records(df: pd.DataFrame, as_of: date,
                        from_col: str, upto_col: str) -> pd.DataFrame:
    """Rows where valid_from <= as_of AND (valid_upto blank OR valid_upto >= as_of)."""
    df = df.copy()
    df["_from"] = pd.to_datetime(df[from_col].replace("", pd.NaT), errors="coerce").dt.date
    df["_upto"] = pd.to_datetime(df[upto_col].replace("", pd.NaT), errors="coerce").dt.date
    mask = (
        df["_from"].notna() &
        (df["_from"] <= as_of) &
        (df["_upto"].isna() | (df["_upto"] >= as_of))
    )
    result = df[mask].copy()
    result.drop(columns=["_from", "_upto"], inplace=True)
    return result


def detect_col(df: pd.DataFrame, *keyword_sets) -> str:
    """Return first column whose lowercase name contains ALL keywords in any keyword set."""
    for keywords in keyword_sets:
        for col in df.columns:
            if all(k in col.lower() for k in keywords):
                return col
    return ""


def get_val(row: pd.Series, *candidates) -> str:
    """Return first non-empty value from candidate column names."""
    for c in candidates:
        if c in row.index and str(row[c]).strip():
            return str(row[c]).strip()
    return ""


def clean_float(val: str) -> float:
    return float(str(val).replace(",", "").strip())


# ─── Session state ────────────────────────────────────────────────────────────
for k in ["results", "processed"]:
    if k not in st.session_state:
        st.session_state[k] = None

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    st.markdown('<div class="section-header">ERP Exports — current active data</div>',
                unsafe_allow_html=True)
    up_prices = st.file_uploader("Item Price CSV (member)", type="csv", key="prices")
    up_rules  = st.file_uploader("Pricing Rule CSV (non-member)", type="csv", key="rules")

    st.markdown('<div class="section-header">Monthly Shortlists</div>',
                unsafe_allow_html=True)
    up_marketing = st.file_uploader(
        "🟢 Marketing / Discount list", type="csv", key="mkt",
        help="Columns: SKU, Product Name, UOM Inofarma, Member Price, Non Member, Promo Price"
    )
    up_cogs = st.file_uploader(
        "🔴 COGS Increase list", type="csv", key="cogs",
        help="Columns: SKU ID, Product Name, UOM Inofarma, Previous Member, Previous Non Member, Next Member, Next Non Member"
    )
    up_revert = st.file_uploader(
        "🔵 Revert list (back to original price)", type="csv", key="revert",
        help="Columns: SKU, Product Name, UOM Inofarma, Member Price, Non Member"
    )

    st.divider()
    st.markdown('<div class="section-header">Parameters</div>', unsafe_allow_html=True)

    valid_from = st.date_input(
        "New prices Valid From",
        value=date.today().replace(day=1),
        help="First day of the new pricing period (e.g. 2026-04-01)"
    )
    close_date    = valid_from - timedelta(days=1)
    discount_upto = last_day_of_month(valid_from)

    st.info(
        f"**Closing date** (existing records): `{close_date}`\n\n"
        f"**Discount valid_upto**: `{discount_upto}`\n\n"
        f"**COGS / Revert valid_upto**: *(blank — forever)*"
    )

    price_list_name = st.text_input(
        "Member Price List Name",
        value="New Jakarta Selling Price List"
    )

    st.divider()
    all_ready = all([up_prices, up_rules, up_marketing, up_cogs, up_revert])
    run_btn = st.button(
        "▶ Run Analysis", type="primary",
        use_container_width=True, disabled=not all_ready
    )
    if not all_ready:
        st.caption("Upload all 5 CSV files to enable.")

# ─── Processing ───────────────────────────────────────────────────────────────

if run_btn:
    with st.spinner("Processing..."):

        prices    = load_csv(up_prices)
        rules     = load_csv(up_rules)
        marketing = load_csv(up_marketing)
        cogs_sl   = load_csv(up_cogs)
        revert_sl = load_csv(up_revert)

        # ── Detect ERP export column names ────────────────────────────────

        # Item Price export — exact match for 'name' first, then fallback
        ip_id_col   = "name" if "name" in prices.columns else detect_col(prices, ["id"]) or prices.columns[0]
        ip_from_col = detect_col(prices, ["valid", "from"])
        ip_upto_col = detect_col(prices, ["valid", "upto"], ["valid", "up"])
        ip_code_col = detect_col(prices, ["item", "code"])
        ip_name_col = detect_col(prices, ["item", "name"])
        ip_uom_col  = detect_col(prices, ["uom"])
        ip_pl_col   = detect_col(prices, ["price", "list"])

        # Pricing Rule export — exact match for 'name' first, then fallback
        pr_id_col   = "name" if "name" in rules.columns else detect_col(rules, ["id"]) or rules.columns[0]
        pr_from_col = detect_col(rules, ["valid", "from"])
        pr_upto_col = detect_col(rules, ["valid", "upto"], ["valid", "up"])
        pr_code_col = detect_col(rules, ["item", "code"])
        pr_uom_col  = detect_col(rules, ["uom"])

        # Guard: abort if critical columns missing
        missing = [
            lbl for lbl, val in [
                ("Item Price → valid_from",  ip_from_col),
                ("Item Price → valid_upto",  ip_upto_col),
                ("Item Price → item_code",   ip_code_col),
                ("Item Price → uom",         ip_uom_col),
                ("Pricing Rule → valid_from",pr_from_col),
                ("Pricing Rule → valid_upto",pr_upto_col),
                ("Pricing Rule → item_code", pr_code_col),
                ("Pricing Rule → uom",       pr_uom_col),
            ] if not val
        ]
        if missing:
            st.error(
                "Could not detect these columns — check your ERP export headers:\n" +
                "\n".join(f"• {m}" for m in missing)
            )
            st.stop()

        # ── Build active record lookup: (SKU_UPPER, UOM_UPPER) → groups ──
        active_prices = find_active_records(prices, valid_from, ip_from_col, ip_upto_col)
        active_rules  = find_active_records(rules,  valid_from, pr_from_col, pr_upto_col)

        def build_index(df, code_col, uom_col):
            df = df.copy()
            df["_key"] = list(zip(
                df[code_col].str.strip().str.upper(),
                df[uom_col].str.strip().str.upper()
            ))
            return df.groupby("_key")

        ap_grp = build_index(active_prices, ip_code_col, ip_uom_col)
        ar_grp = build_index(active_rules,  pr_code_col, pr_uom_col)

        # ── Build shortlist priority map ───────────────────────────────────
        # Each entry: (sku, uom) → dict with type, new_price, new_margin, v_upto, item_name
        # Priority: discount(3) > cogs(2) > revert(1)
        priority_map: dict[tuple, dict] = {}

        def uom_col_from(df):
            return (
                detect_col(df, ["uom", "inofarma"]) or
                detect_col(df, ["uom", "infa"]) or
                detect_col(df, ["uom"])
            )

        # ── 1. Load REVERT list (priority 1 — lowest) ─────────────────────
        rv_uom_col  = uom_col_from(revert_sl)
        rv_sku_col  = detect_col(revert_sl, ["sku"])
        rv_name_col = detect_col(revert_sl, ["product", "name"]) or detect_col(revert_sl, ["item", "name"])
        rv_m_col    = detect_col(revert_sl, ["member", "price"], ["member"])
        rv_nm_col   = detect_col(revert_sl, ["non", "member"])

        rv_missing = [lbl for lbl, val in [
            ("Revert → SKU",          rv_sku_col),
            ("Revert → UOM Inofarma", rv_uom_col),
            ("Revert → Member Price", rv_m_col),
            ("Revert → Non Member",   rv_nm_col),
        ] if not val]
        if rv_missing:
            st.error("Revert list — could not detect:\n" + "\n".join(f"• {m}" for m in rv_missing))
            st.stop()

        for _, row in revert_sl.iterrows():
            sku       = get_val(row, rv_sku_col)
            item_name = get_val(row, rv_name_col)
            uom       = get_val(row, rv_uom_col)
            m_price   = get_val(row, rv_m_col)
            nm_price  = get_val(row, rv_nm_col)
            if not sku or not uom or not m_price or not nm_price:
                continue
            try:
                m_f  = clean_float(m_price)
                nm_f = clean_float(nm_price)
            except ValueError:
                continue
            key = (sku.upper(), uom.upper())
            priority_map[key] = {
                "priority":  1,
                "type":      "revert",
                "sku":       sku,
                "item_name": item_name,
                "uom":       uom,
                "new_price": m_f,
                "new_margin": nm_f - m_f,          # Non Member − Member Price
                "v_upto":    "",                    # blank = forever
            }

        # ── 2. Load COGS INCREASE list (priority 2) ────────────────────────
        cogs_uom_col  = uom_col_from(cogs_sl)
        cogs_sku_col  = detect_col(cogs_sl, ["sku"])
        cogs_name_col = detect_col(cogs_sl, ["product", "name"]) or detect_col(cogs_sl, ["item", "name"])
        cogs_nm_col   = detect_col(cogs_sl, ["next", "member"])
        cogs_nnm_col  = detect_col(cogs_sl, ["next", "non"])

        cogs_missing = [lbl for lbl, val in [
            ("COGS → SKU",             cogs_sku_col),
            ("COGS → UOM Inofarma",    cogs_uom_col),
            ("COGS → Next Member",     cogs_nm_col),
            ("COGS → Next Non Member", cogs_nnm_col),
        ] if not val]
        if cogs_missing:
            st.error("COGS Increase list — could not detect:\n" + "\n".join(f"• {m}" for m in cogs_missing))
            st.stop()

        for _, row in cogs_sl.iterrows():
            sku       = get_val(row, cogs_sku_col)
            item_name = get_val(row, cogs_name_col)
            uom       = get_val(row, cogs_uom_col)
            next_m    = get_val(row, cogs_nm_col)
            next_nm   = get_val(row, cogs_nnm_col)
            if not sku or not uom or not next_m or not next_nm:
                continue
            try:
                nm_f  = clean_float(next_m)
                nnm_f = clean_float(next_nm)
            except ValueError:
                continue
            key = (sku.upper(), uom.upper())
            if key not in priority_map or priority_map[key]["priority"] < 2:
                priority_map[key] = {
                    "priority":  2,
                    "type":      "cogs",
                    "sku":       sku,
                    "item_name": item_name,
                    "uom":       uom,
                    "new_price": nm_f,
                    "new_margin": nnm_f - nm_f,    # Next Non Member − Next Member
                    "v_upto":    "",                # blank = forever
                }

        # ── 3. Load MARKETING / DISCOUNT list (priority 3 — highest) ──────
        mkt_uom_col   = uom_col_from(marketing)
        mkt_sku_col   = detect_col(marketing, ["sku"])
        mkt_name_col  = detect_col(marketing, ["product", "name"]) or detect_col(marketing, ["item", "name"])
        mkt_nm_col    = detect_col(marketing, ["non", "member"])
        mkt_price_col = detect_col(marketing, ["promo", "price"], ["promo"])

        mkt_missing = [lbl for lbl, val in [
            ("Marketing → SKU",         mkt_sku_col),
            ("Marketing → UOM Inofarma",mkt_uom_col),
            ("Marketing → Non Member",  mkt_nm_col),
            ("Marketing → Promo Price", mkt_price_col),
        ] if not val]
        if mkt_missing:
            st.error("Marketing list — could not detect:\n" + "\n".join(f"• {m}" for m in mkt_missing))
            st.stop()

        for _, row in marketing.iterrows():
            sku       = get_val(row, mkt_sku_col)
            item_name = get_val(row, mkt_name_col)
            uom       = get_val(row, mkt_uom_col)
            nm_price  = get_val(row, mkt_nm_col)
            promo     = get_val(row, mkt_price_col)
            if not sku or not uom or not promo or not nm_price:
                continue
            try:
                promo_f = clean_float(promo)
                nm_f    = clean_float(nm_price)
            except ValueError:
                continue
            key = (sku.upper(), uom.upper())
            # Always overwrite — discount is highest priority
            priority_map[key] = {
                "priority":  3,
                "type":      "discount",
                "sku":       sku,
                "item_name": item_name,
                "uom":       uom,
                "new_price": promo_f,
                "new_margin": nm_f - promo_f,      # Non Member − Promo Price
                "v_upto":    str(discount_upto),   # last day of promo month
            }

        # ── Output accumulators ───────────────────────────────────────────
        item_updates = []
        item_inserts = []
        rule_updates = []
        rule_inserts = []
        summary      = []

        # ── Process each resolved entry ────────────────────────────────────
        type_labels = {
            "discount": "🟢 Discount",
            "cogs":     "🔴 COGS Increase",
            "revert":   "🔵 Revert",
        }

        for key, entry in priority_map.items():
            sku       = entry["sku"]
            item_name = entry["item_name"]
            uom       = entry["uom"]
            new_price = entry["new_price"]
            new_margin= entry["new_margin"]
            v_upto    = entry["v_upto"]
            ptype     = entry["type"]

            # ── Item Price ─────────────────────────────────────────────────
            item_action = "INSERT"
            if key in ap_grp.groups:
                item_action = "UPDATE"
                for _, existing in ap_grp.get_group(key).iterrows():
                    item_updates.append({
                        "name":       existing.get(ip_id_col, ""),
                        "Item Code":  sku,
                        "Item Name":  existing.get(ip_name_col, item_name),
                        "UOM":        uom,
                        "Price List": existing.get(ip_pl_col, price_list_name),
                        "Valid Upto": str(close_date),
                    })

            item_inserts.append({
                "Item Code": sku,
                "Item Name":  item_name,
                "UOM":        uom,
                "Rate":       new_price,
                "Price List": price_list_name,
                "Valid From": str(valid_from),
                "Valid Upto": v_upto,
            })

            # ── Pricing Rule ───────────────────────────────────────────────
            rule_action = "INSERT"
            if key in ar_grp.groups:
                rule_action = "UPDATE"
                for _, existing in ar_grp.get_group(key).iterrows():
                    rule_updates.append({
                        "name":                          existing.get(pr_id_col, ""),
                        "Valid Up":                      str(close_date),
                        "Item Code (Apply Rule On)":     sku,
                        "UOM (Apply Rule On Item Code)": uom,
                    })

            rule_inserts.append({
                "ID":                    "",
                "Item Code":             sku,
                "Title":                 f"NON MEMBER MARGIN {sku} {uom}",
                "Disable":               0,
                "Apply On":              "Item Code",
                "Price or Product":      "Price",
                "Currency":              "IDR",
                "Margin Rate or Amount": new_margin,
                "Margin Type":           "Amount",
                "Valid From":            str(valid_from),
                "Valid Upto":            v_upto,
            })

            summary.append({
                "SKU":         sku,
                "Item Name":   item_name,
                "UOM":         uom,
                "Type":        type_labels[ptype],
                "New Price":   new_price,
                "New Margin":  new_margin,
                "Valid Upto":  v_upto if v_upto else "(blank — forever)",
                "Item Action": item_action,
                "Rule Action": rule_action,
            })

        st.session_state.results = {
            "item_updates":  pd.DataFrame(item_updates),
            "item_inserts":  pd.DataFrame(item_inserts),
            "rule_updates":  pd.DataFrame(rule_updates),
            "rule_inserts":  pd.DataFrame(rule_inserts),
            "summary":       pd.DataFrame(summary),
        }
        st.session_state.processed = True

# ─── Display results ──────────────────────────────────────────────────────────

if st.session_state.processed and st.session_state.results:
    R  = st.session_state.results
    sm = R["summary"]

    # KPI strip
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Total rows",    len(sm))
    k2.metric("🟢 Discount",   len(sm[sm["Type"].str.contains("Discount")]))
    k3.metric("🔴 COGS",       len(sm[sm["Type"].str.contains("COGS")]))
    k4.metric("🔵 Revert",     len(sm[sm["Type"].str.contains("Revert")]))
    k5.metric("Item UPDATEs",  len(sm[sm["Item Action"] == "UPDATE"]))
    k6.metric("Item INSERTs",  len(sm[sm["Item Action"] == "INSERT"]))

    st.divider()

    # ── Downloads ─────────────────────────────────────────────────────────────
    st.markdown("### 📥 Download Import Files")
    st.caption("**Order matters in ERP:** run UPDATES first, then INSERTS.")

    col_upd, col_ins = st.columns(2)
    with col_upd:
        st.markdown("**🟡 Step 1 — UPDATES (close existing records)**")
        u1, u2 = st.columns(2)
        with u1:
            if not R["item_updates"].empty:
                st.download_button("Item Price UPDATES",
                    data=to_csv_bytes(R["item_updates"]),
                    file_name="item_price_updates.csv",
                    mime="text/csv", use_container_width=True)
                st.caption(f"{len(R['item_updates'])} rows")
            else:
                st.info("No item price updates")
        with u2:
            if not R["rule_updates"].empty:
                st.download_button("Pricing Rule UPDATES",
                    data=to_csv_bytes(R["rule_updates"]),
                    file_name="pricing_rule_updates.csv",
                    mime="text/csv", use_container_width=True)
                st.caption(f"{len(R['rule_updates'])} rows")
            else:
                st.info("No rule updates")

    with col_ins:
        st.markdown("**🟢 Step 2 — INSERTS (new records)**")
        i1, i2 = st.columns(2)
        with i1:
            if not R["item_inserts"].empty:
                st.download_button("Item Price INSERTS",
                    data=to_csv_bytes(R["item_inserts"]),
                    file_name="item_price_inserts.csv",
                    mime="text/csv", use_container_width=True)
                st.caption(f"{len(R['item_inserts'])} rows")
        with i2:
            if not R["rule_inserts"].empty:
                st.download_button("Pricing Rule INSERTS",
                    data=to_csv_bytes(R["rule_inserts"]),
                    file_name="pricing_rule_inserts.csv",
                    mime="text/csv", use_container_width=True)
                st.caption(f"{len(R['rule_inserts'])} rows — ⚠️ assign ID manually")

    st.divider()

    # ── Detail tabs ───────────────────────────────────────────────────────────
    st.markdown("### 🔍 Preview Output Data")
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋 Summary",
        "🟡 Item Price UPDATE",
        "🟢 Item Price INSERT",
        "🟡 Rule UPDATE",
        "🟢 Rule INSERT",
    ])

    with tab1:
        st.dataframe(
            sm, use_container_width=True, hide_index=True,
            column_config={
                "New Price":  st.column_config.NumberColumn(format="Rp {:,.0f}"),
                "New Margin": st.column_config.NumberColumn(format="Rp {:,.0f}"),
            }
        )

    with tab2:
        st.caption("Schema: **name | Item Code | Item Name | UOM | Price List | Valid Upto**")
        if not R["item_updates"].empty:
            st.dataframe(R["item_updates"], use_container_width=True, hide_index=True)
        else:
            st.info("No existing active Item Prices matched.")

    with tab3:
        st.caption("Schema: **Item Name | UOM | Rate | Price List | Valid From | Valid Upto**")
        st.dataframe(
            R["item_inserts"], use_container_width=True, hide_index=True,
            column_config={
                "Rate": st.column_config.NumberColumn(format="Rp {:,.0f}"),
                "Valid Upto": st.column_config.TextColumn(
                    help="Blank = valid forever (COGS / Revert)")
            }
        )

    with tab4:
        st.caption(
            "Schema: **name | Valid Up | Item Code (Apply Rule On) | "
            "UOM (Apply Rule On Item Code)**"
        )
        if not R["rule_updates"].empty:
            st.dataframe(R["rule_updates"], use_container_width=True, hide_index=True)
        else:
            st.info("No existing active Pricing Rules matched.")

    with tab5:
        st.caption(
            "Schema: **ID (blank) | Title | Disable | Apply On | Price or Product | "
            "Currency | Margin Rate or Amount | Margin Type | Valid From | Valid Upto**"
        )
        st.warning("⚠️ ID column is blank — assign manually before importing.")
        if not R["rule_inserts"].empty:
            st.dataframe(
                R["rule_inserts"], use_container_width=True, hide_index=True,
                column_config={
                    "Margin Rate or Amount": st.column_config.NumberColumn(format="Rp {:,.0f}"),
                    "Valid Upto": st.column_config.TextColumn(
                        help="Blank = valid forever (COGS / Revert)")
                }
            )

    st.divider()
    if st.button("🔄 Start Over"):
        st.session_state.results = None
        st.session_state.processed = False
        st.rerun()

else:
    # ── Welcome ────────────────────────────────────────────────────────────────
    st.markdown("### How to use")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
**Step 1 — Upload 5 CSVs (sidebar)**
1. `Item Price CSV` — current member prices from ERP
2. `Pricing Rule CSV` — current non-member margin rules from ERP
3. `🟢 Marketing / Discount list` — Promo Price column
4. `🔴 COGS Increase list` — Next Member + Next Non Member columns
5. `🔵 Revert list` — Member Price + Non Member columns

**Step 2 — Set parameters**
- `Valid From` — first day of new period (e.g. `2026-04-01`)
- `Price List Name` — your ERP member price list name
        """)
    with c2:
        st.markdown("""
**Step 3 — Run & Download**

**Priority when a product appears in multiple lists:**
Discount (highest) → COGS Increase → Revert (lowest)

| Type | Item Price | Pricing Rule Margin | valid_upto |
|---|---|---|---|
| 🟢 Discount | Promo Price | Non Member − Promo Price | End of month |
| 🔴 COGS | Next Member | Next NM − Next M | Blank |
| 🔵 Revert | Member Price | Non Member − Member Price | Blank |

Apply **UPDATES first**, then **INSERTS** in ERP.
Pricing Rule INSERTS: **assign ID manually**.
        """)
    st.info("💡 Upload all 5 CSV files in the sidebar to get started.")
