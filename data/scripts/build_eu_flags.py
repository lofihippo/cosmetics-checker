#!/usr/bin/env python3
"""Build the curated data/eu_flags.json used by the Phase 0 eu_lint.py prototype.

This is a *curated, hand-verified seed* of high-signal substances drawn from
the official EU regulatory sources. It is NOT a comprehensive or authoritative
dataset -- Phase 1 (eu-reg-lists) replaces it with official export files.

Data model (fixes duplicate-overwrite, unsupported-inference and provenance
issues):
  * A **substance** is a stable identity (id, name, aliases, cas, ec, ...).
  * A substance carries zero or more **rules** (regulatory determinations).
  * Presence in this identity list does NOT assert a regulatory status. A
    substance with no verified rule has `determination: "unknown"` -- we never
    claim "no restriction" merely because an identity row exists.
  * Names/aliases resolve to a single substance. If an alias would ambiguously
    map to more than one substance (or collide with another substance's
    canonical name), it is treated as unresolved rather than silently picking
    whichever row loads last.

Two kinds of substances:
  1. Verified-rule substances: `determination` is one of
     banned/restricted/allowed_with_conditions and they carry at least one
     `rules` entry with a citation, source_url and (where known) effective_date.
  2. Identity-only substances: `determination: "unknown"`, `rules: []`. These
     are recognised names with NO verified EU determination. They are reported
     as such (never as clean).

Provenance fields:
  * `source_url` is per-rule (food vs cosmetic vs SVHC differ).
  * `source_updated_at` is the upstream publication/update date when known,
    else None (we never invent a date).
  * `build_time` / `data_updated_at` is when this artifact was generated and
    documents that these are curated, not live-fetched data.

Usage:
    python data/scripts/build_eu_flags.py              # writes data/eu_flags.json
    python data/scripts/build_eu_flags.py --output X   # writes to a custom path
"""

import json
import pathlib
import time
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "eu_flags.json"

EU_COSMETIC_URL = (
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32009R1223"
)
EU_FOOD_URL = (
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A02008R1333"
)
ECHA_CAND_URL = "https://echa.europa.eu/candidate-list-table"

# Upstream source descriptions + whether we have a verified date.
SOURCE_NOTE = {
    "cosmetic": {
        "name": "Regulation (EC) No 1223/2009 -- cosmetic annexes",
        "url": EU_COSMETIC_URL,
        "source_updated_at": None,  # curated; we do not assert a live date
    },
    "food": {
        "name": "Regulation (EC) No 1333/2008 -- food additives",
        "url": EU_FOOD_URL,
        "source_updated_at": None,
    },
    "svhc": {
        "name": "ECHA SVHC Candidate List",
        "url": ECHA_CAND_URL,
        "source_updated_at": None,
    },
}

# Container keyed by substance id.
SUBSTANCES: dict = {}


def _make_identity(name, aliases, cas, ec, e_number, ci):
    return {
        "name": name,
        "aliases": aliases or [],
        "cas": cas,
        "ec": ec,
        "e_number": e_number,
        "ci": ci,
    }


def add_substance(name, aliases, cas=None, ec=None, e_number=None, ci=None):
    """Declare a substance identity (no determination implied)."""
    key = name.strip().lower()
    if key in SUBSTANCES:
        raise ValueError(f"duplicate substance identity: {name!r}")
    SUBSTANCES[key] = {
        "id": "-".join(key.split()),
        **_make_identity(name, aliases, cas, ec, e_number, ci),
        "determination": "unknown",
        "rules": [],
    }
    return SUBSTANCES[key]


