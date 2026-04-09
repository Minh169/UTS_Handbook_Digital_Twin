import json
import os
from neo4j import GraphDatabase


URI = "neo4j://127.0.0.1:7687"
USERNAME = "neo4j"
PASSWORD = "hieu1609" 

FILES_TO_IMPORT = ["bachelor_ai.json", "master_ai.json"]

class UTSGraphImporter:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=None)

    def close(self):
        self.driver.close()

    def clear_database(self):
        """Clears all nodes and relationships to start fresh."""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("Database cleared. Starting with a fresh graph")

    def ingest_data(self, file_path):
        if not os.path.exists(file_path):
            print(f" File {file_path} not found. Skipping...")
            return

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        course_info = data.get("metadata", {})
        course_name = course_info.get("course", "Unknown")
        course_code = course_info.get("course_code", "Unknown")
        
        years_data = data.get("data_by_year", {})

        with self.driver.session() as session:
            session.run(
                """
                MERGE (c:Course {code: $code})
                SET c.name = $name
                """,
                code=course_code, name=course_name
            )
            print(f"Importing Course: {course_name} ({course_code})")

            for year, y_data in years_data.items():
                year_int = int(year)
                subjects = y_data.get("subjects", [])
                sub_majors = y_data.get("sub_majors", [])


                for subj in subjects:
                    uuid = f"{subj['subject_code']}_{year_int}"
                    session.run(
                        """
                        MERGE (s:Subject {uuid: $uuid})
                        SET s.code = $code,
                            s.name = $name,
                            s.credit_points = $credit_points,
                            s.description = $description,
                            s.type = $type,
                            s.year = $year
                        """,
                        uuid=uuid,
                        code=subj['subject_code'],
                        name=subj['name'],
                        credit_points=subj.get('credit_points', 0),
                        description=subj.get('description', ''),
                        type=subj.get('type', 'subject'),
                        year=year_int
                    )
                    
                    
                    session.run(
                        """
                        MATCH (c:Course {code: $course_code})
                        MATCH (s:Subject {uuid: $uuid})
                        MERGE (c)-[:CONTAINS_SUBJECT]->(s)
                        """,
                        course_code=course_code, uuid=uuid
                    )

                for sm in sub_majors:
                    sm_code = sm.get('code', sm.get('subject_code'))
                    sm_title = sm.get('name', sm.get('title', sm_code))
                    sm_uuid = f"{sm_code}_{year_int}"
                    session.run(
                        """
                        MERGE (cb:ChoiceBlock {uuid: $uuid})
                        SET cb.code = $code,
                            cb.name = $title,
                            cb.year = $year,
                            cb.type = $type
                        """,
                        uuid=sm_uuid,
                        code=sm_code,
                        title=sm_title,
                        year=year_int,
                        type=sm.get('type', 'sub_major')
                    )

                    session.run(
                        """
                        MATCH (c:Course {code: $course_code})
                        MATCH (cb:ChoiceBlock {uuid: $uuid})
                        MERGE (c)-[:CONTAINS_CHOICEBLOCK]->(cb)
                        """,
                        course_code=course_code, uuid=sm_uuid
                    )

            for year, y_data in years_data.items():
                year_int = int(year)
                subjects = y_data.get("subjects", [])
                sub_majors = y_data.get("sub_majors", [])

                for subj in subjects:
                    subj_uuid = f"{subj['subject_code']}_{year_int}"
                    
                    # PREREQUISITES
                    for req_code in subj.get("prereq_codes", []):
                        req_uuid = f"{req_code}_{year_int}"
                        session.run(
                            """
                            MATCH (s:Subject {uuid: $subj_uuid})
                            MERGE (req:Subject {uuid: $req_uuid})
                            ON CREATE SET req.code = $req_code, req.year = $year, req.name = 'External Subject'
                            MERGE (s)-[:REQUIRES]->(req)
                            """,
                            subj_uuid=subj_uuid, req_uuid=req_uuid, req_code=req_code, year=year_int
                        )
                    
                    # ANTIREQUISITES
                    for anti_code in subj.get("antireq_codes", []):
                        anti_uuid = f"{anti_code}_{year_int}"
                        session.run(
                            """
                            MATCH (s:Subject {uuid: $subj_uuid})
                            MERGE (a:Subject {uuid: $anti_uuid})
                            ON CREATE SET a.code = $anti_code, a.year = $year, a.name = 'External Subject'
                            MERGE (s)-[:MUTUALLY_EXCLUSIVE]->(a)
                            """,
                            subj_uuid=subj_uuid, anti_uuid=anti_uuid, anti_code=anti_code, year=year_int
                        )

                # CHOICEBLOCK CONTAINS SUBJECTS
                for sm in sub_majors:
                    sm_code = sm.get('code', sm.get('subject_code'))
                    sm_uuid = f"{sm_code}_{year_int}"
                    for child_code in sm.get("children_subjects", []):
                        child_uuid = f"{child_code}_{year_int}"
                        session.run(
                            """
                            MATCH (cb:ChoiceBlock {uuid: $cb_uuid})
                            MERGE (child:Subject {uuid: $child_uuid})
                            ON CREATE SET child.code = $child_code, child.year = $year, child.name = 'External Subject'
                            MERGE (cb)-[:CONTAINS]->(child)
                            """,
                            cb_uuid=sm_uuid, child_uuid=child_uuid, child_code=child_code, year=year_int
                        )

        
            all_years = sorted([int(y) for y in years_data.keys()])
            for i in range(len(all_years) - 1):
                y_current = all_years[i]
                y_next = all_years[i+1]
                
            
                session.run(
                    """
                    MATCH (s1:Subject {year: $y_current})
                    MATCH (s2:Subject {year: $y_next, code: s1.code})
                    MERGE (s1)-[:EVOLVED_TO]->(s2)
                    """,
                    y_current=y_current, y_next=y_next
                )
            
            print(f"Import completed for: {course_name} (Years: {all_years}")

if __name__ == "__main__":
    importer = UTSGraphImporter(URI, USERNAME, PASSWORD)
    try:
        importer.clear_database()
        for f in FILES_TO_IMPORT:
            importer.ingest_data(f)
        print("\DATA IMPORTED SUCCESSFULLY TO NEO4J!")
        print("Tip: Open Neo4j Desktop and run: MATCH (n) RETURN n LIMIT 100")
    except Exception as e:
        print(f"Error connecting to Neo4j: {e}")
        print("-> Please check your URI, USERNAME, PASSWORD and ensure Neo4j Desktop is 'Active/Started'.")
    finally:
        importer.close()
