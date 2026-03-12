#!/usr/bin/env python3
import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


def normalize_name(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = value.replace("//", "/")
    value = value.replace("&", "and")
    for ch in ["'", ".", ",", ":", ";", "!", "?", "\"", "(", ")", "[", "]"]:
        value = value.replace(ch, "")
    value = " ".join(value.split())
    return value


def card_name_variants(card: dict) -> set[str]:
    variants = set()
    if card.get("name"):
        variants.add(normalize_name(card["name"]))
    if card.get("printed_name"):
        variants.add(normalize_name(card["printed_name"]))

    for face in card.get("card_faces", []) or []:
        if isinstance(face, dict):
            if face.get("name"):
                variants.add(normalize_name(face["name"]))
            if face.get("printed_name"):
                variants.add(normalize_name(face["printed_name"]))
    variants.discard("")
    return variants


def product_name_variants(value: str) -> set[str]:
    variants: set[str] = set()
    if not value:
        return variants

    # First, strip bracketed content (e.g., " (White 1/1)") BEFORE normalization
    # to get a clean base name without color/power info
    without_brackets = re.sub(r"\s*\[[^\]]*\]", "", value)
    without_brackets = re.sub(r"\s*\([^)]*\)", "", without_brackets)
    without_brackets = without_brackets.strip()

    base = normalize_name(value)
    clean_base = normalize_name(without_brackets)
    
    variants.add(base)
    if clean_base != base:
        variants.add(clean_base)

    # Remove common Cardmarket prefixes that often do not exist in Scryfall names.
    prefixes = [
        "art series:",
        "art series",
        "event:",
        "event",
        "theme card:",
        "theme card",
    ]
    for prefix in prefixes:
        if base.startswith(prefix):
            stripped = base[len(prefix):].strip()
            if stripped.startswith(":"):
                stripped = stripped[1:].strip()
            if stripped:
                variants.add(stripped)

    # Drop parenthetical token/pt labels, e.g. "token (g 2/2)".
    without_parens = re.sub(r"\([^)]*\)", "", base)
    without_parens = " ".join(without_parens.split())
    if without_parens:
        variants.add(without_parens)

    # Normalize slash variants for split/double-faced naming differences.
    slash_variants = set()
    for v in list(variants):
        slash_variants.add(v.replace(" / ", " // "))
        slash_variants.add(v.replace(" // ", " / "))
        parts = [p.strip() for p in v.replace(" // ", " / ").split("/") if p.strip()]
        slash_variants.update(parts)
    variants.update(slash_variants)

    # Strip leading labels from token products.
    cleaned = set()
    for v in variants:
        for label in ["token ", "tokens "]:
            if v.startswith(label):
                cleaned.add(v[len(label):].strip())
    variants.update(cleaned)

    # Also strip common trailing labels for token/emblem products.
    tail_cleaned = set()
    for v in variants:
        for suffix in [" emblem", " token", " tokens"]:
            if v.endswith(suffix):
                tail_cleaned.add(v[: -len(suffix)].strip())
    variants.update(tail_cleaned)

    variants.discard("")
    return variants


def strip_special_set_prefix(set_code: str) -> str:
    code = (set_code or "").lower().strip()
    if len(code) >= 2 and code[0] in {"p", "t", "a", "x"}:
        return code[1:]
    return code


def classify_product_bucket(product_name: str) -> str:
    norm = normalize_name(product_name)
    if norm.startswith("art series"):
        return "art"

    token_markers = ("token", "tokens", "emblem")
    if (
        norm.startswith("token ")
        or norm.startswith("tokens ")
        or norm.endswith(" token")
        or norm.endswith(" tokens")
        or norm.endswith(" emblem")
        or " token " in norm
        or " emblem " in norm
    ):
        return "token"

    if "promo" in norm:
        return "promo"

    return "regular"


def preferred_set_codes(mapped_set: str, product_name: str) -> list[str]:
    mapped = (mapped_set or "").lower().strip()
    if not mapped:
        return []

    base = strip_special_set_prefix(mapped)
    bucket = classify_product_bucket(product_name)

    # Order matters: earlier entries are treated as stronger matches.
    if bucket == "promo":
        order = [f"p{base}", mapped, base, f"x{base}", f"t{base}", f"a{base}"]
    elif bucket == "token":
        order = [f"t{base}", base, f"x{base}", mapped, f"p{base}", f"a{base}"]
    elif bucket == "art":
        order = [f"a{base}", f"x{base}", mapped, base, f"p{base}", f"t{base}"]
    else:
        order = [mapped, base, f"p{base}", f"t{base}", f"a{base}", f"x{base}"]

    seen = set()
    out = []
    for code in order:
        code = (code or "").lower().strip()
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def choose_representative(cards: list[dict]) -> dict:
    # Keep selection deterministic.
    return sorted(
        cards,
        key=lambda c: (
            c.get("released_at") or "9999-99-99",
            c.get("set") or "",
            str(c.get("collector_number") or ""),
            c.get("id") or "",
        ),
    )[0]


def is_paper_card(card: dict) -> bool:
    games = card.get("games")
    return isinstance(games, list) and "paper" in games


def is_content_warning(card: dict) -> bool:
    return bool(card.get("content_warning", False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Map Cardmarket products whose idProduct is missing in Scryfall's cardmarket_id field."
        )
    )
    parser.add_argument(
        "--cards",
        default="default-cards-20260312090730.json",
        help="Path to Scryfall default cards JSON.",
    )
    parser.add_argument(
        "--products",
        default="products_singles_1.json",
        help="Path to Cardmarket products catalogue JSON.",
    )
    parser.add_argument(
        "--output",
        default="missing_cardmarket_id_mappings.csv",
        help="Output CSV with mapping proposals.",
    )
    parser.add_argument(
        "--summary",
        default="missing_cardmarket_id_summary.json",
        help="Output JSON summary.",
    )
    parser.add_argument(
        "--review-output",
        default="missing_cardmarket_review_by_set.md",
        help="Grouped review markdown for likely mappings.",
    )
    parser.add_argument(
        "--exclude-expansion-ids",
        default="73",
        help=(
            "Comma-separated Cardmarket idExpansion values to exclude from mapping "
            "(default excludes Foreign White Bordered: 73)."
        ),
    )
    parser.add_argument(
        "--manual-overrides",
        default="manual_overrides.json",
        help=(
            "Optional JSON file with manual override mappings: "
            "{ \"idProduct\": \"scryfall-id\", ... }"
        ),
    )
    args = parser.parse_args()

    excluded_expansion_ids = {
        int(x.strip())
        for x in args.exclude_expansion_ids.split(",")
        if x.strip().isdigit()
    }

    cards_path = Path(args.cards)
    products_path = Path(args.products)
    overrides_path = Path(args.manual_overrides)

    # Load manual overrides (idProduct -> scryfall_id mapping)
    manual_overrides: dict[int, str] = {}
    if overrides_path.exists():
        with overrides_path.open("r", encoding="utf-8") as f:
            try:
                overrides_raw = json.load(f)
                # Convert string keys to int, and values should be strings (scryfall IDs)
                manual_overrides = {int(k): str(v) for k, v in overrides_raw.items()}
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                print(f"Warning: Could not load manual overrides from {overrides_path}: {e}")
                manual_overrides = {}

    with cards_path.open("r", encoding="utf-8") as f:
        all_cards = json.load(f)

    with products_path.open("r", encoding="utf-8") as f:
        products_wrapper = json.load(f)

    products = products_wrapper.get("products", products_wrapper)
    if not isinstance(products, list):
        raise ValueError("Products JSON must be a list or an object with a 'products' list")

    cards = [c for c in all_cards if is_paper_card(c)]
    content_warning_cards = [c for c in cards if is_content_warning(c)]
    mappable_cards = [c for c in cards if not is_content_warning(c)]

    cards_with_mkm = [c for c in cards if c.get("cardmarket_id") is not None]
    cards_without_mkm = [c for c in mappable_cards if c.get("cardmarket_id") is None]

    known_product_ids = {int(c["cardmarket_id"]) for c in cards_with_mkm if isinstance(c.get("cardmarket_id"), int)}
    known_by_product_id = {int(c["cardmarket_id"]): c for c in cards_with_mkm if isinstance(c.get("cardmarket_id"), int)}

    missing_products = [
        p
        for p in products
        if isinstance(p.get("idProduct"), int) and p["idProduct"] not in known_product_ids
    ]

    # Infer a mapping between Cardmarket idExpansion and Scryfall set code from already-linked products.
    expansion_to_set_counts: dict[int, Counter] = defaultdict(Counter)
    for p in products:
        pid = p.get("idProduct")
        exp = p.get("idExpansion")
        if isinstance(pid, int) and isinstance(exp, int) and pid in known_by_product_id:
            set_code = known_by_product_id[pid].get("set")
            if set_code:
                expansion_to_set_counts[exp][set_code] += 1

    expansion_set_choice = {}
    for exp, counts in expansion_to_set_counts.items():
        set_code, count = counts.most_common(1)[0]
        total = sum(counts.values())
        expansion_set_choice[exp] = {
            "set": set_code,
            "count": count,
            "total": total,
            "confidence": round(count / total, 4),
        }

    missing_by_name: dict[str, list[dict]] = defaultdict(list)
    missing_by_name_and_set: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for card in cards_without_mkm:
        set_code = card.get("set") or ""
        for variant in card_name_variants(card):
            missing_by_name[variant].append(card)
            missing_by_name_and_set[(variant, set_code)].append(card)

    proposals = []
    unresolved = 0
    excluded_products = 0

    quirk_matched = 0
    for p in missing_products:
        pid = p.get("idProduct")
        pname = p.get("name") or ""
        pexp = p.get("idExpansion")
        pmeta = p.get("idMetacard")
        name_variants = product_name_variants(pname)
        norm = normalize_name(pname)
        if norm:
            name_variants.add(norm)

        # Check for manual override mapping
        if isinstance(pid, int) and pid in manual_overrides:
            override_scryfall_id = manual_overrides[pid]
            # Find the card with this ID in the cards_without_mkm list
            chosen = None
            for card in cards_without_mkm:
                if card.get("id") == override_scryfall_id:
                    chosen = card
                    break
            if chosen:
                proposals.append(
                    {
                        "idProduct": pid,
                        "idMetacard": pmeta,
                        "name": pname,
                        "idExpansion": pexp,
                        "mapped_set": "",
                        "mapped_set_confidence": 0.0,
                        "proposed_scryfall_id": chosen.get("id", ""),
                        "proposed_oracle_id": chosen.get("oracle_id", ""),
                        "proposed_set": chosen.get("set", ""),
                        "proposed_collector_number": chosen.get("collector_number", ""),
                        "proposed_lang": chosen.get("lang", ""),
                        "confidence": "manual",
                        "reason": "manual override mapping",
                    }
                )
                continue

        if isinstance(pexp, int) and pexp in excluded_expansion_ids:
            excluded_products += 1
            proposals.append(
                {
                    "idProduct": pid,
                    "idMetacard": pmeta,
                    "name": pname,
                    "idExpansion": pexp,
                    "mapped_set": "",
                    "mapped_set_confidence": 0.0,
                    "proposed_scryfall_id": "",
                    "proposed_oracle_id": "",
                    "proposed_set": "",
                    "proposed_collector_number": "",
                    "proposed_lang": "",
                    "confidence": "none",
                    "reason": f"excluded idExpansion {pexp} (supports alternate cardmarket_id not representable in Scryfall)",
                }
            )
            continue

        mapped_set = None
        mapped_set_conf = 0.0
        if isinstance(pexp, int) and pexp in expansion_set_choice:
            mapped_set = expansion_set_choice[pexp]["set"]
            mapped_set_conf = expansion_set_choice[pexp]["confidence"]

        set_candidates = []
        name_candidates = []
        preferred_sets = preferred_set_codes(mapped_set or "", pname)
        for variant in sorted(name_variants):
            for candidate_set in preferred_sets:
                set_candidates.extend(missing_by_name_and_set.get((variant, candidate_set), []))
            name_candidates.extend(missing_by_name.get(variant, []))

        # De-duplicate while preserving deterministic order.
        set_candidates = list({c.get("id"): c for c in set_candidates}.values())
        name_candidates = list({c.get("id"): c for c in name_candidates}.values())

        chosen = None
        confidence = "none"
        reason = ""

        if len(set_candidates) == 1:
            chosen = set_candidates[0]
            confidence = "high"
            reason_set = chosen.get("set") or (preferred_sets[0] if preferred_sets else mapped_set)
            reason = f"unique missing-name match inside preferred inferred set '{reason_set}'"
        elif len(set_candidates) > 1:
            chosen = choose_representative(set_candidates)
            confidence = "low"
            reason_set = chosen.get("set") or (preferred_sets[0] if preferred_sets else mapped_set)
            reason = f"multiple missing-name matches in preferred inferred set '{reason_set}'"
        elif len(name_candidates) == 1:
            chosen = name_candidates[0]
            confidence = "medium"
            reason = "unique missing-name match across all sets"
        elif len(name_candidates) > 1:
            chosen = choose_representative(name_candidates)
            confidence = "low"
            reason = "multiple missing-name matches across sets"

        if chosen is None:
            unresolved += 1
            proposals.append(
                {
                    "idProduct": pid,
                    "idMetacard": pmeta,
                    "name": pname,
                    "idExpansion": pexp,
                    "mapped_set": mapped_set,
                    "mapped_set_confidence": mapped_set_conf,
                    "proposed_scryfall_id": "",
                    "proposed_oracle_id": "",
                    "proposed_set": "",
                    "proposed_collector_number": "",
                    "proposed_lang": "",
                    "confidence": "none",
                    "reason": "no candidate missing card found by name",
                }
            )
            continue

        if normalize_name(pname) not in card_name_variants(chosen):
            quirk_matched += 1
            reason = f"{reason}; matched via normalized name variant"

        proposals.append(
            {
                "idProduct": pid,
                "idMetacard": pmeta,
                "name": pname,
                "idExpansion": pexp,
                "mapped_set": mapped_set,
                "mapped_set_confidence": mapped_set_conf,
                "proposed_scryfall_id": chosen.get("id", ""),
                "proposed_oracle_id": chosen.get("oracle_id", ""),
                "proposed_set": chosen.get("set", ""),
                "proposed_collector_number": chosen.get("collector_number", ""),
                "proposed_lang": chosen.get("lang", ""),
                "confidence": confidence,
                "reason": reason,
            }
        )

    output_fields = [
        "idProduct",
        "idMetacard",
        "name",
        "idExpansion",
        "mapped_set",
        "mapped_set_confidence",
        "proposed_scryfall_id",
        "proposed_oracle_id",
        "proposed_set",
        "proposed_collector_number",
        "proposed_lang",
        "confidence",
        "reason",
    ]

    with Path(args.output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(proposals)

    # Review file for likely mappings, grouped by proposed set.
    likely = [p for p in proposals if p["confidence"] in {"high", "medium"}]
    likely_by_set: dict[str, list[dict]] = defaultdict(list)
    for row in likely:
        set_code = row.get("proposed_set") or row.get("mapped_set") or "unknown"
        likely_by_set[set_code].append(row)

    review_lines = [
        "# Missing Cardmarket ID Review",
        "",
        f"Likely mappings (high/medium confidence): {len(likely)}",
        "",
    ]
    for set_code in sorted(likely_by_set):
        rows = sorted(
            likely_by_set[set_code],
            key=lambda r: (
                str(r.get("proposed_collector_number") or ""),
                str(r.get("name") or ""),
                int(r.get("idProduct") or 0),
            ),
        )
        review_lines.append(f"## {set_code} ({len(rows)})")
        review_lines.append("")
        review_lines.append("| confidence | idProduct | idMetacard | product_name | proposed_card | collector | scryfall_id | reason |")
        review_lines.append("|---|---:|---:|---|---|---|---|---|")
        for r in rows:
            review_lines.append(
                "| {confidence} | {idProduct} | {idMetacard} | {name} | {proposed_name} | {collector} | {sid} | {reason} |".format(
                    confidence=r.get("confidence") or "",
                    idProduct=r.get("idProduct") or "",
                    idMetacard=r.get("idMetacard") or "",
                    name=(r.get("name") or "").replace("|", "\\|"),
                    proposed_name=(r.get("proposed_set") or "") + " " + (r.get("proposed_collector_number") or ""),
                    collector=r.get("proposed_collector_number") or "",
                    sid=r.get("proposed_scryfall_id") or "",
                    reason=(r.get("reason") or "").replace("|", "\\|"),
                )
            )
        review_lines.append("")

    Path(args.review_output).write_text("\n".join(review_lines), encoding="utf-8")

    confidence_counts = Counter(x["confidence"] for x in proposals)
    summary = {
        "cards_total_all_games": len(all_cards),
        "cards_total_paper_only": len(cards),
        "cards_content_warning_paper": len(content_warning_cards),
        "cards_total_mappable": len(mappable_cards),
        "cards_with_cardmarket_id": len(cards_with_mkm),
        "cards_missing_cardmarket_id": len(cards_without_mkm),
        "products_total": len(products),
        "products_missing_in_scryfall": len(missing_products),
        "proposals_total": len(proposals),
        "proposal_confidence_counts": dict(confidence_counts),
        "unresolved_products": unresolved,
        "expansion_mapping_count": len(expansion_set_choice),
        "quirk_variant_matches": quirk_matched,
        "excluded_products": excluded_products,
        "excluded_expansion_ids": sorted(excluded_expansion_ids),
        "review_file": args.review_output,
        "likely_review_count": len(likely),
    }

    with Path(args.summary).open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))
    print(f"Wrote proposals to: {args.output}")
    print(f"Wrote summary to: {args.summary}")
    print(f"Wrote grouped review to: {args.review_output}")


if __name__ == "__main__":
    main()
