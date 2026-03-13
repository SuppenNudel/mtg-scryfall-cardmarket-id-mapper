#!/usr/bin/env python3
import argparse
import csv
import html
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


def to_int(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_name(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = value.replace("//", "/")
    value = value.replace("&", "and")
    for ch in ["'", ".", ",", ":", ";", "!", "?", '"', "(", ")", "[", "]"]:
        value = value.replace(ch, "")
    value = " ".join(value.split())
    return value


def card_name_variants(card: dict) -> set[str]:
    variants = set()
    for key in ("name", "printed_name"):
        if card.get(key):
            variants.add(normalize_name(card[key]))

    for face in card.get("card_faces", []) or []:
        if isinstance(face, dict):
            for key in ("name", "printed_name"):
                if face.get(key):
                    variants.add(normalize_name(face[key]))

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

    for prefix in ["art series:", "art series", "event:", "event", "theme card:", "theme card"]:
        if base.startswith(prefix):
            stripped = base[len(prefix):].strip()
            if stripped.startswith(":"):
                stripped = stripped[1:].strip()
            if stripped:
                variants.add(stripped)

    without_parens = re.sub(r"\([^)]*\)", "", base)
    without_parens = " ".join(without_parens.split())
    if without_parens:
        variants.add(without_parens)

    slash_variants = set()
    for variant in list(variants):
        slash_variants.add(variant.replace(" / ", " // "))
        slash_variants.add(variant.replace(" // ", " / "))
        parts = [p.strip() for p in variant.replace(" // ", " / ").split("/") if p.strip()]
        slash_variants.update(parts)
    variants.update(slash_variants)

    cleaned = set()
    for variant in variants:
        for label in ["token ", "tokens "]:
            if variant.startswith(label):
                cleaned.add(variant[len(label):].strip())
    variants.update(cleaned)

    # Also strip common trailing labels for token/emblem products.
    tail_cleaned = set()
    for variant in variants:
        for suffix in [" emblem", " token", " tokens"]:
            if variant.endswith(suffix):
                tail_cleaned.add(variant[: -len(suffix)].strip())
    variants.update(tail_cleaned)

    variants.discard("")
    return variants


def is_paper_card(card: dict) -> bool:
    games = card.get("games")
    return isinstance(games, list) and "paper" in games


def is_content_warning(card: dict) -> bool:
    return bool(card.get("content_warning", False))


def load_expansion_map(expansions_html_path: Path) -> dict[int, str]:
    text = expansions_html_path.read_text(encoding="utf-8", errors="ignore")
    option_re = re.compile(r'<option\s+value="(\d+)"[^>]*>(.*?)</option>', re.IGNORECASE | re.DOTALL)
    mapping: dict[int, str] = {}
    for match in option_re.finditer(text):
        exp_id_raw, name_raw = match.groups()
        exp_id = to_int(exp_id_raw)
        if exp_id is None or exp_id == 0:
            continue
        name = html.unescape(re.sub(r"\s+", " ", name_raw)).strip()
        if name:
            mapping[exp_id] = name
    return mapping


def load_scryfall_candidate_index(
  cards_path: Path,
) -> tuple[dict[tuple[str, str], list[dict]], dict[str, list[dict]]]:
    with cards_path.open("r", encoding="utf-8") as f:
        cards = json.load(f)

    buckets_by_set: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    buckets_by_name: dict[str, dict[str, dict]] = defaultdict(dict)
    for card in cards:
        if not is_paper_card(card) or is_content_warning(card):
            continue

        set_code = card.get("set") or ""
        card_id = card.get("id") or ""
        if not set_code or not card_id:
            continue

        card_faces = card.get("card_faces") if isinstance(card.get("card_faces"), list) else []
        has_separate_back_image = False
        if len(card_faces) > 1 and isinstance(card_faces[1], dict):
          face_image_uris = card_faces[1].get("image_uris")
          if isinstance(face_image_uris, dict):
            has_separate_back_image = bool(
              face_image_uris.get("normal")
              or face_image_uris.get("large")
              or face_image_uris.get("png")
              or face_image_uris.get("small")
            )

        candidate = {
            "id": card_id,
            "oracle_id": card.get("oracle_id", ""),
            "set": set_code,
            "set_name": card.get("set_name", ""),
            "collector_number": card.get("collector_number", ""),
            "lang": card.get("lang", ""),
            "name": card.get("name", ""),
            "border_color": card.get("border_color", ""),
            "foil": bool(card.get("foil", False)),
            "nonfoil": bool(card.get("nonfoil", False)),
            "finishes": card.get("finishes", []) if isinstance(card.get("finishes"), list) else [],
            "promo_types": card.get("promo_types", []) if isinstance(card.get("promo_types"), list) else [],
            "cardmarket_id": card.get("cardmarket_id") or None,
            "has_separate_back_image": has_separate_back_image,
        }
        for variant in card_name_variants(card):
            buckets_by_set[(set_code, variant)][card_id] = candidate
            buckets_by_name[variant][card_id] = candidate

    by_set = {
        key: sorted(
            values.values(),
            key=lambda c: (str(c.get("collector_number") or ""), str(c.get("id") or "")),
        )
        for key, values in buckets_by_set.items()
    }
    by_name = {
        key: sorted(
            values.values(),
            key=lambda c: (
                str(c.get("set") or ""),
                str(c.get("collector_number") or ""),
                str(c.get("id") or ""),
            ),
        )
        for key, values in buckets_by_name.items()
    }
    return by_set, by_name


def load_rows(
    csv_path: Path,
    expansion_map: dict[int, str] | None = None,
    expansion_code_map: dict[int, str] | None = None,
    scryfall_candidate_index: dict[tuple[str, str], list[dict]] | None = None,
    scryfall_candidate_name_index: dict[str, list[dict]] | None = None,
):
    expansion_map = expansion_map or {}
    expansion_code_map = expansion_code_map or {}
    scryfall_candidate_index = scryfall_candidate_index or {}
    scryfall_candidate_name_index = scryfall_candidate_name_index or {}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            exp_id = to_int(row.get("idExpansion", ""))
            exp_name = expansion_map.get(exp_id, "") if exp_id is not None else ""
            exp_code = expansion_code_map.get(exp_id, "") if exp_id is not None else ""
            target_set = row.get("proposed_set", "") or row.get("mapped_set", "")
            candidates_by_id: dict[str, dict] = {}
            all_candidates_by_id: dict[str, dict] = {}
            for variant in sorted(product_name_variants(row.get("name", ""))):
              for candidate in scryfall_candidate_index.get((target_set, variant), []):
                candidate_id = candidate.get("id") or ""
                if candidate_id and candidate_id not in candidates_by_id:
                  candidates_by_id[candidate_id] = candidate
              for candidate in scryfall_candidate_name_index.get(variant, []):
                candidate_id = candidate.get("id") or ""
                if candidate_id and candidate_id not in all_candidates_by_id:
                  all_candidates_by_id[candidate_id] = candidate
            scryfall_candidates = list(candidates_by_id.values())
            all_scryfall_candidates = list(all_candidates_by_id.values())
            initial_candidate_index = 0
            initial_all_candidate_index = 0
            proposed_scryfall_id = row.get("proposed_scryfall_id", "")
            for idx, candidate in enumerate(scryfall_candidates):
                if candidate.get("id") == proposed_scryfall_id:
                    initial_candidate_index = idx
                    break
            for idx, candidate in enumerate(all_scryfall_candidates):
                if candidate.get("id") == proposed_scryfall_id:
                    initial_all_candidate_index = idx
                    break

            rows.append(
                {
                    "idProduct": to_int(row.get("idProduct", "")),
                    "idMetacard": to_int(row.get("idMetacard", "")),
                    "name": row.get("name", ""),
                    "idExpansion": exp_id,
                    "expansion_name": exp_name,
                    "expansion_code": exp_code,
                    "mapped_set": row.get("mapped_set", ""),
                    "mapped_set_confidence": to_float(row.get("mapped_set_confidence", "")),
                    "proposed_scryfall_id": row.get("proposed_scryfall_id", ""),
                    "proposed_oracle_id": row.get("proposed_oracle_id", ""),
                    "proposed_set": row.get("proposed_set", ""),
                    "proposed_collector_number": row.get("proposed_collector_number", ""),
                    "proposed_lang": row.get("proposed_lang", ""),
                    "confidence": row.get("confidence", ""),
                    "reason": row.get("reason", ""),
                    "scryfall_candidates": scryfall_candidates,
                    "initial_scryfall_candidate_index": initial_candidate_index,
                    "all_scryfall_candidates": all_scryfall_candidates,
                    "initial_all_scryfall_candidate_index": initial_all_candidate_index,
                }
            )
    return rows


def build_html(data_json_name: str, title: str, embedded_rows_json: str):
    template = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <link rel=\"icon\" href=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='10' fill='%23c4572a'/%3E%3Ctext x='50%25' y='56%25' text-anchor='middle' font-size='34' fill='white' font-family='Arial'%3EM%3C/text%3E%3C/svg%3E\" />
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #f4efe6;
      --panel: #fffaf1;
      --ink: #1f2a30;
      --muted: #61707a;
      --line: #e7dbc9;
      --shadow: 0 8px 28px rgba(28, 40, 48, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: \"Trebuchet MS\", \"Segoe UI\", sans-serif;
      background:
        radial-gradient(1200px 600px at -10% -20%, #f8d8b6 0%, transparent 50%),
        radial-gradient(1100px 500px at 120% -10%, #b9d9e6 0%, transparent 50%),
        var(--bg);
      min-height: 100vh;
    }
    .wrap { width: min(1500px, 96vw); margin: 20px auto; padding-bottom: 24px; }
    .hero {
      background: linear-gradient(130deg, #f9f0df, #fffaf1 45%, #eef7fb);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 16px;
      margin-bottom: 12px;
    }
    h1 { margin: 0 0 6px; font-size: 27px; }
    .sub { color: var(--muted); font-size: 14px; }
    .stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-top: 12px; }
    .stat { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 8px; }
    .stat .k { color: var(--muted); font-size: 11px; text-transform: uppercase; }
    .stat .v { font-size: 20px; font-weight: 700; }

    .controls { display: grid; grid-template-columns: 1.8fr 1fr 1fr 1fr 1fr auto auto; gap: 8px; margin: 12px 0; }
    input, select, button, a.btn {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      background: #fff;
      color: var(--ink);
      font-size: 14px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    button { cursor: pointer; }
    button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
      filter: grayscale(0.35);
      box-shadow: none;
    }
    .primary { background: #bf5124; color: #fff; border-color: #b84b21; }
    .secondary { background: #2d6782; color: #fff; border-color: #295f79; }

    .compare {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: var(--shadow);
      padding: 12px;
      margin-bottom: 12px;
    }
    .compare-head { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 13px; margin-bottom: 8px; }
    .compare-tools { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .compare-tools input { padding: 6px 8px; font-size: 12px; width: 120px; }
    .mkm-tools { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .mkm-tools input { padding: 6px 8px; font-size: 12px; width: 120px; }
    .ghost { font-size: 12px; padding: 6px 8px; }
    .compare-decision { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
    .compare-status { font-size: 13px; color: var(--muted); }
    .compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .card-pane { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px; }
    .pane-title { margin: 0 0 8px; font-size: 13px; color: #43535c; text-transform: uppercase; }
    .img-wrap {
      min-height: 260px;
      display: grid;
      place-items: center;
      background: #fcf8f2;
      border: 1px solid #efe4d2;
      border-radius: 10px;
      overflow: hidden;
    }
    .img-wrap img { max-width: 100%; max-height: 400px; object-fit: contain; }
    #mkmFrontImg, #mkmBackImg, #scryCurrentImg, #scryAcceptedImg { height: 520px; max-height: none; }
    #scryCurrentWrap, #scryAcceptedWrap { position: relative; }
    .scry-back-overlay {
      position: absolute;
      right: 10px;
      bottom: 10px;
      width: 34%;
      max-width: 170px;
      height: auto !important;
      border: 2px solid #d7c9b3;
      border-radius: 8px;
      box-shadow: 0 6px 14px rgba(0, 0, 0, 0.2);
      background: #fff;
      z-index: 2;
    }
    .scry-back-overlay.hidden { display: none; }
    .mkm-preview-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .mkm-preview-col {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .mkm-preview-label {
      font-size: 12px;
      color: #43535c;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 700;
    }
    #mkmBackCol.hidden { display: none; }
    .img-meta { margin-top: 7px; color: var(--muted); font-size: 12px; word-break: break-all; }
    .scry-preview-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .scry-preview-col {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .scry-preview-label {
      font-size: 12px;
      color: #43535c;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 700;
    }
    .scry-toggle {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: #4e5f69;
      user-select: none;
    }
    .scry-toggle input {
      width: 14px;
      height: 14px;
      accent-color: #2d6782;
    }
    .scry-candidates {
      margin-top: 8px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 6px;
    }
    .scry-candidate {
      border: 1px solid #e3d5c1;
      border-radius: 8px;
      background: #fffdf8;
      padding: 4px;
      cursor: pointer;
      text-align: left;
      position: relative;
    }
    .scry-candidate.active {
      border-color: #2d6782;
      box-shadow: 0 0 0 1px rgba(45, 103, 130, 0.25);
    }
    .scry-candidate.has-cardmarket-id {
      cursor: not-allowed;
      opacity: 0.68;
    }
    .scry-candidate.has-cardmarket-id::after {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: linear-gradient(135deg, rgba(92, 184, 92, 0.35), rgba(92, 184, 92, 0.15));
      border-radius: 6px;
      pointer-events: none;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .scry-candidate.has-cardmarket-id .scry-candidate-meta {
      position: relative;
      z-index: 1;
    }
    .scry-candidate img {
      width: 100%;
      height: 160px;
      object-fit: contain;
      display: block;
      background: #f7efe2;
      border-radius: 6px;
    }
    .scry-candidate-meta {
      margin-top: 4px;
      font-size: 11px;
      color: #56656f;
      line-height: 1.25;
      word-break: break-word;
    }

    .table-wrap { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); overflow: auto; }
    table { width: 100%; border-collapse: collapse; min-width: 1280px; }
    thead th { position: sticky; top: 0; background: #f6ecd9; text-align: left; padding: 10px; font-size: 12px; color: #43535c; border-bottom: 1px solid var(--line); }
    tbody td { padding: 9px 10px; border-bottom: 1px solid #f0e5d5; vertical-align: top; font-size: 13px; }
    tbody tr:hover { background: #fff4df; }
    .sel-row { background: #fdeed5 !important; }

    .pill { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 11px; border: 1px solid transparent; text-transform: uppercase; }
    .conf-high { background: #dff4e9; color: #1d6948; border-color: #bfe6d4; }
    .conf-medium { background: #e7f0f8; color: #255d7a; border-color: #cce1f2; }
    .conf-low { background: #f7eddc; color: #7f5b2c; border-color: #edd8b8; }
    .conf-none { background: #f9e7e7; color: #8a3434; border-color: #efc8c8; }
    .decision-cell { display: flex; gap: 5px; flex-wrap: wrap; }
    .tiny { font-size: 13px; padding: 8px 12px; border-radius: 9px; }
    .tiny:disabled {
      background: #ece7df !important;
      border-color: #d6cdc0 !important;
      color: #8b7f70 !important;
    }
    .yes { background: #e1f4ea; border-color: #c0e5d1; }
    .repbtn { background: #eee7f8; border-color: #d4c4ed; }
    .no { background: #fae7e7; border-color: #efc3c3; }
    .skip { background: #ecf0f4; border-color: #d6dee8; }
    .ignore { background: #f4efe6; border-color: #ddcfb7; }
    .row-note { color: var(--muted); font-size: 12px; }
    .decision-tag { font-weight: 700; }
    .accepted { color: #206a4a; }
    .decision-tag.replace { color: #5f3c87; }
    .rejected { color: #9a2f2f; }
    .skipped { color: #55626e; }
    .ignored { color: #7b5a2f; }

    .pagination { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; color: var(--muted); font-size: 13px; }
    .pagination-controls { display: inline-flex; gap: 8px; }
    .pagination-controls button[disabled] {
      opacity: 0.45;
      cursor: not-allowed;
      background: #f0e7da;
      color: #8d7f6c;
    }
    .end-notice {
      display: none;
      margin-top: 10px;
      padding: 10px 12px;
      border: 1px solid #d6b172;
      background: linear-gradient(135deg, #fff4d8, #ffe9bb);
      color: #7a4b10;
      border-radius: 10px;
      font-weight: 700;
      text-align: center;
      letter-spacing: 0.02em;
    }
    .end-notice.visible { display: block; }

    @media (max-width: 1040px) {
      .controls { grid-template-columns: 1fr 1fr; }
      .compare-grid { grid-template-columns: 1fr; }
      .mkm-preview-grid { grid-template-columns: 1fr; }
      .scry-preview-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <section class=\"hero\">
      <h1>Proposed Cardmarket ID Matcher Review</h1>
      <div class=\"sub\">Review mappings, compare images side by side, save decisions in-browser, and export accepted rows.</div>
      <div class=\"stats\" id=\"stats\"></div>
    </section>

    <section class=\"controls\">
      <input id=\"searchInput\" placeholder=\"Search by name, reason, set, idProduct, scryfall id\" />
      <select id=\"confidenceFilter\">
        <option value=\"\">All confidence</option>
        <option value=\"high\">high</option>
        <option value=\"medium\">medium</option>
        <option value=\"low\">low</option>
        <option value=\"none\">none</option>
      </select>
      <select id=\"setFilter\"><option value=\"\">All Cardmarket sets</option></select>
      <select id=\"decisionFilter\">
        <option value=\"\">All decisions</option>
        <option value=\"unreviewed\">unreviewed</option>
        <option value=\"accepted\">accepted</option>
        <option value=\"replace\">replace</option>
        <option value=\"rejected\">rejected</option>
        <option value=\"skipped\">skipped</option>
        <option value=\"ignored\">ignored</option>
      </select>
      <select id=\"pageSize\">
        <option value=\"50\">50 / page</option>
        <option value=\"100\" selected>100 / page</option>
        <option value=\"200\">200 / page</option>
      </select>
      <button class=\"primary\" id=\"exportAccepted\">Export accepted CSV</button>
      <button class=\"secondary\" id=\"exportReplacements\">Export replacement CSV</button>
      <button class=\"secondary\" id=\"exportDecisions\">Export decisions JSON</button>
    </section>

    <section class=\"compare\">
      <div class=\"compare-head\">
        <div id=\"compareTitle\">Select a row to compare images</div>
        <div class=\"compare-tools\">
          <button id=\"nextMkmCandidate\" class=\"ghost\">Try next image URL</button>
        </div>
      </div>
      <div class=\"compare-decision\">
        <button id=\"compareAccept\" class=\"tiny yes\">Accept</button>
        <button id=\"compareReplace\" class=\"tiny repbtn\">Replace</button>
        <button id=\"compareReject\" class=\"tiny no\">Reject</button>
        <button id=\"compareSkip\" class=\"tiny skip\">Skip</button>
        <button id=\"compareIgnore\" class=\"tiny ignore\">Ignore</button>
        <span id=\"compareDecisionStatus\" class=\"compare-status\">Current decision: unreviewed</span>
      </div>
      <div class=\"compare-grid\">
        <div class=\"card-pane\">
          <h3 class=\"pane-title\">Cardmarket</h3>
          <div class="mkm-preview-grid">
            <div class="mkm-preview-col">
              <div class="mkm-preview-label">Front</div>
              <div class="img-wrap"><img id="mkmFrontImg" alt="Cardmarket front image" /></div>
            </div>
            <div class="mkm-preview-col hidden" id="mkmBackCol">
              <div class="mkm-preview-label">Back</div>
              <div class="img-wrap"><img id="mkmBackImg" alt="Cardmarket back image" /></div>
            </div>
          </div>
          <div style=\"margin-top:8px; display:flex; justify-content:flex-start; gap:8px; flex-wrap:wrap;\">
            <a id=\"openMkmProduct\" class=\"btn ghost\" target=\"_blank\" rel=\"noreferrer\">Open product page</a>
          </div>
          <div class=\"mkm-tools\" style=\"margin-top:8px;\">
            <label for=\"mkmCodeInput\">MKM code</label>
            <input id=\"mkmCodeInput\" placeholder=\"ECL\" />
            <button id=\"saveMkmCode\" class=\"ghost\">Save for expansion</button>
          </div>
          <div class=\"img-meta\" id=\"mkmMeta\"></div>
        </div>
        <div class=\"card-pane\">
          <h3 class=\"pane-title\">Scryfall</h3>
          <div class="scry-preview-grid">
            <div class="scry-preview-col">
              <div class="scry-preview-label">Current selected candidate</div>
              <div id="scryCurrentWrap" class="img-wrap scry-primary">
                <img id="scryCurrentImg" alt="Current selected Scryfall card image" />
                <img id="scryCurrentBackImg" class="scry-back-overlay hidden" alt="Back face preview" />
              </div>
            </div>
            <div class="scry-preview-col">
              <div class="scry-preview-label">Accepted mapping</div>
              <div id="scryAcceptedWrap" class="img-wrap scry-primary">
                <img id="scryAcceptedImg" alt="Accepted Scryfall mapping image" />
                <img id="scryAcceptedBackImg" class="scry-back-overlay hidden" alt="Accepted mapping back face preview" />
              </div>
            </div>
          </div>
          <div style=\"margin-top:8px; display:flex; justify-content:flex-start; gap:8px; flex-wrap:wrap;\">
            <a id=\"openScryfallCard\" class=\"btn ghost\" target=\"_blank\" rel=\"noreferrer\">Open Scryfall page</a>
            <span id=\"scryfallCandidateStatus\" class=\"row-note\">0/0</span>
          </div>
          <div class=\"scry-tools\" style=\"margin-top:8px;\">
            <label for=\"scryfallCodeInput\">Filter by set code</label>
            <input id=\"scryfallCodeInput\" placeholder=\"TARB\" />
            <button id=\"applyScryfallSetFilter\" class=\"ghost\">Filter</button>
            <button id=\"clearScryfallSetFilter\" class=\"ghost\">Show All</button>
            <label for=\"hideMappedScryfallCandidates\" class=\"scry-toggle\">
              <input id=\"hideMappedScryfallCandidates\" type=\"checkbox\" />
              Hide already mapped printings
            </label>
          </div>
          <div id=\"scryCandidates\" class=\"scry-candidates\"></div>
          <div class=\"img-meta\" id=\"scryMeta\"></div>
        </div>
      </div>
    </section>

    <section class=\"table-wrap\">
      <table>
        <thead>
          <tr>
            <th>Preview</th>
            <th>Decision</th>
            <th>Confidence</th>
            <th>idProduct</th>
            <th>idExpansion</th>
            <th>Name</th>
            <th>proposed_set</th>
            <th>collector</th>
            <th>scryfall_id</th>
            <th>mapped_set</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody id=\"rows\"></tbody>
      </table>
    </section>

    <section class=\"pagination\">
      <div id=\"pageInfo\"></div>
      <div class=\"pagination-controls\">
        <button id=\"prevPage\">Prev</button>
        <button id=\"nextPage\">Next</button>
      </div>
    </section>
    <div id=\"endNotice\" class=\"end-notice\">You reached the end of the table.</div>
  </div>

  <script>
    const EMBEDDED_ROWS = __EMBEDDED_ROWS__;
    const DATA_URL = __DATA_URL__;
    const STORAGE_KEY = 'mkm-review-decisions-v1';
    const MKM_CODE_STORAGE_KEY = 'mkm-expansion-code-map-v1';
    const SCRYFALL_CODE_STORAGE_KEY = 'scryfall-set-code-map-v1';
    const SCRYFALL_SELECTIONS_STORAGE_KEY = 'scryfall-selections-v1';
    const ACCEPTED_SELECTIONS_STORAGE_KEY = 'scryfall-accepted-selections-v1';
    const MANUAL_OVERRIDES_STORAGE_KEY = 'manual-scryfall-overrides-v1';

    const state = {
      rows: [],
      filtered: [],
      decisions: {},
      mkmCodes: {},
      scryfallCodes: {},
      scryfallSelections: {},
      acceptedSelections: {},
      manualOverrides: {},
      scryfallSetFilter: '',
      scryfallShowAllNames: false,
      hideMappedScryfallCandidates: false,
      page: 1,
      pageSize: 100,
      search: '',
      confidence: '',
      set: '',
      decision: '',
      selectedIdProduct: null,
      mkmCandidates: [],
      mkmCandidateIndex: 0,
      mkmRenderToken: 0,
      scryfallCandidateIndex: 0,
    };

    function esc(s) {
      return String(s ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('\"', '&quot;');
    }

    function confidenceClass(c) {
      if (c === 'high') return 'conf-high';
      if (c === 'medium') return 'conf-medium';
      if (c === 'low') return 'conf-low';
      return 'conf-none';
    }

    function describeReason(reason) {
      const text = String(reason || '').trim();
      if (!text) return '';

      let m = text.match(/^excluded idExpansion (\\d+) [(](.+)[)]$/);
      if (m) {
        return `Excluded Cardmarket expansion ${m[1]}: ${m[2]}.`;
      }

      m = text.match(/^unique missing-name match inside inferred set '([^']+)'(?:; matched via normalized name variant)?$/);
      if (m) {
        const viaVariant = text.includes('normalized name variant');
        return viaVariant
          ? `Exactly one missing Scryfall card matched this Cardmarket name inside inferred set '${m[1]}' using a normalized name variant.`
          : `Exactly one missing Scryfall card matched this Cardmarket name inside inferred set '${m[1]}'.`;
      }

      m = text.match(/^multiple missing-name matches in inferred set '([^']+)'(?:; matched via normalized name variant)?$/);
      if (m) {
        const viaVariant = text.includes('normalized name variant');
        return viaVariant
          ? `Multiple missing Scryfall cards matched this Cardmarket name inside inferred set '${m[1]}' (using normalized name matching), so manual review is needed.`
          : `Multiple missing Scryfall cards matched this Cardmarket name inside inferred set '${m[1]}', so manual review is needed.`;
      }

      if (text === 'unique missing-name match across all sets') {
        return 'Exactly one missing Scryfall card matched this Cardmarket name across all sets.';
      }
      if (text === 'unique missing-name match across all sets; matched via normalized name variant') {
        return 'Exactly one missing Scryfall card matched this Cardmarket name across all sets using a normalized name variant.';
      }
      if (text === 'multiple missing-name matches across sets') {
        return 'Multiple missing Scryfall cards matched this Cardmarket name across different sets, so manual review is needed.';
      }
      if (text === 'multiple missing-name matches across sets; matched via normalized name variant') {
        return 'Multiple missing Scryfall cards matched this Cardmarket name across different sets using normalized name matching, so manual review is needed.';
      }
      if (text === 'no candidate missing card found by name') {
        return 'No missing Scryfall card with a matching name was found.';
      }

      return text.charAt(0).toUpperCase() + text.slice(1);
    }

    function saveDecisions() {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.decisions));
      renderStats();
    }

    function saveMkmCodes() {
      localStorage.setItem(MKM_CODE_STORAGE_KEY, JSON.stringify(state.mkmCodes));
    }

    function saveScryfallCodes() {
      localStorage.setItem(SCRYFALL_CODE_STORAGE_KEY, JSON.stringify(state.scryfallCodes));
    }

    function saveManualOverrides() {
      localStorage.setItem(MANUAL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.manualOverrides));
    }

    function saveScryfallSelections() {
      localStorage.setItem(SCRYFALL_SELECTIONS_STORAGE_KEY, JSON.stringify(state.scryfallSelections));
    }

    function saveAcceptedSelections() {
      localStorage.setItem(ACCEPTED_SELECTIONS_STORAGE_KEY, JSON.stringify(state.acceptedSelections));
    }

    function saveManualOverrides() {
      localStorage.setItem(MANUAL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.manualOverrides));
    }

    function loadDecisions() {
      try { state.decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
      catch (_err) { state.decisions = {}; }
    }

    function loadMkmCodes() {
      try { state.mkmCodes = JSON.parse(localStorage.getItem(MKM_CODE_STORAGE_KEY) || '{}'); }
      catch (_err) { state.mkmCodes = {}; }
    }

    function loadScryfallCodes() {
      try { state.scryfallCodes = JSON.parse(localStorage.getItem(SCRYFALL_CODE_STORAGE_KEY) || '{}'); }
      catch (_err) { state.scryfallCodes = {}; }
    }

    function loadManualOverrides() {
      try { state.manualOverrides = JSON.parse(localStorage.getItem(MANUAL_OVERRIDES_STORAGE_KEY) || '{}'); }
      catch (_err) { state.manualOverrides = {}; }
    }

    function loadScryfallSelections() {
      try { state.scryfallSelections = JSON.parse(localStorage.getItem(SCRYFALL_SELECTIONS_STORAGE_KEY) || '{}'); }
      catch (_err) { state.scryfallSelections = {}; }
    }

    function loadAcceptedSelections() {
      try { state.acceptedSelections = JSON.parse(localStorage.getItem(ACCEPTED_SELECTIONS_STORAGE_KEY) || '{}'); }
      catch (_err) { state.acceptedSelections = {}; }
    }

    function getDecision(idProduct) {
      return state.decisions[String(idProduct)] || '';
    }

    function validateDecision(row, value) {
      const candidate = getActiveScryfallCandidate(row);
      const hasExistingCardmarketId = !!(candidate && candidate.cardmarket_id);
      const selectedMatchesCurrent = hasExistingCardmarketId && String(candidate.cardmarket_id) === String(row.idProduct);
      if ((value === 'accepted' || value === 'replace') && selectedMatchesCurrent) {
        return {
          ok: false,
          message: 'Accept/Replace disabled: selected Scryfall printing already points to this Cardmarket product ID.'
        };
      }
      if (value === 'accepted' && hasExistingCardmarketId) {
        return {
          ok: false,
          message: 'Accept is disabled for this selection because the chosen Scryfall printing already has a Cardmarket ID. Use Replace instead.'
        };
      }
      if (value === 'replace' && !hasExistingCardmarketId) {
        return {
          ok: false,
          message: 'Replace is only available when the selected Scryfall printing already has a Cardmarket ID.'
        };
      }
      return { ok: true, message: '' };
    }

    function setDecision(idProduct, value) {
      const row = getRowByIdProduct(idProduct);
      if (!row) return false;

      const validation = validateDecision(row, value);
      if (!validation.ok) {
        if (String(state.selectedIdProduct) === String(idProduct)) {
          const status = document.getElementById('compareDecisionStatus');
          if (status) status.textContent = validation.message;
        }
        return false;
      }

      state.decisions[String(idProduct)] = value;
      if (value === 'accepted' || value === 'replace') {
        state.acceptedSelections[String(idProduct)] = getScryfallCandidateIndex(row);
      } else {
        delete state.acceptedSelections[String(idProduct)];
      }
      saveDecisions();
      saveAcceptedSelections();
      if (String(state.selectedIdProduct) === String(idProduct)) {
        renderCompareById(idProduct);
        return true;
      }
      renderCompareDecisionState();
      renderTable();
      return true;
    }

    function renderCompareDecisionState() {
      const status = document.getElementById('compareDecisionStatus');
      if (!status) return;
      const acceptBtn = document.getElementById('compareAccept');
      const replaceBtn = document.getElementById('compareReplace');
      if (!state.selectedIdProduct) {
        status.textContent = 'Current decision: unreviewed';
        if (acceptBtn) acceptBtn.disabled = true;
        if (replaceBtn) replaceBtn.disabled = true;
        return;
      }
      const decision = getDecision(state.selectedIdProduct) || 'unreviewed';
      status.textContent = `Current decision: ${decision}`;

      const row = getRowByIdProduct(state.selectedIdProduct);
      const candidate = row ? getActiveScryfallCandidate(row) : null;
      const hasExistingCardmarketId = !!(candidate && candidate.cardmarket_id);
      const selectedMatchesCurrent = hasExistingCardmarketId && row && String(candidate.cardmarket_id) === String(row.idProduct);
      if (acceptBtn) {
        acceptBtn.disabled = hasExistingCardmarketId || selectedMatchesCurrent;
        acceptBtn.title = selectedMatchesCurrent
          ? 'Accept disabled: selected Scryfall printing already maps to this Cardmarket ID'
          : (hasExistingCardmarketId
              ? 'Accept disabled: selected Scryfall printing already has a Cardmarket ID'
              : '');
      }
      if (replaceBtn) {
        replaceBtn.disabled = !hasExistingCardmarketId || selectedMatchesCurrent;
        replaceBtn.title = selectedMatchesCurrent
          ? 'Replace disabled: selected Scryfall printing already maps to this Cardmarket ID'
          : (hasExistingCardmarketId
              ? 'Mark as replacement'
              : 'Replace requires a selected Scryfall printing with an existing Cardmarket ID');
      }
    }

    function getRowByIdProduct(idProduct) {
      return state.rows.find(r => String(r.idProduct) === String(idProduct));
    }

    function sanitizeMkmCode(value) {
      return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    }

    function sanitizeScryfallCode(value) {
      return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    }

    function sanitizeScryfallId(value) {
      return String(value || '').toLowerCase().replace(/[^a-z0-9-]/g, '').trim();
    }

    function getDefaultScryfallCandidates(row) {
      const decision = getDecision(row.idProduct);
      if (decision === 'rejected' && Array.isArray(row.all_scryfall_candidates) && row.all_scryfall_candidates.length > 0) {
        return row.all_scryfall_candidates;
      }
      if (Array.isArray(row.scryfall_candidates) && row.scryfall_candidates.length > 0) return row.scryfall_candidates;
      return [];
    }

    function getAllScryfallCandidatesForName(row) {
      if (Array.isArray(row.all_scryfall_candidates) && row.all_scryfall_candidates.length > 0) {
        return row.all_scryfall_candidates;
      }
      if (Array.isArray(row.scryfall_candidates) && row.scryfall_candidates.length > 0) {
        return row.scryfall_candidates;
      }
      if (row.proposed_scryfall_id) {
        return [{
          id: row.proposed_scryfall_id,
          oracle_id: row.proposed_oracle_id,
          set: row.proposed_set,
          set_name: '',
          collector_number: row.proposed_collector_number,
          lang: row.proposed_lang,
          name: row.name,
          border_color: '',
          foil: false,
          nonfoil: false,
          finishes: [],
          promo_types: [],
          has_separate_back_image: false,
        }];
      }
      return [];
    }

    function getScryfallCandidates(row) {
      if (state.scryfallSetFilter || state.scryfallShowAllNames) {
        return getAllScryfallCandidatesForName(row);
      }
      const defaults = getDefaultScryfallCandidates(row);
      if (defaults.length) return defaults;
      return getAllScryfallCandidatesForName(row);
    }

    function getScryfallCandidateIndex(row) {
      const candidates = getScryfallCandidates(row);
      if (!candidates.length) return 0;
      const saved = state.scryfallSelections[String(row.idProduct)];
      const initialDefault = getDecision(row.idProduct) === 'rejected'
        ? Number(row.initial_all_scryfall_candidate_index || 0)
        : Number(row.initial_scryfall_candidate_index || 0);
      const initial = Number.isInteger(saved) ? saved : initialDefault;
      return ((initial % candidates.length) + candidates.length) % candidates.length;
    }

    function getActiveScryfallCandidate(row) {
      const candidates = getScryfallCandidates(row);
      if (!candidates.length) return null;
      return candidates[getScryfallCandidateIndex(row)];
    }

    function getAcceptedScryfallCandidate(row) {
      if (getDecision(row.idProduct) !== 'accepted') return null;
      const candidates = getScryfallCandidates(row);
      if (!candidates.length) return null;
      const saved = state.acceptedSelections[String(row.idProduct)];
      if (!Number.isInteger(saved)) return null;
      const idx = saved;
      const normalized = ((idx % candidates.length) + candidates.length) % candidates.length;
      return candidates[normalized];
    }

    function setScryfallCandidateIndex(idProduct, nextIndex) {
      const row = getRowByIdProduct(idProduct);
      if (!row) return;
      const candidates = getScryfallCandidates(row);
      if (!candidates.length) return;
      state.scryfallSelections[String(idProduct)] = ((nextIndex % candidates.length) + candidates.length) % candidates.length;
      saveScryfallSelections();
      renderCompareById(idProduct);
    }

    function scryfallImageUrl(scryfallId, size = 'normal') {
      if (!scryfallId) return '';
      const id = String(scryfallId).toLowerCase();
      return `https://cards.scryfall.io/${size}/front/${id[0]}/${id[1]}/${id}.jpg`;
    }

    function scryfallBackImageUrl(scryfallId, size = 'normal') {
      if (!scryfallId) return '';
      const id = String(scryfallId).toLowerCase();
      return `https://cards.scryfall.io/${size}/back/${id[0]}/${id[1]}/${id}.jpg`;
    }

    function getVisibleScryfallCandidates(candidates, filterSetCode = '') {
      let toShow = candidates;
      if (filterSetCode) {
        toShow = toShow.filter(c => sanitizeScryfallCode(c.set) === filterSetCode);
      }
      if (state.hideMappedScryfallCandidates) {
        toShow = toShow.filter(c => !c.cardmarket_id);
      }
      return toShow;
    }

    function renderScryfallCandidates(row, candidates, activeIndex, filterSetCode = '') {
      const wrap = document.getElementById('scryCandidates');
      if (!wrap) return;
      const toShow = getVisibleScryfallCandidates(candidates, filterSetCode);
      if (!toShow.length) {
        const reason = state.hideMappedScryfallCandidates ? ' after hiding already mapped printings' : '';
        wrap.innerHTML = `<div style="padding:8px; color:#888;">No cards found${filterSetCode ? ` in set ${filterSetCode}` : ''}${reason}</div>`;
        return;
      }
      wrap.innerHTML = toShow.map((candidate, idx) => {
        const origIdx = candidates.indexOf(candidate);
        const active = origIdx === activeIndex ? ' active' : '';
        const hasCardmarketId = !!candidate.cardmarket_id;
        const alreadyMappedClass = hasCardmarketId ? ' has-cardmarket-id' : '';
        const thumb = scryfallImageUrl(candidate.id, 'small');
        const setCode = esc(candidate.set || '');
        const collector = esc(candidate.collector_number || '');
        const titleText = hasCardmarketId
          ? `Already has Cardmarket ID: ${candidate.cardmarket_id} (click to select replacement target)`
          : `Select ${setCode} #${collector}`;
        return `
          <button class="scry-candidate${active}${alreadyMappedClass}" data-scry-index="${origIdx}" title="${titleText}">
            <img src="${thumb}" alt="Scryfall candidate ${origIdx + 1}" loading="lazy" />
            <div class="scry-candidate-meta">${setCode} #${collector}</div>
          </button>
        `;
      }).join('');

      for (const btn of wrap.querySelectorAll('button[data-scry-index]')) {
        btn.addEventListener('click', () => {
          const next = Number(btn.getAttribute('data-scry-index'));
          if (!Number.isNaN(next)) setScryfallCandidateIndex(row.idProduct, next);
        });
      }
    }

    function scryfallCardPageUrl(candidate) {
      if (candidate && candidate.set && candidate.collector_number) {
        return `https://scryfall.com/card/${encodeURIComponent(candidate.set)}/${encodeURIComponent(candidate.collector_number)}`;
      }
      if (candidate && candidate.id) {
        return `https://scryfall.com/card/${encodeURIComponent(candidate.id)}`;
      }
      return 'https://scryfall.com';
    }

    function cardmarketProductUrl(idProduct) {
      return `https://www.cardmarket.com/en/Magic/Products?idProduct=${idProduct}`;
    }

    function proxiedMkmImageUrl(url) {
      return `/mkm-image?u=${encodeURIComponent(url)}`;
    }

    function mkmPlaceholderImage(text = 'No Cardmarket image') {
      const safe = encodeURIComponent(text);
      return `data:image/svg+xml,` +
        `%3Csvg xmlns='http://www.w3.org/2000/svg' width='700' height='980' viewBox='0 0 700 980'%3E` +
        `%3Crect width='100%25' height='100%25' fill='%23f3e7d5'/%3E` +
        `%3Crect x='36' y='36' width='628' height='908' rx='22' fill='%23fffaf2' stroke='%23d8c6aa' stroke-width='4'/%3E` +
        `%3Ctext x='50%25' y='49%25' text-anchor='middle' font-family='Trebuchet MS,Segoe UI,sans-serif' font-size='34' fill='%2371644f'%3E${safe}%3C/text%3E` +
        `%3C/svg%3E`;
    }

    function scryPlaceholderImage(text = 'Not mapped yet') {
      const safe = encodeURIComponent(text);
      return `data:image/svg+xml,` +
        `%3Csvg xmlns='http://www.w3.org/2000/svg' width='700' height='980' viewBox='0 0 700 980'%3E` +
        `%3Crect width='100%25' height='100%25' fill='%23e8f0f6'/%3E` +
        `%3Crect x='36' y='36' width='628' height='908' rx='22' fill='%23f8fbff' stroke='%23b9cfdf' stroke-width='4'/%3E` +
        `%3Ctext x='50%25' y='49%25' text-anchor='middle' font-family='Trebuchet MS,Segoe UI,sans-serif' font-size='34' fill='%23435d70'%3E${safe}%3C/text%3E` +
        `%3C/svg%3E`;
    }

    function buildMkmImageCandidates(row) {
      const out = [];
      const seen = new Set();
      const addCandidate = (code) => {
        if (!seen.has(code)) {
          seen.add(code);
          const base = `https://product-images.s3.cardmarket.com/1/${code}/${row.idProduct}`;
          out.push({
            code,
            front: `${base}/${row.idProduct}.jpg`,
            back: `${base}/${row.idProduct}_back.jpg`,
          });
        }
      };
      const mkmCodeInput = document.getElementById('mkmCodeInput');
      const fromInput = sanitizeMkmCode(mkmCodeInput ? mkmCodeInput.value : '');
      const fromRowCode = sanitizeMkmCode(row.expansion_code || '');
      const fromSaved = sanitizeMkmCode(state.mkmCodes[String(row.idExpansion)] || '');
      const fromProposed = sanitizeMkmCode(row.proposed_set || '');
      const fromMapped = sanitizeMkmCode(row.mapped_set || '');
      for (const code of [fromInput, fromSaved, fromRowCode, fromProposed, fromMapped]) {
        if (!code) continue;
        addCandidate(code);
      }
      return out;
    }

    async function checkImageExists(url) {
      return await new Promise((resolve) => {
        const probe = new Image();
        probe.onload = () => resolve(true);
        probe.onerror = () => resolve(false);
        probe.src = proxiedMkmImageUrl(url);
      });
    }

    async function updateMkmImage(row, startIndex = 0) {
      const frontImg = document.getElementById('mkmFrontImg');
      const backImg = document.getElementById('mkmBackImg');
      const backCol = document.getElementById('mkmBackCol');
      const meta = document.getElementById('mkmMeta');
      if (!frontImg || !backImg || !backCol || !meta) return;
      const cands = buildMkmImageCandidates(row);
      state.mkmCandidates = cands;
      if (cands.length > 0) {
        state.mkmCandidateIndex = ((startIndex % cands.length) + cands.length) % cands.length;
      } else {
        state.mkmCandidateIndex = 0;
      }
      const renderToken = ++state.mkmRenderToken;

      if (!cands.length) {
        frontImg.src = mkmPlaceholderImage('No Cardmarket image URL');
        backCol.classList.add('hidden');
        meta.textContent = 'No Cardmarket image URL candidates.';
        return;
      }

      for (let idx = state.mkmCandidateIndex; idx < cands.length; idx += 1) {
        const current = cands[idx];
        const frontExists = await checkImageExists(current.front);
        if (renderToken !== state.mkmRenderToken) return;
        if (!frontExists) continue;

        state.mkmCandidateIndex = idx;
        frontImg.src = proxiedMkmImageUrl(current.front);
        const mkmCodeInput = document.getElementById('mkmCodeInput');
        if (mkmCodeInput) {
          mkmCodeInput.value = current.code;
        }

        const backExists = await checkImageExists(current.back);
        if (renderToken !== state.mkmRenderToken) return;
        if (backExists) {
          backCol.classList.remove('hidden');
          backImg.src = proxiedMkmImageUrl(current.back);
        } else {
          backCol.classList.add('hidden');
          backImg.src = mkmPlaceholderImage('No back image');
        }

        meta.innerHTML = `Candidate ${idx + 1}/${cands.length} (${esc(current.code)})` +
          `<br>Front: ${esc(current.front)}` +
          (backExists ? `<br>Back: ${esc(current.back)}` : '<br>Back: not available');
        return;
      }

      frontImg.src = mkmPlaceholderImage('Cardmarket image unavailable');
      backCol.classList.add('hidden');
      meta.textContent = 'All candidates failed. Open product page and set MKM code manually.';
    }

    function renderCompareById(idProduct) {
      const row = getRowByIdProduct(idProduct);
      if (!row) return;
      const isNewSelection = String(state.selectedIdProduct) !== String(row.idProduct);
      state.selectedIdProduct = row.idProduct;
      if (isNewSelection) {
        state.scryfallSetFilter = '';
        state.scryfallShowAllNames = false;
      }

      const expPart = row.expansion_name ? ` [${row.expansion_name}]` : '';
      document.getElementById('compareTitle').textContent = `Reviewing idProduct ${row.idProduct} - ${row.name}${expPart}`;

      const productLink = document.getElementById('openMkmProduct');
      productLink.href = cardmarketProductUrl(row.idProduct);
      productLink.textContent = `Open product #${row.idProduct}`;

      const input = document.getElementById('mkmCodeInput');
      if (input) {
        input.value = state.mkmCodes[String(row.idExpansion)] || sanitizeMkmCode(row.expansion_code || row.proposed_set || row.mapped_set || '');
      }

      const scryfallCandidates = getScryfallCandidates(row);
      state.scryfallCandidateIndex = getScryfallCandidateIndex(row);

      // Backfill accepted selection for legacy accepted rows that predate acceptedSelections persistence.
      if (getDecision(row.idProduct) === 'accepted' && !Number.isInteger(state.acceptedSelections[String(row.idProduct)])) {
        state.acceptedSelections[String(row.idProduct)] = state.scryfallCandidateIndex;
        saveAcceptedSelections();
      }

      const activeCandidate = getActiveScryfallCandidate(row);
      const scryStatus = document.getElementById('scryfallCandidateStatus');
      const shownScryfallCandidates = getVisibleScryfallCandidates(scryfallCandidates, state.scryfallSetFilter);
      scryStatus.textContent = shownScryfallCandidates.length ? `${shownScryfallCandidates.length} arts shown` : '0 arts';

      const hideMappedToggle = document.getElementById('hideMappedScryfallCandidates');
      if (hideMappedToggle) {
        hideMappedToggle.checked = !!state.hideMappedScryfallCandidates;
      }

      const scryfallCodeInput = document.getElementById('scryfallCodeInput');
      if (isNewSelection) {
        if (scryfallCodeInput) {
          scryfallCodeInput.value = '';
        }
      } else if (scryfallCodeInput) {
        scryfallCodeInput.value = state.scryfallSetFilter;
      }
      
      renderScryfallCandidates(row, scryfallCandidates, state.scryfallCandidateIndex, state.scryfallSetFilter);

      const scryCurrentImg = document.getElementById('scryCurrentImg');
      const scryCurrentBackImg = document.getElementById('scryCurrentBackImg');
      const scryAcceptedImg = document.getElementById('scryAcceptedImg');
      const scryAcceptedBackImg = document.getElementById('scryAcceptedBackImg');
      const scryMeta = document.getElementById('scryMeta');
      const scryfallLink = document.getElementById('openScryfallCard');
      const currentUrl = activeCandidate ? scryfallImageUrl(activeCandidate.id) : '';
      const currentBackUrl = (activeCandidate && activeCandidate.has_separate_back_image)
        ? scryfallBackImageUrl(activeCandidate.id)
        : '';
      const acceptedCandidate = getAcceptedScryfallCandidate(row);
      const acceptedUrl = acceptedCandidate ? scryfallImageUrl(acceptedCandidate.id) : '';
      const acceptedBackUrl = (acceptedCandidate && acceptedCandidate.has_separate_back_image)
        ? scryfallBackImageUrl(acceptedCandidate.id)
        : '';

      if (currentUrl && activeCandidate) {
        if (scryCurrentImg) scryCurrentImg.src = currentUrl;
        if (scryCurrentBackImg) {
          if (currentBackUrl) {
            scryCurrentBackImg.src = currentBackUrl;
            scryCurrentBackImg.classList.remove('hidden');
          } else {
            scryCurrentBackImg.src = '';
            scryCurrentBackImg.classList.add('hidden');
          }
        }
        if (scryAcceptedImg) scryAcceptedImg.src = acceptedUrl || scryPlaceholderImage('Not mapped yet');
        if (scryAcceptedBackImg) {
          if (acceptedBackUrl) {
            scryAcceptedBackImg.src = acceptedBackUrl;
            scryAcceptedBackImg.classList.remove('hidden');
          } else {
            scryAcceptedBackImg.src = '';
            scryAcceptedBackImg.classList.add('hidden');
          }
        }
        scryfallLink.href = scryfallCardPageUrl(activeCandidate);
        scryfallLink.textContent = activeCandidate.set && activeCandidate.collector_number
          ? `Open Scryfall: ${activeCandidate.set} ${activeCandidate.collector_number}`
          : 'Open Scryfall page';
        const finishes = Array.isArray(activeCandidate.finishes) ? activeCandidate.finishes.join(', ') : '';
        const promoTypes = Array.isArray(activeCandidate.promo_types) ? activeCandidate.promo_types.join(', ') : '';
        const overrideCode = activeCandidate.id ? state.scryfallCodes[String(activeCandidate.id)] : '';
        const metaLines = [
          `<strong>Name:</strong> ${esc(activeCandidate.name || row.name)}`,
          `<strong>Set code:</strong> ${esc(activeCandidate.set)} <strong>Set name:</strong> ${esc(activeCandidate.set_name || '')}`,
          `<strong>Collector #:</strong> ${esc(activeCandidate.collector_number)} <strong>Border color:</strong> ${esc(activeCandidate.border_color || '')}`,
          `<strong>Foil:</strong> ${activeCandidate.foil ? 'yes' : 'no'} <strong>Nonfoil:</strong> ${activeCandidate.nonfoil ? 'yes' : 'no'}`,
          `<strong>Finishes:</strong> ${esc(finishes || 'none')}`,
          `<strong>Promo types:</strong> ${esc(promoTypes || 'none')}`,
          `<strong>Match Confidence:</strong> ${esc(row.confidence)} <strong>Mapped set:</strong> ${esc(row.mapped_set)} (${esc(row.mapped_set_confidence ?? '')})`,
          `<strong>Reason:</strong> ${esc(describeReason(row.reason))}`,
        ];
        if (overrideCode) {
          metaLines.push(`<strong>Override set code:</strong> ${esc(overrideCode)}`);
        }
        scryMeta.innerHTML = metaLines.join('<br>');
      } else {
        if (scryCurrentImg) scryCurrentImg.src = scryPlaceholderImage('No current Scryfall card');
        if (scryCurrentBackImg) {
          scryCurrentBackImg.src = '';
          scryCurrentBackImg.classList.add('hidden');
        }
        if (scryAcceptedImg) scryAcceptedImg.src = acceptedUrl || scryPlaceholderImage('Not mapped yet');
        if (scryAcceptedBackImg) {
          if (acceptedBackUrl) {
            scryAcceptedBackImg.src = acceptedBackUrl;
            scryAcceptedBackImg.classList.remove('hidden');
          } else {
            scryAcceptedBackImg.src = '';
            scryAcceptedBackImg.classList.add('hidden');
          }
        }
        scryfallLink.href = 'https://scryfall.com';
        scryfallLink.textContent = 'Open Scryfall page';
        scryMeta.textContent = 'No proposed Scryfall image URL.';
      }

      updateMkmImage(row, 0);
      renderCompareDecisionState();
      renderTable();
    }

    function navigateToNext() {
      const status = document.getElementById('compareDecisionStatus');
      if (!state.selectedIdProduct || !state.filtered.length) {
        if (status) status.textContent = 'No next card to review.';
        return;
      }
      const idx = state.filtered.findIndex(r => String(r.idProduct) === String(state.selectedIdProduct));
      if (idx === -1 || idx + 1 >= state.filtered.length) {
        if (status) status.textContent = 'No next card to review.';
        return;
      }
      const nextRow = state.filtered[idx + 1];
      const nextPage = Math.floor((idx + 1) / state.pageSize) + 1;
      if (nextPage !== state.page) { state.page = nextPage; renderTable(); }
      renderCompareById(nextRow.idProduct);
    }

    function renderStats() {
      const total = state.rows.length;
      const accepted = Object.values(state.decisions).filter(v => v === 'accepted').length;
      const replaced = Object.values(state.decisions).filter(v => v === 'replace').length;
      const rejected = Object.values(state.decisions).filter(v => v === 'rejected').length;
      const skipped = Object.values(state.decisions).filter(v => v === 'skipped').length;
      const ignored = Object.values(state.decisions).filter(v => v === 'ignored').length;
      const reviewed = accepted + replaced + rejected + skipped + ignored;
      const byConf = { high: 0, medium: 0, low: 0, none: 0 };
      for (const r of state.rows) byConf[r.confidence] = (byConf[r.confidence] || 0) + 1;
      const cards = [
        ['Total rows', total], ['Reviewed', reviewed], ['Accepted', accepted], ['Rejected', rejected],
        ['Replaced', replaced], ['Skipped', skipped], ['Ignored', ignored], ['High', byConf.high], ['Medium', byConf.medium], ['Low', byConf.low], ['None', byConf.none]
      ];
      document.getElementById('stats').innerHTML = cards
        .map(([k, v]) => `<div class=\"stat\"><div class=\"k\">${esc(k)}</div><div class=\"v\">${esc(v)}</div></div>`)
        .join('');
    }

    function applyFilters() {
      const q = state.search.trim().toLowerCase();
      state.filtered = state.rows.filter(r => {
        if (state.confidence && r.confidence !== state.confidence) return false;
        if (state.set && String(r.idExpansion ?? '') !== state.set) return false;
        const d = getDecision(r.idProduct) || 'unreviewed';
        if (state.decision && d !== state.decision) return false;
        if (!q) return true;
        const blob = [r.idProduct, r.idMetacard, r.idExpansion, r.expansion_name, r.name, r.reason, r.proposed_set, r.mapped_set, r.proposed_scryfall_id].join(' ').toLowerCase();
        return blob.includes(q);
      });
      state.page = 1;
      renderTable();
      if (state.filtered.length > 0) {
        renderCompareById(state.filtered[0].idProduct);
      }
    }

    function renderTable() {
      const tbody = document.getElementById('rows');
      const total = state.filtered.length;
      const maxPage = Math.max(1, Math.ceil(total / state.pageSize));
      if (state.page > maxPage) state.page = maxPage;
      const start = (state.page - 1) * state.pageSize;
      const pageRows = state.filtered.slice(start, start + state.pageSize);

      tbody.innerHTML = pageRows.map(r => {
        const d = getDecision(r.idProduct);
        const selectedClass = String(r.idProduct) === String(state.selectedIdProduct) ? 'sel-row' : '';
        const decisionTag = d ? `<span class=\"decision-tag ${d}\">${esc(d)}</span>` : '<span class=\"row-note\">unreviewed</span>';
        return `
          <tr class=\"${selectedClass}\" data-row-id=\"${r.idProduct}\">
            <td><button class=\"tiny ghost\" data-preview=\"${r.idProduct}\">View</button></td>
            <td>
              <div>${decisionTag}</div>
              <div class=\"decision-cell\">
                <button class=\"tiny yes\" data-id=\"${r.idProduct}\" data-d=\"accepted\">Accept</button>
                <button class="tiny repbtn" data-id="${r.idProduct}" data-d="replace">Replace</button>
                <button class=\"tiny no\" data-id=\"${r.idProduct}\" data-d=\"rejected\">Reject</button>
                <button class=\"tiny skip\" data-id=\"${r.idProduct}\" data-d=\"skipped\">Skip</button>
                <button class=\"tiny ignore\" data-id=\"${r.idProduct}\" data-d=\"ignored\">Ignore</button>
              </div>
            </td>
            <td><span class=\"pill ${confidenceClass(r.confidence)}\">${esc(r.confidence)}</span></td>
            <td>${esc(r.idProduct)}</td>
            <td>${esc(r.idExpansion)} <span class=\"row-note\">${esc(r.expansion_name || '')}</span></td>
            <td>${esc(r.name)}</td>
            <td>${esc(r.proposed_set)}</td>
            <td>${esc(r.proposed_collector_number)}</td>
            <td>${esc(r.proposed_scryfall_id)}</td>
            <td>${esc(r.mapped_set)} <span class=\"row-note\">(${esc(r.mapped_set_confidence ?? '')})</span></td>
            <td>${esc(describeReason(r.reason))}</td>
          </tr>
        `;
      }).join('');

      const pageInfo = document.getElementById('pageInfo');
      const prevBtn = document.getElementById('prevPage');
      const nextBtn = document.getElementById('nextPage');
      const endNotice = document.getElementById('endNotice');
      const atStart = state.page <= 1;
      const atEnd = total === 0 || state.page >= maxPage;

      prevBtn.disabled = atStart;
      nextBtn.disabled = atEnd;

      if (total === 0) {
        pageInfo.textContent = 'No rows match current filters.';
      } else {
        pageInfo.textContent = `Showing ${start + 1}-${Math.min(start + state.pageSize, total)} of ${total} rows (page ${state.page}/${maxPage})`;
      }

      if (endNotice) {
        if (total > 0 && atEnd) {
          endNotice.textContent = `End of table reached. You are on the final page (${state.page}/${maxPage}).`;
          endNotice.classList.add('visible');
        } else {
          endNotice.classList.remove('visible');
        }
      }

      for (const btn of tbody.querySelectorAll('button[data-id][data-d]')) {
        btn.addEventListener('click', () => setDecision(btn.getAttribute('data-id'), btn.getAttribute('data-d')));
      }
      for (const btn of tbody.querySelectorAll('button[data-preview]')) {
        btn.addEventListener('click', () => {
          window.scrollTo({ top: 0, behavior: 'auto' });
          renderCompareById(btn.getAttribute('data-preview'));
        });
      }
      for (const tr of tbody.querySelectorAll('tr[data-row-id]')) {
        tr.addEventListener('dblclick', () => renderCompareById(tr.getAttribute('data-row-id')));
      }
    }

    function downloadText(filename, text, mime) {
      const blob = new Blob([text], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }

    function toCsvRow(values) {
      const NEWLINE = String.fromCharCode(10);
      return values.map(v => {
        const s = String(v ?? '');
        if (s.includes(',') || s.includes('\"') || s.includes(NEWLINE)) return '\"' + s.replaceAll('\"', '\"\"') + '\"';
        return s;
      }).join(',');
    }

    function exportAccepted() {
      const fields = ['name', 'set', 'cn', 'scryfall_id', 'new_cardmarket_id'];
      const acceptedRows = state.rows.filter(r => getDecision(r.idProduct) === 'accepted');
      const lines = [toCsvRow(fields)];
      for (const r of acceptedRows) {
        const candidate = getAcceptedScryfallCandidate(r) || getActiveScryfallCandidate(r);
        const exportRow = {
          name: r.name || '',
          set: (candidate && candidate.set) || r.proposed_set || '',
          cn: (candidate && candidate.collector_number) || r.proposed_collector_number || '',
          scryfall_id: (candidate && candidate.id) || r.proposed_scryfall_id || '',
          new_cardmarket_id: r.idProduct || ''
        };
        lines.push(toCsvRow(fields.map(f => exportRow[f])));
      }
      downloadText('accepted_mappings.csv', lines.join(String.fromCharCode(10)), 'text/csv;charset=utf-8');
    }

    function exportReplacements() {
      const fields = ['name', 'set', 'cn', 'scryfall_id', 'old_cardmarket_id', 'new_cardmarket_id'];
      const replaceRows = state.rows.filter(r => getDecision(r.idProduct) === 'replace');
      const lines = [toCsvRow(fields)];
      for (const r of replaceRows) {
        const candidate = getAcceptedScryfallCandidate(r) || getActiveScryfallCandidate(r);
        if (!candidate || !candidate.cardmarket_id) continue;
        const oldId = String(candidate.cardmarket_id);
        const newId = String(r.idProduct || '');
        if (!newId || oldId === newId) continue;
        const exportRow = {
          name: candidate.name || r.name || '',
          set: candidate.set || r.proposed_set || '',
          cn: candidate.collector_number || r.proposed_collector_number || '',
          scryfall_id: candidate.id || r.proposed_scryfall_id || '',
          old_cardmarket_id: oldId,
          new_cardmarket_id: newId,
        };
        lines.push(toCsvRow(fields.map(f => exportRow[f])));
      }
      downloadText('replacement_mappings.csv', lines.join(String.fromCharCode(10)), 'text/csv;charset=utf-8');
    }

    function exportDecisions() {
      const payload = {
        exportedAt: new Date().toISOString(),
        decisions: state.decisions,
        mkmCodes: state.mkmCodes,
        scryfallSelections: state.scryfallSelections,
        acceptedSelections: state.acceptedSelections,
      };
      downloadText('review_decisions.json', JSON.stringify(payload, null, 2), 'application/json;charset=utf-8');
    }

    function bindControls() {
      document.getElementById('searchInput').addEventListener('input', e => { state.search = e.target.value || ''; applyFilters(); });
      document.getElementById('confidenceFilter').addEventListener('change', e => { state.confidence = e.target.value || ''; applyFilters(); });
      document.getElementById('setFilter').addEventListener('change', e => { state.set = e.target.value || ''; applyFilters(); });
      document.getElementById('decisionFilter').addEventListener('change', e => { state.decision = e.target.value || ''; applyFilters(); });
      document.getElementById('pageSize').addEventListener('change', e => { state.pageSize = Number(e.target.value) || 100; renderTable(); });
      document.getElementById('prevPage').addEventListener('click', () => { state.page = Math.max(1, state.page - 1); renderTable(); });
      document.getElementById('nextPage').addEventListener('click', () => {
        const maxPage = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
        state.page = Math.min(maxPage, state.page + 1);
        renderTable();
      });

      document.getElementById('exportAccepted').addEventListener('click', exportAccepted);
      document.getElementById('exportReplacements').addEventListener('click', exportReplacements);
      document.getElementById('exportDecisions').addEventListener('click', exportDecisions);

      document.getElementById('saveMkmCode').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        const row = getRowByIdProduct(state.selectedIdProduct);
        if (!row) return;
        const code = sanitizeMkmCode(document.getElementById('mkmCodeInput').value);
        if (!code) return;
        state.mkmCodes[String(row.idExpansion)] = code;
        saveMkmCodes();
        updateMkmImage(row, 0);
      });

      document.getElementById('applyScryfallSetFilter').addEventListener('click', () => {
        const code = sanitizeScryfallCode(document.getElementById('scryfallCodeInput').value);
        if (!code) return;
        if (!state.selectedIdProduct) return;
        const row = getRowByIdProduct(state.selectedIdProduct);
        if (!row) return;
        state.scryfallSetFilter = code;
        state.scryfallShowAllNames = false;
        const scryfallCandidates = getScryfallCandidates(row);
        const filteredCandidates = scryfallCandidates.filter(c => sanitizeScryfallCode(c.set) === code);
        if (!filteredCandidates.length) {
          renderCompareById(row.idProduct);
          return;
        }
        const preferredId = filteredCandidates[0].id;
        const preferredIndex = scryfallCandidates.findIndex(c => c.id === preferredId);
        if (preferredIndex >= 0) {
          setScryfallCandidateIndex(row.idProduct, preferredIndex);
        } else {
          renderCompareById(row.idProduct);
        }
      });

      document.getElementById('clearScryfallSetFilter').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        const row = getRowByIdProduct(state.selectedIdProduct);
        if (!row) return;
        const activeCandidate = getActiveScryfallCandidate(row);
        state.scryfallSetFilter = '';
        state.scryfallShowAllNames = true;
        const scryfallCandidates = getScryfallCandidates(row);
        if (!scryfallCandidates.length) {
          renderCompareById(row.idProduct);
          return;
        }
        let preferredIndex = 0;
        if (activeCandidate && activeCandidate.id) {
          const byId = scryfallCandidates.findIndex(c => c.id === activeCandidate.id);
          if (byId >= 0) preferredIndex = byId;
        }
        setScryfallCandidateIndex(row.idProduct, preferredIndex);
      });

      document.getElementById('hideMappedScryfallCandidates').addEventListener('change', e => {
        state.hideMappedScryfallCandidates = !!e.target.checked;
        if (!state.selectedIdProduct) return;
        const row = getRowByIdProduct(state.selectedIdProduct);
        if (!row) return;
        const scryfallCandidates = getScryfallCandidates(row);
        const visibleCandidates = getVisibleScryfallCandidates(scryfallCandidates, state.scryfallSetFilter);
        if (!visibleCandidates.length) {
          renderCompareById(row.idProduct);
          return;
        }
        const activeCandidate = getActiveScryfallCandidate(row);
        if (activeCandidate && visibleCandidates.some(c => c.id === activeCandidate.id)) {
          renderCompareById(row.idProduct);
          return;
        }
        const nextCandidateId = visibleCandidates[0].id;
        const nextIndex = scryfallCandidates.findIndex(c => c.id === nextCandidateId);
        if (nextIndex >= 0) {
          setScryfallCandidateIndex(row.idProduct, nextIndex);
          return;
        }
        renderCompareById(row.idProduct);
      });

      document.getElementById('nextMkmCandidate').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        const row = getRowByIdProduct(state.selectedIdProduct);
        if (!row) return;
        if (!state.mkmCandidates.length) updateMkmImage(row, 0);
        else updateMkmImage(row, state.mkmCandidateIndex + 1);
      });

      document.getElementById('compareAccept').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        if (setDecision(state.selectedIdProduct, 'accepted')) navigateToNext();
      });

      document.getElementById('compareReplace').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        if (setDecision(state.selectedIdProduct, 'replace')) navigateToNext();
      });

      document.getElementById('compareReject').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        if (setDecision(state.selectedIdProduct, 'rejected')) navigateToNext();
      });

      document.getElementById('compareSkip').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        if (setDecision(state.selectedIdProduct, 'skipped')) navigateToNext();
      });

      document.getElementById('compareIgnore').addEventListener('click', () => {
        if (!state.selectedIdProduct) return;
        if (setDecision(state.selectedIdProduct, 'ignored')) navigateToNext();
      });
    }

    async function init() {
      if (Array.isArray(EMBEDDED_ROWS) && EMBEDDED_ROWS.length > 0) {
        state.rows = EMBEDDED_ROWS;
      } else {
        const res = await fetch(DATA_URL);
        if (!res.ok) throw new Error(`Failed to load data file: ${DATA_URL}`);
        state.rows = await res.json();
      }

      loadDecisions();
      loadMkmCodes();
      loadScryfallCodes();
      loadScryfallSelections();
      loadAcceptedSelections();
      loadManualOverrides();
      bindControls();

      const setValues = Array.from(
        new Map(
          state.rows
            .filter(r => r.idExpansion !== null && r.idExpansion !== undefined)
            .map(r => [String(r.idExpansion), { id: String(r.idExpansion), name: r.expansion_name || '' }])
        ).values()
      ).sort((a, b) => {
        const na = (a.name || '').toLowerCase();
        const nb = (b.name || '').toLowerCase();
        if (na < nb) return -1;
        if (na > nb) return 1;
        return Number(a.id) - Number(b.id);
      });
      const setFilter = document.getElementById('setFilter');
      for (const setEntry of setValues) {
        const opt = document.createElement('option');
        opt.value = setEntry.id;
        opt.textContent = setEntry.name ? `${setEntry.name} (${setEntry.id})` : `Expansion ${setEntry.id}`;
        setFilter.appendChild(opt);
      }

      state.filtered = [...state.rows];
      renderStats();
      renderTable();
      if (state.rows.length > 0) renderCompareById(state.rows[0].idProduct);
    }

    init().catch(err => {
      document.body.innerHTML = `<pre style=\"padding:20px;color:#8a3434\">${esc(String(err))}</pre>`;
      console.error(err);
    });
  </script>
</body>
</html>
"""

    return (
        template.replace("__TITLE__", title)
        .replace("__DATA_URL__", json.dumps(data_json_name))
        .replace("__EMBEDDED_ROWS__", embedded_rows_json)
    )


def main():
    parser = argparse.ArgumentParser(description="Build a local HTML UI to review proposed mappings")
    parser.add_argument("--input", default="missing_cardmarket_id_mappings.csv", help="Input mapping CSV")
    parser.add_argument("--cards-json", default="default-cards-20260312090730.json", help="Scryfall default cards JSON for alternate-art candidate discovery")
    parser.add_argument("--data-output", default="review_data.json", help="Generated JSON data file")
    parser.add_argument("--html-output", default="review_ui.html", help="Generated HTML UI file")
    parser.add_argument("--expansions-html", default="expansions.html", help="Cardmarket expansions HTML select source")
    parser.add_argument("--expansion-map-output", default="cardmarket_expansion_map.json", help="Output JSON mapping idExpansion to Cardmarket set name")
    parser.add_argument("--expansion-code-map", default="cardmarket_expansion_code_overrides.json", help="Optional JSON mapping idExpansion to Cardmarket image set code")
    parser.add_argument("--title", default="Cardmarket Mapping Review", help="Page title")
    parser.add_argument(
        "--embed-data",
        action="store_true",
        help="Embed rows directly into HTML (useful for opening file:// URLs without a server)",
    )
    args = parser.parse_args()

    expansion_map: dict[int, str] = {}
    expansion_code_map: dict[int, str] = {}
    scryfall_candidate_index: dict[tuple[str, str], list[dict]] = {}
    scryfall_candidate_name_index: dict[str, list[dict]] = {}

    expansions_path = Path(args.expansions_html)
    if expansions_path.exists():
        expansion_map = load_expansion_map(expansions_path)
        expansion_map_out = {str(k): v for k, v in sorted(expansion_map.items())}
        Path(args.expansion_map_output).write_text(json.dumps(expansion_map_out, ensure_ascii=False, indent=2), encoding="utf-8")

    expansion_code_map_path = Path(args.expansion_code_map)
    if expansion_code_map_path.exists():
        raw = json.loads(expansion_code_map_path.read_text(encoding="utf-8"))
        for key, value in raw.items():
            exp_id = to_int(key)
            if exp_id is not None and isinstance(value, str) and value.strip():
                expansion_code_map[exp_id] = value.strip().upper()

    cards_path = Path(args.cards_json)
    if cards_path.exists():
      scryfall_candidate_index, scryfall_candidate_name_index = load_scryfall_candidate_index(cards_path)

    rows = load_rows(
      Path(args.input),
      expansion_map,
      expansion_code_map,
      scryfall_candidate_index,
      scryfall_candidate_name_index,
    )

    with Path(args.data_output).open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=True, separators=(",", ":"))

    embedded_rows_json = json.dumps(rows, ensure_ascii=True, separators=(",", ":")) if args.embed_data else "[]"
    html_output = build_html(args.data_output, args.title, embedded_rows_json)
    Path(args.html_output).write_text(html_output, encoding="utf-8")

    print(f"Rows: {len(rows)}")
    print(f"Expansion mappings: {len(expansion_map)}")
    print(f"Expansion code overrides: {len(expansion_code_map)}")
    print(f"Scryfall candidate keys (set+name): {len(scryfall_candidate_index)}")
    print(f"Scryfall candidate keys (name-only): {len(scryfall_candidate_name_index)}")
    if expansion_map:
        print(f"Wrote: {args.expansion_map_output}")
    print(f"Wrote: {args.data_output}")
    print(f"Wrote: {args.html_output}")


if __name__ == "__main__":
    main()