def add_rule(name, status, annex, source, conditions, effective_date=None,
             extra_url=None):
    """Attach a regulatory rule (determination) to an existing substance.

    `source` is one of SOURCE_NOTE keys (cosmetic|food|svhc). A substance may
    carry several rules (e.g. both a food rule and a cosmetic rule); these are
    distinct legitimate determinations, not duplicates.
    """
    key = name.strip().lower()
    if key not in SUBSTANCES:
        raise ValueError(f"unknown substance for rule: {name!r}")
    note = SOURCE_NOTE[source]
    sub = SUBSTANCES[key]
    sub["determination"] = status  # strongest applicable is set by caller
    sub["rules"].append({
        "status": status,
        "annex": annex,
        "regulation": note["name"],
        "source_url": extra_url or note["url"],
        "source_updated_at": note["source_updated_at"],
        "conditions": conditions,
        "effective_date": effective_date,
    })


# ---------------------------------------------------------------------------
# Verified-rule substances (each rule carries a real citation/source).
# ---------------------------------------------------------------------------

# --- Annex II: prohibited (cosmetic) ---
add_substance("Zinc pyrithione",
              ["zinc pyrithione", "pyrithione zinc", "zpt", "zinc omadine"],
              "13463-41-7", "236-671-3")
add_rule("Zinc pyrithione", "banned", "Annex II", "cosmetic",
         "Prohibited in cosmetic products under Annex II (previously "
         "restricted; banned entry effective 2022).")

add_substance("Lilial",
              ["lilial", "lily aldehyde", "butylphenyl methylpropional",
               "lyral", "bmhca"],
              "80-54-6", "201-289-8")
add_rule("Lilial", "banned", "Annex II", "cosmetic",
         "Banned (added to Annex II by Regulation (EU) 2021/1902, effective "
         "2022).")

add_substance("Isobutylparaben",
              ["isobutyl 4-hydroxybenzoate", "isobutyl p-hydroxybenzoate"],
              "4247-02-3", "224-208-8")
add_rule("Isobutylparaben", "banned", "Annex II", "cosmetic",
         "Banned (Annex II).")

add_substance("Isopropylparaben",
              ["isopropyl 4-hydroxybenzoate", "isopropyl p-hydroxybenzoate"],
              "4191-73-5", "224-069-3")
add_rule("Isopropylparaben", "banned", "Annex II", "cosmetic",
         "Banned (Annex II).")

add_substance("Benzylparaben",
              ["benzyl 4-hydroxybenzoate", "benzyl p-hydroxybenzoate"],
              "94-18-8", "202-311-3")
add_rule("Benzylparaben", "banned", "Annex II", "cosmetic",
         "Banned (Annex II).")

add_substance("Hydroquinone", [], "123-31-9", "204-617-8")
add_rule("Hydroquinone", "banned", "Annex II", "cosmetic",
         "Prohibited in cosmetics (Annex II); also a skin-bleaching agent "
         "subject to separate rules.")

add_substance("1,4-Dioxane", [], "123-91-1", "204-661-8")
add_rule("1,4-Dioxane", "banned", "Annex II", "cosmetic",
         "Prohibited in cosmetics (Annex II); also listed as a CMR / SVHC.")

add_substance("Acrylamide", [], "79-06-1", "201-173-7")
add_rule("Acrylamide", "banned", "Annex II", "cosmetic",
         "Prohibited in cosmetics (Annex II); CMR category 1B.")

# --- Annex III: restricted (cosmetic) ---
add_substance("Formaldehyde", ["formaldehyde", "methanal", "formalin"],
              "50-00-0", "200-001-8")
add_rule("Formaldehyde", "restricted", "Annex III", "cosmetic",
         "Restricted: 0.05% in nail products, 0.1% in oral products; free "
         "formaldehyde limits apply; must carry warning.")

add_substance("Dibutyl phthalate", ["dibutyl phthalate", "dbp"],
              "84-74-2", "201-557-4")
add_rule("Dibutyl phthalate", "restricted", "Annex III", "cosmetic",
         "Restricted use in cosmetics; also a CMR under CLP.")

add_substance("Boric acid", ["boric acid"], "10043-35-3", "233-139-2")
add_rule("Boric acid", "restricted", "Annex III", "cosmetic",
         "Restricted to talc/concentration limits; banned in certain "
         "leave-on products.")

