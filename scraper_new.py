import os, time, json, re
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
 
 
# ================================================================
# browser factory
# ================================================================
 
def _make_browser():
    svc = Service(ChromeDriverManager().install())
    opts = webdriver.ChromeOptions()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
 
 
# ================================================================
# settings
# ================================================================
 
LEGACY_ROOT   = "https://handbookpre2025.uts.edu.au"
CURRENT_ROOT  = "https://coursehandbook.uts.edu.au"
 
LEGACY_RUNS   = [2023, 2024]
CURRENT_RUNS  = [2025, 2026]
 
PROGRAMS = [
    { "label": "Bachelor of Artificial Intelligence", "id": "C10474", "save_as": "bachelor_ai.json"  },
    { "label": "Master of Artificial Intelligence",   "id": "C04443", "save_as": "master_ai.json"    },
]
 
# global memory store: "YYYY::CODE" -> record dict
_store: dict = {}
 
 
# ================================================================
# memory store helpers
# ================================================================
 
def _key(yr, cd):
    return f"{yr}::{cd}"
 
 
def restore_from_disk(yr, folder):
    fp = os.path.join(folder, f"{yr}_subjects.json")
    if not os.path.exists(fp):
        print(f"   [{yr}] no saved data found, starting clean")
        return
    with open(fp, encoding="utf-8") as fh:
        rows = json.load(fh)
    for cd, rec in rows.items():
        _store[_key(yr, cd)] = rec
    print(f"   [{yr}] restored {len(rows)} records from disk")
 
 
