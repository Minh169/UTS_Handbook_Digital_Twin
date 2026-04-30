import json
import os
import re
from neo4j import GraphDatabase
 
 
URI      = "neo4j://127.0.0.1:7687"
USERNAME = "neo4j"
PASSWORD = "hieu1609"
 
FILES = ["dataset/bachelor_ai.json", "dataset/master_ai.json"]
 
 
# ----------------------------------------------------------------
# helpers
# ----------------------------------------------------------------
 
def parse_cp(val):
    """Turn '6cp', 6, '6', '' into an int or None."""
    if val is None or val == "":
        return None
    if isinstance(val, int):
        return val
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else None
 
 
def walk_structure(node, subjects=None, groups=None):
    """
    Recursively collect every subject and group node from the
    nested structure tree produced by uts_scraper.py.
    """
    if subjects is None:
        subjects, groups = [], []
 
    if isinstance(node, list):
        for item in node:
            walk_structure(item, subjects, groups)
        return subjects, groups
 
    if not isinstance(node, dict):
        return subjects, groups
 
    code = str(node.get("code", ""))
 
    if re.match(r"^\d{5}$", code):
        subjects.append(node)
    elif any(code.startswith(p) for p in ["CBK", "MAJ", "SMJ", "STM"]):
        groups.append(node)
 
    # recurse into every list field
    for val in node.values():
        if isinstance(val, list):
            walk_structure(val, subjects, groups)
 
    return subjects, groups
 
 
# ----------------------------------------------------------------
# importer
# ----------------------------------------------------------------
 
