# UTS Handbook Digital Twin

A scraper and graph-database pipeline that builds a "digital twin" of the UTS (University of Technology Sydney) course handbook for the Bachelor and Master of Artificial Intelligence programs — capturing subjects, prerequisites, requisites, and course structure across multiple years (2023–2026) and loading them into a Neo4j graph.

## Overview

The project has two stages:

1. **Scrape** — `scraper_new.py` crawls both the legacy (`handbookpre2025.uts.edu.au`, years 2023–2024) and current (`coursehandbook.uts.edu.au`, years 2025–2026) UTS handbook sites for the Bachelor of Artificial Intelligence (`C10474`) and Master of Artificial Intelligence (`C04443`). For each program and year it recursively walks the course structure (streams, choice blocks, majors, sub-majors) and pulls per-subject details — description, credit points, faculty, study level, learning outcomes, workload, and prerequisite/anti-requisite requirements — into nested JSON.
2. **Import** — `neo4j_importer.py` loads the scraped JSON into a Neo4j graph database, creating `Course`, `Subject`, and group nodes (`Major`, `SubMajor`, `ChoiceBlock`, `Stream`) with relationships for `CONTAINS_SUBJECT`, `CONTAINS_GROUP`, `REQUIRES` (prerequisites), `MUTUALLY_EXCLUSIVE` (anti-requisites), and `EVOLVED_TO` (linking the same subject code across consecutive years, so you can trace how a subject changed over time).

## Project structure

```
UTS_Handbook_Digital_Twin/
├── scraper_new.py           # Scrapes the UTS handbook (legacy + current sites) into JSON
├── neo4j_importer.py        # Loads the scraped JSON into a Neo4j graph
└── dataset/
    ├── bachelor_ai.json     # Scraped output for the Bachelor of AI
    ├── master_ai.json       # Scraped output for the Master of AI
    └── subjects_archive/    # Per-year cached subject records (used to resume interrupted scrapes)
        ├── 2023_subjects.json
        ├── 2024_subjects.json
        ├── 2025_subjects.json
        └── 2026_subjects.json
```

## Getting started

### Prerequisites

- Python 3.9+
- Google Chrome (the scraper drives it via Selenium)
- A running Neo4j instance (local or remote) if you want to import the data

### Installation

```bash
git clone https://github.com/Minh169/UTS_Handbook_Digital_Twin.git
cd UTS_Handbook_Digital_Twin
pip install selenium beautifulsoup4 webdriver-manager neo4j
```

### Scraping the handbook

```bash
python scraper_new.py
```

This drives a headless-capable Chrome browser through both handbook sites for every configured program and year, writing results to `dataset/bachelor_ai.json` and `dataset/master_ai.json`. Progress is checkpointed per year in `dataset/subjects_archive/`, so if the scraper is interrupted (or a page fails to load after 3 retries) it can resume from the last saved state instead of starting over.

### Importing into Neo4j

`neo4j_importer.py` currently reads its connection details (URI, username, password) from constants at the top of the file — update those for your own Neo4j instance before running (or, better, switch them to environment variables so credentials aren't stored in the script).

```bash
python neo4j_importer.py
```

This clears the target database and rebuilds it from `dataset/bachelor_ai.json` and `dataset/master_ai.json`. Once it finishes, open the Neo4j Browser and run:

```cypher
MATCH (n) RETURN n LIMIT 100
```

## Notes

- The scraper adds short delays between page loads and automatically restarts the browser if it crashes, to stay reasonably polite to the handbook sites.
- Subject codes are used to detect duplicates across the legacy/current sites and across years, so re-running the scraper won't create duplicate nodes on import (Neo4j `MERGE` is used throughout).