add_substance("BHA", ["bha", "butylated hydroxyanisole", "e320"],
              "25013-16-5", "246-563-8", "E320")
add_rule("BHA", "allowed_with_conditions", "Annex III", "cosmetic",
         "Antioxidant; restricted cap and authorisation limits.")

add_substance("Butylparaben",
              ["butyl 4-hydroxybenzoate", "butyl p-hydroxybenzoate"],
              "94-26-8", "202-318-7")
add_rule("Butylparaben", "restricted", "Annex V", "cosmetic",
         "Preservative; banned in leave-on products for the nappy area of "
         "children under 3.")

add_substance("Benzyl alcohol",
              ["benzyl alcohol", "phenylmethanol"], "100-51-6", "202-859-9")
add_rule("Benzyl alcohol", "restricted", "Annex III", "cosmetic",
         "Fragrance allergen subject to Annex III labelling and concentration "
         "provisions.")

# --- Annex V: preservatives (cosmetic, positive list) ---
for name, aliases, cas, ec, enum, cond in [
    ("Methylparaben", ["methyl 4-hydroxybenzoate", "methyl p-hydroxybenzoate"],
     "99-76-3", "202-785-7", "E218",
     "Preservative; max 0.4% (as acid), 0.8% for ester sum."),
    ("Propylparaben", ["propyl 4-hydroxybenzoate", "propyl p-hydroxybenzoate"],
     "94-13-3", "202-615-1", "E216",
     "Preservative; banned in leave-on products for the nappy area of "
     "children under 3; max 0.4% (as acid), 0.8% ester sum."),
    ("Ethylparaben", ["ethyl 4-hydroxybenzoate", "ethyl p-hydroxybenzoate"],
     "120-47-8", "204-399-4", "E214",
     "Preservative; max 0.4% (as acid), 0.8% ester sum."),
    ("Benzoic acid", ["e210"], "65-85-0", "200-618-2", "E210",
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Sodium benzoate", ["e211"], "532-32-1", "208-534-8", "E211",
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Sorbic acid", ["e200"], "110-44-1", "203-768-7", "E200",
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Potassium sorbate", ["e202"], "24634-61-5", "246-376-1", "E202",
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Salicylic acid", [], "69-72-7", "200-712-3", None,
     "Preservative (Annex V); restricted concentration and use."),
    ("Phenoxyethanol", [], "122-99-6", "204-589-7", None,
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Imidazolidinyl urea", [], "39236-46-9", "254-372-6", None,
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Diazolidinyl urea", [], "78491-02-8", "278-928-2", None,
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
    ("Methylisothiazolinone", ["mit"], "2682-20-4", "220-239-6", None,
     "Preservative (Annex V); restricted concentration (0.01% max)."),
    ("Methylchloroisothiazolinone", ["cmit"], "26172-55-4", "247-500-7", None,
     "Preservative (Annex V); restricted concentration and use."),
    ("Iodopropynyl butylcarbamate", ["ipbc"], "55406-53-6", "259-627-5", None,
     "Preservative (Annex V) subject to maximum concentration and use "
     "conditions."),
]:
    add_substance(name, aliases, cas, ec, enum)
    add_rule(name, "allowed_with_conditions", "Annex V", "cosmetic", cond)

# These four are also food additives (E210/E211/E200/E202) under Regulation
# (EC) No 1333/2008. The same substance legitimately carries BOTH a cosmetic
# rule (above) and a food rule (below); both are retained as separate rules.
for name, enum in [("Benzoic acid", "E210"), ("Sodium benzoate", "E211"),
                   ("Sorbic acid", "E200"), ("Potassium sorbate", "E202")]:
    add_rule(name, "restricted", "Food", "food",
             f"Food additive {enum}; authorised use subject to conditions in "
             "Regulation (EC) No 1333/2008.")

# --- Annex VI: UV filters (cosmetic, positive list) ---
for name, aliases, cas, ec in [
    ("Benzophenone-3", ["oxybenzone", "bp-3"], "131-57-7", "205-031-5"),
    ("Benzophenone-4", ["sulisobenzone", "bp-4"], "4065-45-6", "223-772-2"),
    ("Avobenzone", ["butyl methoxydibenzoylmethane"], "70356-09-1", "274-581-6"),
    ("Octinoxate", ["ethylhexyl methoxycinnamate", "octyl methoxycinnamate"],
     "5466-77-3", "226-775-7"),
    ("Octocrylene", [], "6197-30-4", "228-250-8"),
    ("Homosalate", [], "118-56-9", "204-260-8"),
    ("Octisalate", ["ethylhexyl salicylate"], "118-60-5", "204-263-4"),
    ("Ensulizole", ["phenylbenzimidazole sulfonic acid"], "27503-81-7",
     "248-502-0"),
    ("Padimate O", ["octyl dimethyl paba"], "21245-02-3", "244-289-3"),
]:
    add_substance(name, aliases, cas, ec)
    add_rule(name, "allowed_with_conditions", "Annex VI", "cosmetic",
             "UV filter listed in Annex VI; subject to maximum concentration "
             "and use conditions in Annex VI.")

# --- Annex IV: colourants (cosmetic, positive list) ---
for name, aliases, cas, ec, ci in [
    ("Titanium dioxide", ["titanium dioxide", "titanium(vi) oxide", "e171",
                          "ci 77891"], "13463-67-7", "236-675-5", "CI 77891"),
    ("Iron oxides", ["ci 77491", "ci 77492", "ci 77499", "iron oxide red",
                     "iron oxide yellow", "iron oxide black"],
     "1309-37-1", "215-168-2", "CI 77491"),
    ("Zinc oxide", ["ci 77947"], "1314-13-2", "215-222-5", "CI 77947"),
    ("FD&C Red 3", ["erythrosine", "e127", "ci 45430"], "16423-68-0",
     "240-474-8", "CI 45430"),
]:
    add_substance(name, aliases, cas, ec, ci=ci)
    add_rule(name, "allowed_with_conditions", "Annex IV", "cosmetic",
             "Colourant on the Annex IV positive list; subject to purity and "
             "use conditions.")

add_substance("Resorcinol", [], "108-46-3", "203-585-2")
add_rule("Resorcinol", "restricted", "Annex III", "cosmetic",
         "Colourant and depilatory agent subject to Annex III concentration "
         "limits.")

# --- Food additives (Regulation (EC) No 1333/2008) ---
# These carry a *food* rule distinct from any cosmetic rule. The same name may
# also carry a cosmetic rule above; both are retained as separate rules.
for name, aliases, enum in [
    ("Red 40", ["red 40", "allura red", "allura red ac", "fd&c red 40",
                "e129"], "E129"),
    ("Yellow 5", ["yellow 5", "tartrazine", "fd&c yellow 5", "e102"], "E102"),
    ("Yellow 6", ["yellow 6", "sunset yellow", "fd&c yellow 6", "e110"], "E110"),
    ("Blue 1", ["blue 1", "brilliant blue", "fd&c blue 1", "e133"], "E133"),
    ("BHT", ["bht", "butylated hydroxytoluene", "e321"], "E321"),
    ("Potassium bromate", ["potassium bromate", "e924"], "E924"),
    ("Azodicarbonamide", ["azodicarbonamide", "ada", "e927"], "E927"),
]:
    add_substance(name, aliases)
    add_rule(name, "restricted", "Food", "food",
             f"Food additive {enum} (colour/flour agent); authorised use "
             "subject to conditions in Regulation (EC) No 1333/2008. This is "
             "a food rule, not a cosmetic Annex determination.")

# Additional food colourants.
for name, aliases, enum, cond in [
    ("Carmoisine", ["e122"], "E122",
     "Food colourant E122; authorisation limits apply."),
    ("Ponceau 4R", ["e124", "cochineal red a"], "E124",
     "Food colourant E124; authorisation limits apply."),
    ("Quinoline yellow", ["e104"], "E104",
     "Food colourant E104; authorisation limits apply."),
    ("Cochineal extract", ["carmine", "e120", "natural red 4"], "E120",
     "Food colourant E120; authorisation limits apply."),
]:
    add_substance(name, aliases)
    add_rule(name, "restricted", "Food", "food", cond)

# Erythrosine (= FD&C Red 3, E127) is one substance subject to BOTH a cosmetic
# rule (Annex IV, added above) and a food rule (E127). Consolidate the food
# rule onto the existing substance so aliases e127/erythrosine map to ONE
# identity with both rules retained.
add_rule("FD&C Red 3", "restricted", "Food", "food",
         "Food colourant E127; authorisation limits apply.")

# --- SVHC / CMR (restricted via REACH candidate list) ---
for name, aliases, cond in [
    ("Bisphenol A", ["bpa"],
     "SVHC Candidate List; authorisation/restriction provisions pending "
     "specific use evaluation."),
    ("Triclosan", [],
     "SVHC/restriction; not permitted as a preservative in leave-on products "
     "in certain uses."),
]:
    add_substance(name, aliases)
    add_rule(name, "restricted", "SVHC", "svhc", cond)

# ---------------------------------------------------------------------------
# Identity-only substances (recognised names, NO verified EU determination).
# Presence here is NOT a regulatory claim. determination stays "unknown".
# ---------------------------------------------------------------------------
_identity_only = [
    # Benign carriers / emollients / humectants (no verified EU restriction).
    ("Water", ["aqua", "eau", "agua", "aqua (water)"], "7732-18-5", "231-791-2"),
    ("Glycerin", ["glycerol"], "56-81-5", "200-289-5"),
    ("Propylene glycol", ["propanediol"], "57-55-6", "200-338-0"),
    ("Butylene glycol", [], "107-88-0", "203-529-7"),
    ("Dimethicone", ["silicone oil"], "9006-65-9", None),
    ("Mineral oil", ["paraffinum liquidum", "liquid paraffin"], "8012-95-1",
     "232-384-2"),
    ("Petrolatum", ["petroleum jelly"], "8009-03-8", "232-373-2"),
    ("Cyclopentasiloxane", ["d5"], "541-02-6", "208-764-9"),
    ("Cyclomethicone", [], "69430-24-6", None),
    ("Sodium chloride", ["table salt", "salt"], "7647-14-5", "231-598-3"),
    ("Cetearyl alcohol", [], "8005-44-5", None),
    ("Cetyl alcohol", ["cetanol"], "36653-82-4", "253-149-0"),
    ("Stearyl alcohol", [], "112-92-5", "204-017-6"),
    ("Alcohol denat.", ["denatured alcohol", "sd alcohol"], "64-17-5",
     "200-578-6"),
    ("Natural flavor", ["natural flavour"], None, None),
    ("Sugar", ["sucrose"], "57-50-1", "200-334-9"),
    ("Corn syrup", [], None, None),
    ("Whole grain corn", [], None, None),
    ("Whole grain oat flour", [], None, None),
    # Vitamins / actives (identity only).
    ("Tocopherol", ["vitamin e"], "59-02-9", "200-412-2"),
    ("Tocopheryl acetate", ["vitamin e acetate"], "7695-91-2", "231-710-0"),
    ("Retinol", [], "68-26-8", "200-683-4"),
    ("Retinyl acetate", ["vitamin a acetate"], "127-47-9", "204-844-2"),
    ("Retinyl palmitate", [], "79-81-2", "201-228-8"),
    ("Niacinamide", ["vitamin b3"], "98-92-0", "202-713-4"),
    ("Panthenol", ["pro-vitamin b5", "dexpanthenol"], "81-13-0", "201-327-3"),
    ("Ascorbic acid", ["vitamin c", "e300"], "50-81-7", "200-066-2"),
    ("Citric acid", ["e330"], "77-92-9", "201-069-1"),
    # Common cosmetic ingredients.
    ("Sodium lauryl sulfate", ["sls"], "151-21-3", "205-787-2"),
    ("Sodium laureth sulfate", ["sles", "sodium lauryl ether sulfate"],
     "9004-82-4", None),
    ("Cocamidopropyl betaine", ["capb"], "61789-40-0", "263-058-8"),
    ("Stearic acid", [], "57-11-4", "200-313-4"),
    ("Palmitic acid", [], "57-10-3", "200-312-9"),
    ("Myristic acid", [], "544-63-8", "208-875-2"),
    ("Lauric acid", [], "143-07-7", "205-582-1"),
    ("Linoleic acid", [], "60-33-3", "200-470-9"),
    ("Oleic acid", [], "112-80-1", "204-007-1"),
    ("Caprylic acid", [], "124-07-2", "204-677-5"),
    ("Hyaluronic acid", [], "9067-32-7", None),
    ("Sodium hyaluronate", [], "9067-32-7", None),
    ("Hydrolyzed collagen", [], "9064-67-9", None),
    ("Ceramide NP", ["ceramide 3"], None, None),
    ("Squalane", [], "111-01-3", "203-825-6"),
    ("Squalene", [], "111-02-4", "203-826-1"),
    ("Cholesterol", [], "57-88-5", "200-353-2"),
    ("Lecithin", [], "8002-43-5", "232-307-2"),
    ("Iron", ["ferrum"], "7439-89-6", "231-096-4"),
    ("Zinc", [], "7440-66-6", "231-175-3"),
    ("Sodium bicarbonate", ["baking soda", "e500ii"], "144-55-8", "205-633-8"),
    ("Silicon dioxide", ["silica", "e551"], "7631-86-9", "231-545-4"),
    ("Magnesium sulfate", ["epsom salt", "e518"], "7487-88-9", "231-298-2"),
]
for name, aliases, cas, ec in _identity_only:
    # determination stays "unknown"; no rule is added.
    add_substance(name, aliases, cas, ec)


# ---------------------------------------------------------------------------
# Serialise.
# ---------------------------------------------------------------------------
def build_doc(build_time: Optional[str] = None) -> dict:
    """Build the dataset document (unit-testable, no filesystem side effects).

    `build_time` defaults to now but may be pinned for deterministic tests.
    Returns the document; callers decide where to write it.
    """
    if build_time is None:
        build_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    substances = list(SUBSTANCES.values())
    n_verified = sum(1 for s in substances if s["determination"] != "unknown")
    n_identity_only = len(substances) - n_verified
    meta = {
        "title": "EU cosmetic substance flags -- curated seed (Phase 0)",
        "version": "0.2.0",
        "build_time": build_time,
        # data_updated_at is the artifact build time (curated data). We do NOT
        # claim any upstream source was updated today.
        "data_updated_at": build_time,
        "count_substances": len(substances),
        "count_verified_rules": n_verified,
        "count_identity_only": n_identity_only,
        "sources": SOURCE_NOTE,
        "disclaimer": ("Regulatory information, not legal advice. A 'clean' "
                       "result means no EU ban/restriction was triggered for "
                       "the substances that could be resolved in this curated "
                       "dataset. Absence of a substance, or an identity-only "
                       "substance with determination 'unknown', is NOT "
                       "'clean'."),
    }
    return {"meta": meta, "substances": substances}


def main(output: Optional[str] = None) -> None:
    out = pathlib.Path(output) if output else OUT
    doc = build_doc()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    n = len(doc["substances"])
    n_ver = doc["meta"]["count_verified_rules"]
    n_id = doc["meta"]["count_identity_only"]
    print(f"Wrote {n} substances ({n_ver} verified, {n_id} identity-only) "
          f"-> {out}")


if __name__ == "__main__":
    import sys as _sys
    _argv = _sys.argv[1:]
    _output = None
    if _argv and _argv[0] == "--output":
        _output = _argv[1] if len(_argv) > 1 else None
    main(_output)
