import time
import json
import re
import requests
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup


BASE_NEW = "https://coursehandbook.uts.edu.au"
BASE_OLD = "https://handbookpre2025.uts.edu.au"
YEARS_OLD = [2023, 2024]
YEARS_NEW = [2025, 2026]

COURSES = [
    {
        "name": "Bachelor of Artificial Intelligence",
        "code": "C10474",
        "output": "bachelor_ai.json"
    },
    {
        "name": "Master of Artificial Intelligence",
        "code": "C04443",
        "output": "master_ai.json"
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def classify_type(subject_code, subject_url):
    if "/subject/" in subject_url:
        return "subject"
    elif "/aos/" in subject_url:
        if subject_code.startswith("SMJ"):
            return "sub_major"
        elif subject_code.startswith("MAJ"):
            return "major"
        elif subject_code.startswith("CBK"):
            return "choice_block"
        elif subject_code.startswith("STM"):
            return "stream"
        else:
            return "academic_structure"
    else:
        return "unknown"


def extract_section_data(driver, header_text):
    soup = BeautifulSoup(driver.page_source, "html.parser")
    header = soup.find("h3", string=lambda t: t and header_text in t)
    if not header:
        return None

    table = header.find_next("table")
    if not table:
        return None

    section_data = {"rule": "", "items": []}
    rule_row = table.find("td", colspan=True)
    if rule_row:
        section_data["rule"] = rule_row.get_text(strip=True)

    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 2 and not row.find("td", colspan=True):
            item_entry = {
                "item_id": cells[0].get_text(strip=True),
                "details": cells[-1].get_text(strip=True),
            }
            if len(cells) == 3:
                item_entry["type"] = cells[1].get_text(strip=True)
            section_data["items"].append(item_entry)

    return section_data


def scrape_new(driver, year, course_code):
    course_url = f"{BASE_NEW}/course/{year}/{course_code}"
    print(f"[NEW] Scraping {year}: {course_url}")

    driver.get(course_url)
    time.sleep(5)

    # Click Structure tab
    try:
        tabs = driver.find_elements(By.XPATH, "//*[contains(text(),'Structure')]")
        for t in tabs:
            driver.execute_script("arguments[0].click();", t)
            time.sleep(2)
            break
    except:
        pass

    # Expand all
    try:
        for btn in driver.find_elements(By.XPATH, "//*[contains(text(),'Expand')]"):
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(0.5)
    except:
        pass

    time.sleep(2)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    course_data = {
        "year": year,
        "course_url": course_url,
        "subjects": [],
        "sub_majors": [],
        "other_structures": []
    }

    subject_divs = soup.find_all("div", class_="unit-title")
    print(f"   Found {len(subject_divs)} items")

    for div in subject_divs:
        parent = div.find_parent("a")
        if not parent or not parent.get("href"):
            continue

        name = div.get_text(strip=True)
        code_div = parent.find("div", class_="section1")
        code = code_div.get_text(strip=True) if code_div else None
        cp_div = parent.find("div", class_="section2")
        credit_points = None
        if cp_div:
            match = re.search(r"\d+", cp_div.text)
            if match:
                credit_points = int(match.group())

        subject_url = urljoin(BASE_NEW, parent["href"])
        item_type = classify_type(code or "", subject_url)

        print(f"[{item_type.upper()}] {code}")
        driver.get(subject_url)
        time.sleep(3)

        # Click Read More & Expand all
        for btn_text in ["Read More", "Expand all"]:
            for btn in driver.find_elements(By.XPATH, f"//button[contains(text(),'{btn_text}')]"):
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.3)
                except:
                    pass

        soup2 = BeautifulSoup(driver.page_source, "html.parser")

        # Description
        desc = soup2.find("div", class_="readmore-content-wrapper")
        description = desc.get_text(strip=True) if desc else None

        # Prerequisites
        prereq = None
        antireq = None
        if item_type == "subject":
            req_div = soup2.find("div", id="Requisites")
            if req_div:
                target_link = req_div.find("a", href=lambda href: href and "subjectcode=" in href)
                if target_link:
                    driver.get(target_link["href"])
                    time.sleep(2)
                    antireq = extract_section_data(driver, "Anti-requisite(s)")
                    prereq = extract_section_data(driver, "Requisite(s)")
                    driver.back()
                    time.sleep(1)

        # Learning outcomes
        learning_outcomes = []
        if item_type == "subject":
            lo_header = soup2.find(string=re.compile(r"(Course Intended Learning Outcomes|Course intended learning outcomes \(CILOs\)|Subject Learning Outcomes|Subject learning objectives \(SLOs\)|Subject learning objectives)", re.I))
            if lo_header:
                header_tag = lo_header.find_parent(["h2", "h3", "h4", "div"])
                if not header_tag:
                    header_tag = lo_header
                
                # Check siblings for 'ul'
                ul = None
                for sib in header_tag.find_next_siblings():
                    if sib.name in ["h2", "h3", "h4"]:
                        break
                    if sib.name == "ul":
                        ul = sib
                        break
                    if sib.find("ul"):
                        ul = sib.find("ul")
                        break
                
                if not ul:
                    parent_div = header_tag.find_parent("div")
                    if parent_div:
                        ul = parent_div.find("ul")
                        
                if ul:
                    learning_outcomes = [li.get_text(strip=True) for li in ul.find_all("li")]

        # Extract children subjects for choice blocks / sub_majors
        children_subjects = []
        if item_type != "subject":
            for a in soup2.find_all("a"):
                txt = a.get_text(strip=True)
                href = a.get("href", "")
                if re.match(r"^\d{5}$", txt):
                    children_subjects.append(txt)
                elif "subjectcode=" in href:
                    m = re.search(r"subjectcode=(\d{5})", href)
                    if m: children_subjects.append(m.group(1))
            children_subjects = list(dict.fromkeys(children_subjects))

        # Parse purely subjective requirements
        prerequisite_codes = list(set(re.findall(r"\b\d{5}\b", str(prereq)))) if prereq else []
        antirequisite_codes = list(set(re.findall(r"\b\d{5}\b", str(antireq)))) if antireq else []

        subject_info = {
            "subject_code": code,
            "name": name,
            "url": subject_url,
            "year": year,
            "type": item_type,
            "credit_points": credit_points,
            "description": description,
            "learning_outcomes": learning_outcomes,
            "prereq_raw": prereq,
            "prereq_codes": prerequisite_codes,
            "antireq_raw": antireq,
            "antireq_codes": antirequisite_codes,
            "children_subjects": children_subjects,
        }

        if item_type == "subject":
            course_data["subjects"].append(subject_info)
        elif item_type == "sub_major":
            course_data["sub_majors"].append(subject_info)
        else:
            course_data["other_structures"].append(subject_info)

    print(f" {year}: {len(course_data['subjects'])} subjects | "
          f"{len(course_data['sub_majors'])} sub-majors")
    return course_data


def get_subjects_old(year, course_code):
    url = f"{BASE_OLD}/{year}/courses/{course_code.lower()}.html"
    print(f"[OLD] Scraping {year}: {url}")

    resp = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    subjects = []
    seen = set()

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if re.match(r"^\d{5}$", text):
            code = text
            name_span = a.find_next_sibling(string=True)
            name = name_span.strip() if name_span else ""
            if code not in seen:
                seen.add(code)
                subjects.append({
                    "subject_code": code,
                    "name": name,
                    "url": f"{BASE_OLD}/{year}/subjects/details/{code}.html",
                    "year": year,
                    "type": "subject"
                })
        elif re.match(r"^CBK\d+$", text):
            code = text
            name_span = a.find_next_sibling(string=True)
            name = name_span.strip() if name_span else ""
            if code not in seen:
                seen.add(code)
                subjects.append({
                    "subject_code": code,
                    "name": name,
                    "url": f"{BASE_OLD}/{year}/subjectgroups/details/{code}.html",
                    "year": year,
                    "type": "choice_block"
                })

    print(f" Found {len(subjects)} subjects")
    return subjects

def scrape_subject_old(subject):
    url = subject["url"]
    code = subject["subject_code"]

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return subject

        soup = BeautifulSoup(resp.text, "html.parser")

        # Description
        desc = None
        for header in soup.find_all(["h2", "h3", "h4"]):
            if "Description" in header.get_text():
                next_el = header.find_next_sibling()
                if next_el:
                    desc = next_el.get_text(strip=True)
                break

        # Credit points
        cp = None
        cp_text = soup.find(string=re.compile("Credit points:"))
        if cp_text:
            val = cp_text.find_next_sibling(string=True)
            if not val:
                 parent = cp_text.find_parent()
                 if parent:
                      val = str(parent.get_text())
            if val:
                 match = re.search(r"(\d+)\s*cp", str(val))
                 if not match: match = re.search(r"(\d+)", str(val))
                 if match: cp = int(match.group(1))

        if cp is None:
             cp_em = soup.find("em", string=re.compile("Credit points"))
             if cp_em:
                  val = cp_em.next_sibling
                  if val:
                       match = re.search(r"(\d+)", str(val))
                       if match: cp = int(match.group(1))
             
        if cp is None:
             cp_text = soup.find(string=re.compile("Credit points"))
             if cp_text:
                 parent = cp_text.find_parent()
                 if parent:
                     match = re.search(r"(\d+)", parent.get_text())
                     if match:
                         cp = int(match.group(1))
                         
        # Faculty
        faculty = None
        faculty_tag = soup.find("a", class_="coursearea")
        if faculty_tag:
            faculty = faculty_tag.get_text(strip=True)

        # Prerequisites
        prereq = None
        for header in soup.find_all(["h2", "h3", "h4"]):
            if "Requisite" in header.get_text():
                next_el = header.find_next_sibling()
                if next_el:
                    prereq = next_el.get_text(strip=True)
                break
                
        if not prereq:
            req_elem = soup.find(string=re.compile(r"Requisite\(s\):"))
            if req_elem and req_elem.parent and req_elem.parent.parent:
                container_text = req_elem.parent.parent.get_text(separator=' ', strip=True)
                m = re.search(r"Requisite\(s\):(.*?)(Recommended|Anti-requisite|Description)", container_text, re.I)
                if m:
                    prereq = m.group(1).strip()
                else:
                    m = re.search(r"Requisite\(s\):(.*?)$", container_text)
                    if m: prereq = m.group(1).strip()
                    
        # Anti-requisites
        antireq = None
        anti_elem = soup.find(string=re.compile(r"Anti-requisite\(s\):"))
        if anti_elem and anti_elem.parent and anti_elem.parent.parent:
            container_text = anti_elem.parent.parent.get_text(separator=' ', strip=True)
            m = re.search(r"Anti-requisite\(s\):(.*?)(Recommended|Requisite|Description)", container_text, re.I)
            if m:
                antireq = m.group(1).strip()
            else:
                m = re.search(r"Anti-requisite\(s\):(.*?)$", container_text)
                if m: antireq = m.group(1).strip()

        # Learning outcomes
        learning_outcomes = []
        lo_headers = soup.find_all(lambda tag: tag.name in ["h2", "h3", "h4"] and 
                                 re.search(r"(Course intended learning outcomes|Subject learning objectives)", tag.get_text(strip=True), re.I))
        for header in lo_headers:
            ul = None
            for sib in header.find_next_siblings():
                if sib.name in ["h2", "h3", "h4"]:
                    break
                if sib.name == "ul":
                    ul = sib
                    break
                ul_in = sib.find("ul")
                if ul_in:
                    ul = ul_in
                    break
            if ul:
                learning_outcomes.extend([li.get_text(strip=True) for li in ul.find_all("li")])

        # Children subjects (if not a subject)
        children_subjects = []
        if subject["type"] != "subject":
            for a in soup.find_all("a"):
                txt = a.get_text(strip=True)
                href = a.get("href", "")
                if re.match(r"^\d{5}$", txt):
                    children_subjects.append(txt)
                elif "subjects/" in href and re.search(r"(\d{5})\.html", href):
                    m = re.search(r"(\d{5})\.html", href)
                    if m: children_subjects.append(m.group(1))
            children_subjects = list(dict.fromkeys(children_subjects))

        prerequisite_codes = list(set(re.findall(r"\b\d{5}\b", str(prereq)))) if prereq else []
        antirequisite_codes = list(set(re.findall(r"\b\d{5}\b", str(antireq)))) if antireq else []

        subject.update({
            "description": desc,
            "credit_points": cp,
            "faculty": faculty,
            "prereq_raw": prereq,
            "prereq_codes": prerequisite_codes,
            "antireq_raw": antireq,
            "antireq_codes": antirequisite_codes,
            "children_subjects": children_subjects,
            "learning_outcomes": list(dict.fromkeys(learning_outcomes)), # remove potential duplicates
        })

        print(f" {code}: {subject['name'][:40]}")

    except Exception as e:
        print(f" {code}: {e}")

    return subject

def scrape_old(year, course_code):
    subjects = get_subjects_old(year, course_code)
    detailed = []

    for i, subj in enumerate(subjects):
        print(f"[{i+1}/{len(subjects)}] {subj['subject_code']}")
        result = scrape_subject_old(subj)
        detailed.append(result)
        time.sleep(0.5)

    return {
        "year": year,
        "subjects": [s for s in detailed if s["type"] == "subject"],
        "sub_majors": [],
        "other_structures": [s for s in detailed if s["type"] != "subject"]
    }


if __name__ == "__main__":
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service)

    try:
        for course in COURSES:
            course_name = course["name"]
            course_code = course["code"]
            output_file = course["output"]
            
            print(f"{'#'*60}")
            print(f"SCRAPING {course_name.upper()} ({course_code})")
            print(f"#{'*'*59}")
            
            all_data = {
                "metadata": {
                    "course": course_name,
                    "course_code": course_code,
                    "years": YEARS_OLD + YEARS_NEW,
                    "source": "UTS Course Handbook"
                },
                "data_by_year": {}
            }

            for year in YEARS_OLD:
                print(f"{'='*50}")
                print(f"YEAR {year} (OLD HANDBOOK) - {course_code}")
                print("="*50)
                all_data["data_by_year"][str(year)] = scrape_old(year, course_code)
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(all_data, f, indent=4, ensure_ascii=False)
                print(f"Saved year {year} to {output_file}")

            for year in YEARS_NEW:
                print(f"{'='*50}")
                print(f"YEAR {year} (NEW HANDBOOK) - {course_code}")
                print("="*50)
                all_data["data_by_year"][str(year)] = scrape_new(driver, year, course_code)
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(all_data, f, indent=4, ensure_ascii=False)
                print(f"Saved year {year} to {output_file}")
                
            with open(output_file, "w", encoding="utf-8") as f:
                 json.dump(all_data, f, indent=4, ensure_ascii=False)

            print(f" DONE WITH {course_name}! Saved to {output_file}")
            for year in YEARS_OLD + YEARS_NEW:
                 year_data = all_data["data_by_year"].get(str(year), {})
                 print(f"   {year}: {len(year_data.get('subjects', []))} subjects | "
                       f"{len(year_data.get('sub_majors', []))} sub-majors")

    finally:
        driver.quit()