def flush_to_disk(yr, folder):
    tag = f"{yr}::"
    snapshot = { k[len(tag):]: v for k, v in _store.items() if k.startswith(tag) }
    fp = os.path.join(folder, f"{yr}_subjects.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=4, ensure_ascii=False)
    print(f"   [{yr}] wrote {len(snapshot)} records to disk")
 
 
# ================================================================
# legacy handbook helpers
# ================================================================
 
def _url_segment(yr):
    # old site uses "2024_1" for 2024, plain year string for others
    return { 2023: "2023", 2024: "2024_1" }.get(yr, str(yr))
 
 
def _node_kind_legacy(cd):
    if cd.startswith("STM"): return "Stream"
    if cd.startswith("CBK"): return "Choice Block"
    if cd.startswith("MAJ"): return "Major"
    if cd.startswith("SMJ"): return "Sub-Major"
    return "Subject"
 
 
def _pull_req_block(browser, label):
    # finds a requisite table by section heading — works for h3 and bold-text variants
    pg = BeautifulSoup(browser.page_source, "html.parser")
    tbl = None
    h = pg.find("h3", string=lambda t: t and label in t)
    if h:
        tbl = h.find_next("table")
    if not tbl:
        bold = pg.find("strong", string=lambda t: t and label in t)
        if bold:
            tbl = bold.find_parent("table")
    if not tbl:
        return None
 
    out = { "rule": "", "items": [] }
    span_cell = tbl.find("td", colspan=True)
    if span_cell:
        out["rule"] = span_cell.get_text(strip=True)
 
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td")
        if span_cell in tds or tr.find("th"):
            continue
        if len(tds) >= 2 and not tr.find("td", colspan=True):
            row = { "item_id": tds[0].get_text(strip=True), "details": tds[-1].get_text(strip=True) }
            if len(tds) == 3:
                row["type"] = tds[1].get_text(strip=True)
            out["items"].append(row)
        elif len(tds) == 1 or tr.find("td", colspan=True):
            txt = tr.get_text(strip=True)
            if txt and txt.lower() != label.lower():
                out["items"].append({ "note": txt })
 
    return out if (out["items"] or out["rule"]) else None
 
 
# ================================================================
# legacy handbook — subject detail
# ================================================================
 
def _fetch_subject_legacy(browser, pg_url, cd, nm, pts, yr):
    print(f"      subject  {cd}  {pg_url}")
    browser.get(pg_url)
    time.sleep(2)
    pg = BeautifulSoup(browser.page_source, "html.parser")
 
    rec = {
        "code": cd, "name": nm, "credit_points": pts,
        "kind": "Subject", "url": pg_url, "year": yr,
        "faculty": None, "study_level": None, "result_type": None,
        "total_workload_hours": None, "learning_outcomes": None, "learning_and_teaching_activities": None,
        "description": None, "prereq_raw": None, "prereq_codes": [],
        "antireq_raw": None, "antireq_codes": [],
    }
 
    body = pg.find("div", id="content")
    if not body:
        return rec
 
    # credit points fallback — if not passed in from the table, parse from page
    # old handbook shows it as "Credit points: 6" in an <em> or plain text
    if not rec["credit_points"]:
        page_text = pg.get_text()
        cp_match = (
            re.search(r"Credit points?:\s*(\d+)", page_text, re.I) or
            re.search(r"(\d+)\s*credit points?", page_text, re.I) or
            re.search(r"(\d+)cp", page_text, re.I)
        )
        if cp_match:
            rec["credit_points"] = int(cp_match.group(1))
 
    # workload sits in <em> tags, stops before any requisite mention
    for em in body.find_all("em")[1:]:
        txt = em.get_text(strip=True).lower()
        if any(w in txt for w in ["hpw", "weeks", "attendance", "tutorial", "lecture", "block"]):
            rec["total_workload_hours"] = em.get_text(strip=True)
            break
        if "requisite" in txt:
            break
 
    # access conditions link leads to the requisite detail page
    cond_link = None
    h4 = body.find("h4")
    if h4:
        cond_link = h4.find("a", href=lambda h: h and "subjectcode=" in h)
    if not cond_link:
        cond_link = body.find("a", string=lambda t: t and "Access conditions" in t)
 
    if cond_link:
        browser.get(cond_link["href"])
        time.sleep(2)
        req_data = {}
        for section, tag in [("anti_requisite", "Anti-requisite(s)"),
                              ("requisite",      "Requisite(s)"),
                              ("other_requisite","Other requisite")]:
            block = _pull_req_block(browser, tag)
            if block:
                req_data[section] = block
        if req_data:
            rec["requisite_list"] = req_data
        all_codes = re.findall(r"\b\d{5}\b", json.dumps(req_data))
        rec["prereq_codes"] = list(set(all_codes))
        browser.back()
        time.sleep(1)
        pg = BeautifulSoup(browser.page_source, "html.parser")
        body = pg.find("div", id="content")
 
    if not body:
        return rec
 
    # detailed page has description, outcomes, teaching strategies, faculty
    detail_a = body.find("a", string=lambda t: t and "Detailed subject description" in t)
    if detail_a:
        browser.get(urljoin(pg_url, detail_a["href"]))
        time.sleep(2)
        detail = BeautifulSoup(browser.page_source, "html.parser")
 
        fac = detail.find("em", string=re.compile(r"^UTS:", re.I))
        if fac:
            rec["faculty"] = fac.get_text(strip=True)
 
        desc_h = detail.find("h3", string=lambda t: t and "Description" in t)
        if desc_h:
            bits, node = [], desc_h.find_next()
            while node and node.name != "h3":
                if node.name == "p":
                    bits.append(node.get_text(strip=True))
                node = node.find_next_sibling()
            if bits:
                rec["description"] = " ".join(bits)
 
        slo = detail.find("table", class_="SLOTable")
        if slo:
            vals = [td.get_text(strip=True) for td in slo.find_all("td") if td.get_text(strip=True)]
            if vals:
                rec["learning_outcomes"] = vals
 
        teach_h = detail.find("h3", string=lambda t: t and "Teaching and learning strategies" in t)
        if teach_h:
            bits, node = [], teach_h.find_next()
            while node and node.name != "h3":
                if node.name == "p":
                    bits.append(node.get_text(strip=True))
                node = node.find_next_sibling()
            if bits:
                rec["learning_and_teaching_activities"] = "\n".join(bits)
 
        for em in detail.find_all("em"):
            if "result type" in em.get_text(strip=True).lower() and em.next_sibling:
                rec["result_type"] = em.next_sibling.strip(": ")
 
        lvl = detail.find("p", string=lambda t: t and "Subject level" in t)
        if lvl:
            nxt = lvl.find_next("p")
            if nxt:
                rec["study_level"] = nxt.get_text(strip=True)
 
    return rec
 
 
# ================================================================
# legacy handbook — area of study
# ================================================================
 
def _fetch_group_legacy(browser, pg_url, cd, nm, pts, yr):
    prev = browser.current_url
    print(f"      group    {cd}  {pg_url}")
    browser.get(pg_url)
    time.sleep(2)
    pg = BeautifulSoup(browser.page_source, "html.parser")
 
    rec = {
        "code": cd, "name": nm, "credit_points": pts,
        "kind": _node_kind_legacy(cd), "url": pg_url, "year": yr,
        "description": None, "contents": [],
    }
 
    body = pg.find("div", id="content") or pg.find("div", id="full-container")
    if body:
        # grab paragraphs from the main content area, skipping anything inside tables
        all_paras = body.find_all("p")
        table = body.find("table")
        paras = [
            p.get_text(strip=True) for p in all_paras
            if p.get_text(strip=True) and (not table or not p.find_parent("table"))
        ]
        if paras:
            rec["description"] = "\n\n".join(paras)
 
        tree_node = _build_tree_legacy(browser, pg_url, cd, nm, pts, yr)
        has_items = "has_subject" in tree_node or "has_area_of_study" in tree_node
        has_nested = "have_sub_structures" in tree_node
 
        if has_nested and not has_items:
            if tree_node.get("structure_cp") and not rec["credit_points"]:
                rec["credit_points"] = tree_node["structure_cp"]
            if tree_node.get("structure_details"):
                note = tree_node["structure_details"]
                rec["description"] = note if not rec["description"] else rec["description"] + f"\n\nRule: {note}"
            rec["contents"].extend(tree_node["have_sub_structures"])
        elif tree_node:
            rec["contents"].append(tree_node)
 
    browser.get(prev)
    time.sleep(1)
    return rec
 
 
# ================================================================
# legacy handbook — recursive tree builder
# ================================================================
 
def _build_tree_legacy(browser, pg_url, cd, nm, pts, yr):
    print(f"      tree     {cd}  {pg_url}")
    browser.get(pg_url)
    time.sleep(2)
    pg = BeautifulSoup(browser.page_source, "html.parser")
 
    node = { "structure_name": nm, "structure_code": cd, "structure_cp": pts, "structure_details": "" }
 
    body = pg.find("div", id="content")
    tbl  = body.find("table") if body else None
    if not tbl:
        return node
 
    rows   = tbl.find_all("tr")
    groups = []
    active = { "name": "Compulsory", "cp": "", "rows": [] }
 
    for idx, tr in enumerate(rows):
        tds = tr.find_all("td")
        if not tds:
            continue
        txt  = tr.get_text(strip=True)
        link = tr.find("a")
 
        is_selector = "select" in txt.lower() and "credit points" in txt.lower() and not link
        if is_selector:
            rule_pts = tds[-1].get_text(strip=True) if len(tds) > 1 else ""
            if idx == 0:
                node["structure_details"] = txt
                if rule_pts:
                    node["structure_cp"] = rule_pts
                active["name"] = txt
                active["cp"]   = rule_pts
            else:
                if active["rows"]:
                    groups.append(active)
                active = { "name": txt, "cp": rule_pts, "rows": [] }
            continue
 
        if "total" in txt.lower() and len(tds) < 3:
            continue
 
        if link:
            el_cd   = link.get_text(strip=True)
            el_nm   = tds[0].get_text(strip=True).replace(el_cd, "").strip()
            el_url  = urljoin(pg_url, link["href"])
            el_kind = _node_kind_legacy(el_cd)
            el_pts  = tds[-1].get_text(strip=True) if len(tds) > 1 else ""
 
            ck = _key(yr, el_cd)
            if ck in _store:
                print(f"         cached   {el_cd}")
                el_rec = _store[ck].copy()
            else:
                if el_kind == "Subject":
                    el_rec = _fetch_subject_legacy(browser, el_url, el_cd, el_nm, el_pts, yr)
                elif el_kind == "Stream":
                    el_rec = _build_tree_legacy(browser, el_url, el_cd, el_nm, el_pts, yr)
                    el_rec["kind"] = "Stream"
                else:
                    el_rec = _fetch_group_legacy(browser, el_url, el_cd, el_nm, el_pts, yr)
                _store[ck] = el_rec
 
            active["rows"].append(el_rec)
 
    if active["rows"]:
        groups.append(active)
 
    # assemble groups into the node
    if len(groups) == 1:
        grp      = groups[0]
        subjects = [r for r in grp["rows"] if r.get("kind") == "Subject"]
        areas    = [r for r in grp["rows"] if r.get("kind") in ("Choice Block", "Major", "Sub-Major")]
        streams  = [r for r in grp["rows"] if r.get("kind") == "Stream"]
        area_kinds = set(r.get("kind") for r in areas)
 
        if grp["name"] == "Compulsory" and len(area_kinds) > 1:
            node["have_sub_structures"] = []
            if subjects:
                node["have_sub_structures"].append({ "structure_name": "Core Subjects", "structure_cp": "", "has_subject": subjects })
            for k in sorted(area_kinds):
                node["have_sub_structures"].append({ "structure_name": f"{k}s", "structure_cp": "", "has_area_of_study": [r for r in areas if r.get("kind") == k] })
        else:
            if subjects: node["has_subject"]      = subjects
            if areas:    node["has_area_of_study"] = areas
 
        for s in streams:
            s.pop("kind", None)
            node.setdefault("have_sub_structures", []).append(s)
    else:
        node["have_sub_structures"] = []
        for grp in groups:
            sub      = { "structure_name": grp["name"], "structure_cp": grp["cp"] }
            subjects = [r for r in grp["rows"] if r.get("kind") == "Subject"]
            areas    = [r for r in grp["rows"] if r.get("kind") in ("Choice Block", "Major", "Sub-Major")]
            streams  = [r for r in grp["rows"] if r.get("kind") == "Stream"]
            if subjects: sub["has_subject"]      = subjects
            if areas:    sub["has_area_of_study"] = areas
            for s in streams:
                s.pop("kind", None)
                sub.setdefault("have_sub_structures", []).append(s)
            node["have_sub_structures"].append(sub)
 
    return node
 
 
# ================================================================
# legacy handbook — top-level course scraper
# ================================================================
 
def run_legacy(browser, yr, prog_id):
    seg = _url_segment(yr)
    pg_url = f"{LEGACY_ROOT}/{seg}/courses/{prog_id.lower()}.html"
    print(f"   loading  {pg_url}")
 
    browser.get(pg_url)
    time.sleep(2)
    pg = BeautifulSoup(browser.page_source, "html.parser")
 
    h1 = pg.find("h1")
    if h1:
        raw = h1.get_text(strip=True).split(" ", 1)
        prog_cd = re.sub(r"v\d+$", "", raw[0], flags=re.IGNORECASE)
        prog_nm = raw[1] if len(raw) > 1 else "Unknown"
    else:
        prog_cd, prog_nm = prog_id, "Unknown"
 
    overview_h = pg.find("h2", string=lambda t: t and "Overview" in t)
    overview   = None
    if overview_h:
        bits, node = [], overview_h.find_next_sibling()
        while node and node.name == "p":
            bits.append(node.get_text(strip=True))
            node = node.find_next_sibling()
        if bits:
            overview = " ".join(bits)
 
    cilo_h   = pg.find("h2", string=lambda t: t and "Course intended learning outcomes" in t)
    outcomes = []
    if cilo_h:
        cilo_tbl = cilo_h.find_next("table")
        if cilo_tbl:
            for tr in cilo_tbl.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 2:
                    outcomes.append(tds[1].get_text(strip=True))
 
    result = {
        "course_code": prog_cd,
        "course_name": prog_nm,
        "course_details": overview,
        "course_learning_outcomes": outcomes or None,
        "course_url": pg_url,
        "year": yr,
        "structure": [],
    }
 
    req_h = pg.find("h2", string=lambda t: t and "Course completion requirements" in t)
    if req_h:
        req_tbl = req_h.find_next("table")
        if req_tbl:
            for tr in req_tbl.find_all("tr"):
                a    = tr.find("a")
                tds  = tr.find_all("td")
                if not a or "total" in tr.get_text(strip=True).lower():
                    continue
                r_cd  = a.get_text(strip=True)
                r_nm  = tds[0].get_text(strip=True).replace(r_cd, "").strip()
                r_pts = tds[-1].get_text(strip=True) if len(tds) > 1 else ""
                r_url = urljoin(pg_url, a["href"])
                result["structure"].append(_build_tree_legacy(browser, r_url, r_cd, r_nm, r_pts, yr))
 
    return result
 
 
# ================================================================
# current handbook helpers
# ================================================================
 
def _node_kind_current(cd, href=""):
    if not cd:
        return "unknown"
    if re.match(r"^\d", cd):
        return "subject"
    if "/aos/" in href or "/subjectgroups/" in href:
        mapping = { "SMJ": "sub_major", "MAJ": "major", "CBK": "choice_block", "STM": "stream" }
        for prefix, kind in mapping.items():
            if cd.startswith(prefix):
                return kind
        return "academic_structure"
    return "unknown"
 
 
def _get_outcomes_current(pg):
    box = pg.find("div", {"data-menu-id": "Learningoutcomes"})
    if not box:
        return None
    hits = []
    for item in box.find_all("div", class_=lambda c: c and "AccordionItem" in c):
        clamp = item.find("div", class_=lambda c: c and "clamp" in c)
        if clamp:
            t = clamp.get_text(strip=True)
            if t:
                hits.append(t)
    return hits or None
 
 
def _get_attributes_current(pg):
    labels = { "faculty": "faculty", "study level": "study_level", "result type": "result_type", "total workload hours": "total_workload_hours" }
    out = { v: None for v in labels.values() }
    block = pg.find("div", {"data-testid": "attributes-table"})
    if block:
        for box in block.find_all("div", class_=lambda c: c and "AttrContainer" in c):
            h = box.find("h3")
            if not h:
                continue
            lbl = h.get_text(strip=True).lower()
            if lbl in labels:
                val = box.find("div", {"data-testid": "AttrBody"})
                if val:
                    out[labels[lbl]] = val.get_text(strip=True)
    return out
 
 
def _get_req_block_current(browser, label):
    pg = BeautifulSoup(browser.page_source, "html.parser")
    tbl = None
    h = pg.find("h3", string=lambda t: t and label in t)
    if h:
        tbl = h.find_next("table")
    if not tbl:
        bold = pg.find("strong", string=lambda t: t and label in t)
        if bold:
            tbl = bold.find_parent("table")
    if not tbl:
        return None
 
    out = { "rule": "", "items": [] }
    span = tbl.find("td", colspan=True)
    if span:
        out["rule"] = span.get_text(strip=True)
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td")
        if span in tds or tr.find("th"):
            continue
        if len(tds) >= 2 and not tr.find("td", colspan=True):
            row = { "item_id": tds[0].get_text(strip=True), "details": tds[-1].get_text(strip=True) }
            if len(tds) == 3:
                row["type"] = tds[1].get_text(strip=True)
            out["items"].append(row)
        elif len(tds) == 1 or tr.find("td", colspan=True):
            t = tr.get_text(strip=True)
            if t and t.lower() != label.lower():
                out["items"].append({ "note": t })
    return out if (out["items"] or out["rule"]) else None
 
 
# ================================================================
# current handbook — subject detail
# ================================================================
 
def _fetch_subject_current(browser, pg_url, yr):
    print(f"         subject  {pg_url}")
    browser.get(pg_url)
    time.sleep(3)
 
    for btn_txt in ["Read More", "Expand all"]:
        for btn in browser.find_elements(By.XPATH, f"//button[contains(text(),'{btn_txt}')]"):
            try:
                browser.execute_script("arguments[0].click();", btn)
                time.sleep(0.3)
            except Exception:
                pass
 
    pg  = BeautifulSoup(browser.page_source, "html.parser")
    rec = _get_attributes_current(pg)
    rec["learning_outcomes"] = _get_outcomes_current(pg)
    rec["year"]     = yr
 
    act_box = pg.find("div", {"data-menu-id": "Learningandteachingactivities"})
    if act_box:
        inner = act_box.find("div", class_="readmore-content-wrapper")
        rec["learning_and_teaching_activities"] = inner.get_text(strip=True) if inner else None
    else:
        rec["learning_and_teaching_activities"] = None
 
    desc_box = pg.find("div", {"data-menu-id": "Subjectdescription"})
    if desc_box:
        inner = desc_box.find("div", class_="readmore-content-wrapper")
        rec["description"] = inner.get_text(strip=True) if inner else None
    else:
        rec["description"] = None
 
    req_data = {}
    req_section = pg.find("div", id="Requisites")
    if req_section:
        req_a = req_section.find("a", href=lambda h: h and "subjectcode=" in h)
        if req_a:
            browser.get(req_a["href"])
            time.sleep(2)
            for section, tag in [("anti_requisite","Anti-requisite(s)"),
                                  ("requisite",     "Requisite(s)"),
                                  ("other_requisite","Other requisite")]:
                blk = _get_req_block_current(browser, tag)
                if blk:
                    req_data[section] = blk
            browser.back()
            time.sleep(1)
 
    if req_data:
        rec["requisite_list"] = req_data
    rec["prereq_codes"] = list(set(re.findall(r"\b\d{5}\b", json.dumps(req_data))))
 
    return rec
 
 
# ================================================================
# current handbook — area of study
# ================================================================
 
def _fetch_group_current(browser, pg_url, yr):
    print(f"         group    {pg_url}")
    prev = browser.current_url
    browser.get(pg_url)
    time.sleep(3)
    pg = BeautifulSoup(browser.page_source, "html.parser")
 
    rec = { "description": None, "contents": [], "year": yr }
    desc = pg.find("div", class_="readmore-content-wrapper")
    if desc:
        rec["description"] = desc.get_text(strip=True)
 
    struct_sec = pg.find("div", {"data-menu-title": "Structure"})
    if struct_sec:
        top = [d for d in struct_sec.find_all("div", class_=lambda c: c and "AccordionItem" in c)
               if not d.find_parent("div", class_=lambda c: c and "AccordionItem" in c)]
        for acc in top:
            rec["contents"].append(_build_tree_current(browser, acc, yr))
 
    browser.get(prev)
    time.sleep(1)
    return rec
 
 
# ================================================================
# current handbook — recursive tree builder
# ================================================================
 
def _build_tree_current(browser, acc_node, yr):
    h_tag = acc_node.find(["strong", "h4"], class_=lambda c: c and ("SAlternateHeading" in c or "SDefaultHeading" in c))
    sec_nm  = h_tag.get_text(strip=True) if h_tag else "Untitled Section"
 
    cp_tag  = acc_node.find("strong", class_=lambda c: c and "SAlternateSubheading" in c)
    sec_pts = cp_tag.get_text(strip=True) if cp_tag else None
 
    d_tag   = acc_node.find("div", class_=lambda c: c and "SAccordionDescription" in c)
    sec_dsc = d_tag.get_text(strip=True) if d_tag else None
 
    node = { "structure_name": sec_nm, "structure_cp": sec_pts, "structure_details": sec_dsc }
 
    plate = acc_node.find("div", class_=lambda c: c and "SAccordionContentContainer" in c)
    if not plate:
        return node
 
    for child in plate.find_all("div", recursive=False):
        cls = str(child.get("class", []))
 
        if "Links--StyledLinkGroup" in cls:
            for a in child.find_all("a", class_="cs-list-item"):
                cd_div = a.find("div", class_="section1")
                el_cd  = cd_div.get_text(strip=True) if cd_div else ""
                el_url = urljoin(CURRENT_ROOT, a["href"])
                el_kind = _node_kind_current(el_cd, el_url)
                bucket  = "have_subject" if el_kind == "subject" else "have_area_of_study"
 
                ck = _key(yr, el_cd)
                if ck in _store:
                    print(f"         cached   {el_cd}")
                    el_rec = _store[ck]
                else:
                    nm_div  = a.find("div", class_="unit-title")
                    pts_div = a.find("div", class_="section2")
                    el_nm   = nm_div.get_text(strip=True)  if nm_div  else ""
                    el_pts  = pts_div.get_text(strip=True) if pts_div else ""
 
                    if el_kind == "subject":
                        details = _fetch_subject_current(browser, el_url, yr)
                    else:
                        details = _fetch_group_current(browser, el_url, yr)
 
                    el_rec = { "code": el_cd, "name": el_nm, "credit_points": el_pts, "kind": el_kind, "url": el_url, **details }
                    _store[ck] = el_rec
 
                node.setdefault(bucket, []).append(el_rec)
 
        elif "AccordionItem" in cls:
            child_node = _build_tree_current(browser, child, yr)
            if child_node:
                node.setdefault("have_sub_structures", []).append(child_node)
 
    return node
 
 
# ================================================================
# current handbook — top-level course scraper
# ================================================================
 
def run_current(browser, yr, prog_id):
    pg_url = f"{CURRENT_ROOT}/course/{yr}/{prog_id}"
    print(f"   loading  {pg_url}")
 
    browser.get(pg_url)
    time.sleep(5)
 
    for attempt in [("//*[contains(text(),'Structure')]", 2), ("//*[contains(text(),'Expand')]", 0.5)]:
        try:
            for el in browser.find_elements(By.XPATH, attempt[0]):
                browser.execute_script("arguments[0].click();", el)
                time.sleep(attempt[1])
                if "Structure" in attempt[0]:
                    break
        except Exception:
            pass
 
    time.sleep(2)
    pg = BeautifulSoup(browser.page_source, "html.parser")
 
    title = pg.find("h2", {"data-testid": "ai-header"})
    if title:
        parts   = title.get_text().split("-", 1)
        prog_cd = parts[0].strip()
        prog_nm = parts[1].strip() if len(parts) > 1 else ""
    else:
        prog_cd, prog_nm = prog_id, ""
 
    desc_wrap = pg.find("div", class_="readmore-content-wrapper")
    overview  = desc_wrap.get_text(strip=True) if desc_wrap else None
 
    result = {
        "course_code": prog_cd, "course_name": prog_nm,
        "course_details": overview, "course_learning_outcomes": _get_outcomes_current(pg),
        "course_url": pg_url, "year": yr, "structure": [],
    }
 
    struct_sec = pg.find("div", {"data-menu-title": "Structure"})
    if struct_sec:
        top = [d for d in struct_sec.find_all("div", class_=lambda c: c and "AccordionItem" in c)
               if not d.find_parent("div", class_=lambda c: c and "AccordionItem" in c)]
        for acc in top:
            result["structure"].append(_build_tree_current(browser, acc, yr))
            browser.get(pg_url)
            time.sleep(2)
 
    return result
 
 
# ================================================================
# entry point
# ================================================================
 
if __name__ == "__main__":
    store_dir = os.path.join("dataset", "subjects_archive")
    os.makedirs(store_dir, exist_ok=True)
    os.makedirs("dataset", exist_ok=True)
 
    browser = _make_browser()
 
    try:
        for prog in PROGRAMS:
            prog_label = prog["label"]
            prog_id    = prog["id"]
            out_path   = os.path.join("dataset", prog["save_as"])
 
            print(f"\n{'#'*60}\n  {prog_label} ({prog_id})\n{'#'*60}")
 
            payload = {
                "metadata": {
                    "program": prog_label,
                    "program_id": prog_id,
                    "years": LEGACY_RUNS + CURRENT_RUNS,
                    "source": "UTS Handbook",
                },
                "by_year": {},
            }
 
            for yr in LEGACY_RUNS:
                print(f"\n{'='*50}\n  {yr}  (legacy)\n{'='*50}")
                restore_from_disk(yr, store_dir)
                for attempt in range(3):
                    try:
                        payload["by_year"][str(yr)] = run_legacy(browser, yr, prog_id)
                        break
                    except Exception as e:
                        print(f"   browser crashed ({e}), restarting... attempt {attempt+1}/3")
                        try:
                            browser.quit()
                        except Exception:
                            pass
                        browser = _make_browser()
                        if attempt == 2:
                            print(f"   failed after 3 attempts, skipping {yr}")
                flush_to_disk(yr, store_dir)
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=4, ensure_ascii=False)
                print(f"   saved {yr} -> {out_path}")
 
            for yr in CURRENT_RUNS:
                print(f"\n{'='*50}\n  {yr}  (current)\n{'='*50}")
                restore_from_disk(yr, store_dir)
                for attempt in range(3):
                    try:
                        payload["by_year"][str(yr)] = run_current(browser, yr, prog_id)
                        break
                    except Exception as e:
                        print(f"   browser crashed ({e}), restarting... attempt {attempt+1}/3")
                        try:
                            browser.quit()
                        except Exception:
                            pass
                        browser = _make_browser()
                        if attempt == 2:
                            print(f"   failed after 3 attempts, skipping {yr}")
                flush_to_disk(yr, store_dir)
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=4, ensure_ascii=False)
                print(f"   saved {yr} -> {out_path}")
 
            print(f"\n  finished  {prog_label}")
 
    finally:
        browser.quit()
        print("\nbrowser closed")