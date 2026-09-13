import pathlib
p = pathlib.Path("src/tracker/collect.py")
s = p.read_text(encoding="utf-8")

patch1 = '    ap.add_argument("--dry-run", action="store_true")'
replace1 = """    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", action="store_true",
        help="store every manual reading on file, not only the most recent")"""
s = s.replace(patch1, replace1)

patch2 = '    return run(args.config, args.db, args.source, args.dry_run)'
replace2 = """    if args.backfill:
        return backfill(args.config, args.db)
    return run(args.config, args.db, args.source, args.dry_run)"""
s = s.replace(patch2, replace2)

backfill_fn = '''

def backfill(config_path, db_path):
    from .sources import ManualSource
    cfg = load_config(config_path)
    source = ManualSource()
    stored = skipped = 0
    with st.connect(db_path) as conn:
        run_id = st.start_run(conn, "manual_entry:backfill")
        for entry in cfg.get("products", []):
            sku = str(entry["sku"])
            declared = entry.get("declared", {})
            spec = parse_spec(
                title=entry.get("title"),
                cpu=declared.get("cpu"), ram=declared.get("ram"),
                storage=declared.get("storage"), os_=declared.get("os"),
                device_type=declared.get("device_type"),
                form_factor=declared.get("form_factor"))
            st.upsert_product(conn, sku=sku, brand=entry.get("brand", "?"),
                model_name=entry.get("model_name", sku),
                listing_title=entry.get("title"), url=entry.get("url"),
                declared=declared, normalized=spec.__dict__,
                group_key=spec.group_key(), tier_key=spec.tier_key())
            for quote in source.history(sku):
                captured = quote.raw.get("captured_at_utc") or st.utcnow()
                obs_id = st.insert_observation(conn, sku=sku,
                    captured_at_utc=captured, price_usd=quote.price_usd,
                    regular_price_usd=None, on_sale=None,
                    availability=quote.availability,
                    source_method=quote.source_method,
                    source_url=entry.get("url"),
                    collector_version=COLLECTOR_VERSION, raw=quote.raw)
                if obs_id is None:
                    skipped += 1
                else:
                    stored += 1
                    print(f"  [ADD] {sku} ${quote.price_usd:.2f} at {captured}")
        st.finish_run(conn, run_id, attempted=stored+skipped,
            succeeded=stored, failed=0, notes=f"{skipped} already on file")
    print(f"\\nBackfill complete: {stored} stored, {skipped} already on file.")
    return 0

'''

s = s.replace('\ndef main(', backfill_fn + '\ndef main(')
p.write_text(s, encoding="utf-8")
print("patched, new size:", len(s))