class GraphImporter:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
 
    def close(self):
        self.driver.close()
 
    def clear(self):
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
            print("Database cleared.")
 
    def run(self, file_path):
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return
 
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
 
        meta        = data.get("metadata", {})
        prog_name   = meta.get("program", "Unknown")
        prog_id     = meta.get("program_id", "Unknown")
        by_year     = data.get("by_year", {})
 
        print(f"\nImporting: {prog_name} ({prog_id})")
 
        with self.driver.session() as s:
 
            # Course node
            s.run(
                "MERGE (c:Course {code: $code}) SET c.name = $name",
                code=prog_id, name=prog_name,
            )
 
            # ── pass 1: create Subject and Group nodes ──────────────
            for year_str, yr_data in by_year.items():
                year = int(year_str)
                subj_list, grp_list = walk_structure(yr_data)
 
                for subj in subj_list:
                    code = subj.get("code", "")
                    uuid = f"{code}_{year}"
                    s.run(
                        """
                        MERGE (n:Subject {uuid: $uuid})
                        SET n.code        = $code,
                            n.name        = $name,
                            n.year        = $year,
                            n.credit_points = $cp,
                            n.description = $desc,
                            n.faculty     = $faculty,
                            n.study_level = $level,
                            n.result_type = $result,
                            n.workload    = $workload
                        """,
                        uuid=uuid,
                        code=code,
                        name=subj.get("name", ""),
                        year=year,
                        cp=parse_cp(subj.get("credit_points")),
                        desc=subj.get("description", ""),
                        faculty=subj.get("faculty", ""),
                        level=subj.get("study_level", ""),
                        result=subj.get("result_type", ""),
                        workload=subj.get("total_workload_hours", ""),
                    )
                    s.run(
                        """
                        MATCH (c:Course {code: $prog}), (n:Subject {uuid: $uuid})
                        MERGE (c)-[:CONTAINS_SUBJECT]->(n)
                        """,
                        prog=prog_id, uuid=uuid,
                    )
 
                for grp in grp_list:
                    code = grp.get("code", "")
                    uuid = f"{code}_{year}"
                    kind = grp.get("kind", "Choice Block")
                    label = kind.replace(" ", "").replace("-", "").replace("-", "")   # "ChoiceBlock", "Major", "SubMajor", "Stream"
                    s.run(
                        f"""
                        MERGE (g:{label} {{uuid: $uuid}})
                        SET g.code = $code,
                            g.name = $name,
                            g.year = $year,
                            g.kind = $kind,
                            g.credit_points = $cp,
                            g.description   = $desc
                        """,
                        uuid=uuid,
                        code=code,
                        name=grp.get("name", ""),
                        year=year,
                        kind=kind,
                        cp=parse_cp(grp.get("credit_points")),
                        desc=grp.get("description", ""),
                    )
                    s.run(
                        f"""
                        MATCH (c:Course {{code: $prog}}), (g:{label} {{uuid: $uuid}})
                        MERGE (c)-[:CONTAINS_GROUP]->(g)
                        """,
                        prog=prog_id, uuid=uuid,
                    )
 
            # ── pass 2: relationships ────────────────────────────────
            for year_str, yr_data in by_year.items():
                year = int(year_str)
                subj_list, grp_list = walk_structure(yr_data)
 
                for subj in subj_list:
                    code     = subj.get("code", "")
                    uuid     = f"{code}_{year}"
 
                    # REQUIRES (prerequisites)
                    for req in subj.get("prereq_codes", []):
                        req_uuid = f"{req}_{year}"
                        s.run(
                            """
                            MATCH (a:Subject {uuid: $uuid})
                            MERGE (b:Subject {uuid: $req_uuid})
                            ON CREATE SET b.code = $req, b.year = $year, b.name = 'External'
                            MERGE (a)-[:REQUIRES]->(b)
                            """,
                            uuid=uuid, req_uuid=req_uuid, req=req, year=year,
                        )
 
                    # MUTUALLY_EXCLUSIVE (anti-requisites)
                    for anti in subj.get("antireq_codes", []):
                        anti_uuid = f"{anti}_{year}"
                        s.run(
                            """
                            MATCH (a:Subject {uuid: $uuid})
                            MERGE (b:Subject {uuid: $anti_uuid})
                            ON CREATE SET b.code = $anti, b.year = $year, b.name = 'External'
                            MERGE (a)-[:MUTUALLY_EXCLUSIVE]->(b)
                            """,
                            uuid=uuid, anti_uuid=anti_uuid, anti=anti, year=year,
                        )
 
                # CONTAINS (group -> subject)
                for grp in grp_list:
                    grp_code = grp.get("code", "")
                    grp_uuid = f"{grp_code}_{year}"
                    kind     = grp.get("kind", "Choice Block")
                    label    = kind.replace(" ", "").replace("-", "")
 
                    for child_code in grp.get("children_subjects", []):
                        child_uuid = f"{child_code}_{year}"
                        s.run(
                            f"""
                            MATCH (g:{label} {{uuid: $grp_uuid}})
                            MERGE (c:Subject {{uuid: $child_uuid}})
                            ON CREATE SET c.code = $child_code, c.year = $year, c.name = 'External'
                            MERGE (g)-[:CONTAINS]->(c)
                            """,
                            grp_uuid=grp_uuid,
                            child_uuid=child_uuid,
                            child_code=child_code,
                            year=year,
                        )
 
            # ── pass 3: EVOLVED_TO across years ─────────────────────
            all_years = sorted(int(y) for y in by_year.keys())
            for i in range(len(all_years) - 1):
                y_cur  = all_years[i]
                y_next = all_years[i + 1]
                s.run(
                    """
                    MATCH (a:Subject {year: $y_cur}), (b:Subject {year: $y_next})
                    WHERE a.code = b.code
                    MERGE (a)-[:EVOLVED_TO]->(b)
                    """,
                    y_cur=y_cur, y_next=y_next,
                )
 
            print(f"Done: {prog_name} | years {all_years}")
 
 
# ----------------------------------------------------------------
# entry point
# ----------------------------------------------------------------
 
if __name__ == "__main__":
    importer = GraphImporter(URI, USERNAME, PASSWORD)
    try:
        importer.clear()
        for f in FILES:
            importer.run(f)
        print("\nAll data imported successfully.")
        print("Open Neo4j Browser and run: MATCH (n) RETURN n LIMIT 100")
    except Exception as e:
        print(f"Error: {e}")
        print("Check URI, USERNAME, PASSWORD and ensure Neo4j is running.")
    finally:
        importer.close()