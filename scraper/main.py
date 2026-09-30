import os
import json
import time
import re
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import pandas as pd
import requests
import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")

if not APOLLO_API_KEY:
    raise RuntimeError(
        "APOLLO_API_KEY is missing from .env"
    )


# ============================================================
# CONFIG
# ============================================================

CONFIG_FILE = "config.yaml"

# IMPORTANT:
# This attempts to scrape up to 20 Naukri result pages
# for EVERY keyword.
MAX_PAGES_PER_KEYWORD = 20

# 0 = unlimited
MAX_UNIQUE_COMPANIES_TO_CHECK = 0

# 0 = unlimited qualified jobs
MAX_QUALIFIED_JOBS = 0

# Wait between requests
REQUEST_DELAY = 1.2

# Browser visible while debugging
HEADLESS = False


# ============================================================
# FINANCE TITLES FOR APOLLO PEOPLE SEARCH
# ============================================================

FINANCE_PERSON_TITLES = [
    "accounts receivable",
    "accounts payable",
    "finance manager",
    "finance analyst",
    "finance executive",
    "finance associate",
    "finance controller",
    "financial controller",
    "financial analyst",
    "accountant",
    "senior accountant",
    "accounting manager",
    "accounting analyst",
    "accounting executive",
    "controller",
    "assistant controller",
    "billing",
    "billing specialist",
    "collections",
    "revenue accounting",
    "treasury",
]


# ============================================================
# COMPANY TYPE SIGNALS
#
# These are PREFERRED signals.
# They are NOT hard filters.
# ============================================================

COMPANY_TYPE_KEYWORDS = {
    "ai_agents": [
        "ai agent",
        "ai agents",
        "agentic ai",
        "agentic",
        "autonomous agents",
    ],

    "ai": [
        "artificial intelligence",
        "machine learning",
        "generative ai",
        "genai",
        "ai-powered",
        "ai platform",
    ],

    "b2b_saas": [
        "b2b saas",
        "enterprise software",
        "business software",
        "software for businesses",
    ],

    "saas": [
        "saas",
        "software as a service",
        "cloud software",
        "cloud-based software",
    ],

    "startup": [
        "startup",
        "start-up",
        "venture-backed",
        "venture backed",
    ],

    "technology": [
        "technology",
        "software",
        "information technology",
    ],
}


# ============================================================
# LOAD CONFIG
# ============================================================

def load_config():

    with open(
        CONFIG_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        return yaml.safe_load(file)


# ============================================================
# COMPANY NAME NORMALIZATION
# ============================================================

def normalize_company_name(name):

    if name is None:
        return ""

    name = str(name).lower().strip()

    # Remove common corporate suffixes
    replacements = [
        "private limited",
        "private ltd",
        "pvt ltd",
        "pvt. ltd.",
        "limited",
        "ltd",
        "llp",
        "incorporated",
        "inc.",
        "inc",
        "corporation",
        "corp.",
        "corp",
        "company",
        "co.",
    ]

    for replacement in replacements:

        name = name.replace(
            replacement,
            " "
        )

    # Remove punctuation
    name = "".join(
        char if char.isalnum() else " "
        for char in name
    )

    # Normalize whitespace
    name = " ".join(
        name.split()
    )

    return name


def company_name_match(
    naukri_name,
    apollo_name
):

    a = normalize_company_name(
        naukri_name
    )

    b = normalize_company_name(
        apollo_name
    )

    if not a or not b:
        return False, 0.0

    # Exact normalized match
    if a == b:
        return True, 1.0

    # One name contains the other
    if len(a) >= 5 and (
        a in b or b in a
    ):

        return True, 0.95

    # Fuzzy match only for reasonably long names
    if len(a) >= 8 and len(b) >= 8:

        similarity = SequenceMatcher(
            None,
            a,
            b
        ).ratio()

        if similarity >= 0.90:

            return True, similarity

    return False, 0.0


# ============================================================
# APOLLO HEADERS
# ============================================================

def apollo_headers():

    return {
        "x-api-key":
            APOLLO_API_KEY,

        "Content-Type":
            "application/json",

        "accept":
            "application/json",

        "Cache-Control":
            "no-cache"
    }


# ============================================================
# APOLLO COMPANY SEARCH
#
# HARD FILTER:
# 101 - 1000 EMPLOYEES
#
# This is performed BEFORE the full Naukri job page is opened.
# ============================================================

def apollo_company_size_gate(
    company
):

    endpoint = (
        "https://api.apollo.io/api/v1/"
        "mixed_companies/search"
    )

    payload = {

        "q_organization_name":
            company,

        "organization_num_employees_ranges":
            [
                "101,1000"
            ],

        "page":
            1,

        "per_page":
            10
    }

    try:

        response = requests.post(
            endpoint,
            headers=apollo_headers(),
            json=payload,
            timeout=30
        )

    except requests.RequestException as error:

        print(
            "Apollo company request error:",
            error
        )

        return None

    print(
        "Apollo company HTTP:",
        response.status_code
    )

    if response.status_code != 200:

        print(
            "Apollo error:",
            response.text[:500]
        )

        return None

    try:

        data = response.json()

    except ValueError:

        return None

    candidates = []

    # Net-new organizations
    candidates.extend(
        data.get(
            "organizations",
            []
        )
    )

    # Existing saved accounts
    candidates.extend(
        data.get(
            "accounts",
            []
        )
    )

    if not candidates:

        return None

    best_match = None
    best_score = 0.0

    for candidate in candidates:

        apollo_name = str(
            candidate.get(
                "name",
                ""
            )
        ).strip()

        matches, score = company_name_match(
            company,
            apollo_name
        )

        if matches and score > best_score:

            best_match = candidate
            best_score = score

    if not best_match:

        return None

    # --------------------------------------------------------
    # Determine Apollo organization ID
    # --------------------------------------------------------

    organization_id = (
        best_match.get(
            "organization_id"
        )
    )

    if not organization_id:

        organization_id = (
            best_match.get(
                "id",
                ""
            )
        )

    # --------------------------------------------------------
    # Employee count
    #
    # Apollo's search itself is already filtered to 101-1000.
    # If the explicit count is returned, verify it too.
    # --------------------------------------------------------

    employee_count = (
        best_match.get(
            "estimated_num_employees"
        )
    )

    if employee_count is not None:

        try:

            employee_count = int(
                employee_count
            )

            if not (
                101
                <= employee_count
                <= 1000
            ):

                return None

        except (
            ValueError,
            TypeError
        ):

            employee_count = None

    domain = (
        best_match.get(
            "primary_domain",
            best_match.get(
                "domain",
                ""
            )
        )
    )

    website = (
        best_match.get(
            "website_url",
            ""
        )
    )

    linkedin = (
        best_match.get(
            "linkedin_url",
            ""
        )
    )

    industry = (
        best_match.get(
            "industry",
            ""
        )
    )

    industries = (
        best_match.get(
            "industries",
            []
        )
    )

    keywords = (
        best_match.get(
            "keywords",
            []
        )
    )

    founded_year = (
        best_match.get(
            "founded_year"
        )
    )

    print(
        "Apollo matched:",
        best_match.get(
            "name",
            ""
        )
    )

    print(
        "Match score:",
        round(
            best_score,
            3
        )
    )

    print(
        "Employees:",
        employee_count
    )

    return {

        "apollo_id":
            organization_id,

        "apollo_name":
            best_match.get(
                "name",
                ""
            ),

        "employee_count":
            employee_count,

        "domain":
            domain,

        "website":
            website,

        "linkedin":
            linkedin,

        "industry":
            industry,

        "industries":
            industries,

        "keywords":
            keywords,

        "founded_year":
            founded_year,

        "match_score":
            best_score
    }


# ============================================================
# APOLLO FINANCE TEAM CHECK
#
# We count current Apollo people matching finance/accounting
# titles at the exact Apollo organization.
#
# Apollo People API Search currently costs 0 credits.
# ============================================================

def apollo_finance_people_count(
    organization_id
):

    if not organization_id:
        return None

    endpoint = (
        "https://api.apollo.io/api/v1/"
        "mixed_people/api_search"
    )

    payload = {

        "organization_ids":
            [
                organization_id
            ],

        "person_titles":
            FINANCE_PERSON_TITLES,

        "include_similar_titles":
            False,

        "page":
            1,

        "per_page":
            1
    }

    try:

        response = requests.post(
            endpoint,
            headers=apollo_headers(),
            json=payload,
            timeout=30
        )

    except requests.RequestException as error:

        print(
            "Apollo people request error:",
            error
        )

        return None

    print(
        "Apollo people HTTP:",
        response.status_code
    )

    if response.status_code != 200:

        print(
            "Apollo people error:",
            response.text[:500]
        )

        return None

    try:

        data = response.json()

    except ValueError:

        return None

    total_entries = data.get(
        "total_entries"
    )

    if total_entries is None:

        # Some responses may expose total_count
        total_entries = data.get(
            "total_count"
        )

    if total_entries is None:
        return None

    try:

        total_entries = int(
            total_entries
        )

    except (
        ValueError,
        TypeError
    ):

        return None

    return total_entries


# ============================================================
# COMPANY TYPE CLASSIFICATION
# ============================================================

def classify_company_type(
    industry,
    industries,
    keywords
):

    parts = []

    if industry:
        parts.append(
            str(industry)
        )

    if isinstance(
        industries,
        list
    ):

        parts.extend(
            str(value)
            for value in industries
            if value
        )

    if isinstance(
        keywords,
        list
    ):

        parts.extend(
            str(value)
            for value in keywords
            if value
        )

    text = " ".join(
        parts
    ).lower()

    detected = []

    for category, terms in (
        COMPANY_TYPE_KEYWORDS.items()
    ):

        for term in terms:

            if term.lower() in text:

                detected.append(
                    category
                )

                break

    return sorted(
        set(detected)
    )


# ============================================================
# PRE-FILTER COMPANY
#
# BOTH HARD FILTERS MUST PASS.
#
# 1. 101-1000 employees
# 2. 11+ finance people found in Apollo
#
# ONLY THEN is the actual Naukri job page opened.
# ============================================================

def prefilter_company(
    company,
    config
):

    print("\n")
    print(
        "-" * 70
    )

    print(
        "PRE-FILTERING:",
        company
    )

    print(
        "-" * 70
    )

    # --------------------------------------------------------
    # COMPANY SIZE
    # --------------------------------------------------------

    apollo_company = (
        apollo_company_size_gate(
            company
        )
    )

    if not apollo_company:

        print(
            "REJECTED BEFORE JOB SCRAPE:"
            " employee-size/company match failed"
        )

        return None

    # Explicit employee count is available:
    # verify hard limit again.
    employee_count = (
        apollo_company[
            "employee_count"
        ]
    )

    minimum_employees = int(
        config[
            "icp"
        ][
            "min_employees"
        ]
    )

    maximum_employees = int(
        config[
            "icp"
        ][
            "max_employees"
        ]
    )

    if employee_count is not None:

        if not (
            minimum_employees
            <= employee_count
            <= maximum_employees
        ):

            print(
                "REJECTED BEFORE JOB SCRAPE:"
            )

            print(
                "Employee count:",
                employee_count
            )

            return None

    # --------------------------------------------------------
    # FINANCE TEAM
    # --------------------------------------------------------

    finance_count = (
        apollo_finance_people_count(
            apollo_company[
                "apollo_id"
            ]
        )
    )

    minimum_finance = int(
        config[
            "icp"
        ][
            "min_finance_employees"
        ]
    )

    if finance_count is None:

        print(
            "REJECTED BEFORE JOB SCRAPE:"
            " finance-team evidence unavailable"
        )

        return None

    if finance_count < minimum_finance:

        print(
            "REJECTED BEFORE JOB SCRAPE:"
        )

        print(
            "Finance people found:",
            finance_count
        )

        return None

    print(
        "EMPLOYEE FILTER: PASS"
    )

    print(
        "FINANCE TEAM FILTER: PASS"
    )

    # --------------------------------------------------------
    # COMPANY TYPE
    # --------------------------------------------------------

    company_types = (
        classify_company_type(
            apollo_company[
                "industry"
            ],
            apollo_company[
                "industries"
            ],
            apollo_company[
                "keywords"
            ]
        )
    )

    preferred_types = set(
        config.get(
            "preferred_company_types",
            []
        )
    )

    company_type_match = bool(
        preferred_types.intersection(
            set(company_types)
        )
    )

    print(
        "Company types:",
        company_types
    )

    if company_type_match:

        print(
            "PREFERRED COMPANY TYPE: YES"
        )

    else:

        print(
            "PREFERRED COMPANY TYPE: NO"
        )

    return {

        "apollo_id":
            apollo_company[
                "apollo_id"
            ],

        "apollo_name":
            apollo_company[
                "apollo_name"
            ],

        "employee_count":
            employee_count,

        "finance_people_found":
            finance_count,

        "company_types":
            company_types,

        "company_type_match":
            company_type_match,

        "industry":
            apollo_company[
                "industry"
            ],

        "domain":
            apollo_company[
                "domain"
            ],

        "website":
            apollo_company[
                "website"
            ],

        "linkedin":
            apollo_company[
                "linkedin"
            ],

        "founded_year":
            apollo_company[
                "founded_year"
            ],

        "match_score":
            apollo_company[
                "match_score"
            ]
    }


# ============================================================
# NAUKRI JOB CARD EXTRACTION
#
# IMPORTANT:
# This reads only the search-result card.
# The full job page is NOT opened here.
# ============================================================

def extract_naukri_cards(page):

    cards = page.locator(
        'a[href*="/job-listings-"]'
    ).evaluate_all(
        """
        elements => {

            function clean(value) {
                return (value || "")
                    .replace(/\\s+/g, " ")
                    .trim();
            }

            return elements
                .map(anchor => {

                    let current = anchor;
                    let company = "";
                    let cardText = "";

                    for (
                        let i = 0;
                        i < 10 && current;
                        i++
                    ) {

                        const companySelectors = [
                            "a[href*='/company/']",
                            "a[class*='comp-name']",
                            "a[class*='company-name']",
                            "a[class*='company']",
                            "span[class*='comp-name']",
                            "span[class*='company-name']",
                            "div[class*='comp-name']",
                            "div[class*='company-name']"
                        ];

                        let companyElement = null;

                        for (
                            const selector
                            of companySelectors
                        ) {

                            companyElement =
                                current.querySelector(
                                    selector
                                );

                            if (
                                companyElement &&
                                clean(
                                    companyElement.innerText
                                )
                            ) {

                                break;
                            }
                        }

                        if (
                            companyElement &&
                            clean(
                                companyElement.innerText
                            )
                        ) {

                            company =
                                clean(
                                    companyElement.innerText
                                );

                            cardText =
                                clean(
                                    current.innerText
                                );

                            break;
                        }

                        current =
                            current.parentElement;
                    }

                    return {

                        job_title:
                            clean(
                                anchor.innerText
                            ),

                        job_url:
                            anchor.href
                                .split("?")[0],

                        company:
                            company,

                        card_text:
                            cardText
                    };
                })
                .filter(
                    item =>
                        item.job_title &&
                        item.job_url
                );
        }
        """
    )

    unique = {}

    for card in cards:

        url = card[
            "job_url"
        ].strip()

        if not url:
            continue

        if url not in unique:

            unique[url] = card

    return list(
        unique.values()
    )


# ============================================================
# MOVE TO NEXT NAUKRI PAGE
# ============================================================

def go_to_next_naukri_page(
    page,
    previous_first_job_url
):

    selectors = [

        'a[aria-label*="Next"]',
        'button[aria-label*="Next"]',

        'a[title*="Next"]',
        'button[title*="Next"]',

        'a[class*="next"]',
        'button[class*="next"]'
    ]

    for selector in selectors:

        locator = page.locator(
            selector
        )

        if locator.count() == 0:
            continue

        try:

            button = locator.last

            if not button.is_visible():
                continue

            aria_disabled = button.get_attribute(
                "aria-disabled"
            )

            disabled = button.get_attribute(
                "disabled"
            )

            if (
                aria_disabled == "true"
                or disabled is not None
            ):

                continue

            button.click(
                timeout=5000
            )

            # Wait for content to change.
            for _ in range(15):

                page.wait_for_timeout(
                    500
                )

                cards = extract_naukri_cards(
                    page
                )

                if cards:

                    first_url = cards[0][
                        "job_url"
                    ]

                    if (
                        first_url
                        != previous_first_job_url
                    ):

                        return True

        except Exception:
            continue

    # Text fallback
    try:

        next_button = page.get_by_text(
            "Next",
            exact=True
        ).last

        if next_button.count() > 0:

            next_button.click(
                timeout=5000
            )

            for _ in range(15):

                page.wait_for_timeout(
                    500
                )

                cards = extract_naukri_cards(
                    page
                )

                if cards:

                    first_url = cards[0][
                        "job_url"
                    ]

                    if (
                        first_url
                        != previous_first_job_url
                    ):

                        return True

    except Exception:
        pass

    return False


# ============================================================
# NAUKRI SEARCH
#
# 20 PAGES PER KEYWORD
#
# Only qualifying companies proceed to full job scraping.
# ============================================================

def search_naukri_keyword(
    page,
    keyword,
    config,
    company_cache,
    qualified_jobs,
    seen_job_urls
):

    print("\n")
    print(
        "=" * 75
    )

    print(
        "NAUKRI SEARCH:",
        keyword
    )

    print(
        "=" * 75
    )

    # --------------------------------------------------------
    # Open Naukri
    # --------------------------------------------------------

    page.goto(
        "https://www.naukri.com/",
        wait_until="domcontentloaded",
        timeout=30000
    )

    page.wait_for_timeout(
        2000
    )

    # --------------------------------------------------------
    # Search keyword
    # --------------------------------------------------------

    search_box = page.locator(
        'input[placeholder*="Search jobs"], '
        'input[placeholder*="skills"], '
        'input[type="text"]'
    ).first

    if search_box.count() == 0:

        raise RuntimeError(
            "Naukri search box not found."
        )

    search_box.fill(
        keyword
    )

    search_box.press(
        "Enter"
    )

    page.wait_for_timeout(
        5000
    )

    # --------------------------------------------------------
    # Pages 1 -> 20
    # --------------------------------------------------------

    for page_number in range(
        1,
        MAX_PAGES_PER_KEYWORD + 1
    ):

        print("\n")
        print(
            f"[{keyword}] PAGE "
            f"{page_number}/"
            f"{MAX_PAGES_PER_KEYWORD}"
        )

        cards = extract_naukri_cards(
            page
        )

        print(
            "Cards found:",
            len(cards)
        )

        if not cards:

            print(
                "No job cards found."
            )

            print(
                "Stopping this keyword."
            )

            break

        previous_first_job_url = (
            cards[0][
                "job_url"
            ]
        )

        # ----------------------------------------------------
        # Process cards
        # ----------------------------------------------------

        for card in cards:

            job_url = card[
                "job_url"
            ].strip()

            company = card[
                "company"
            ].strip()

            job_title = card[
                "job_title"
            ].strip()

            # Avoid duplicates
            if job_url in seen_job_urls:

                continue

            seen_job_urls.add(
                job_url
            )

            # ------------------------------------------------
            # Company name required
            # ------------------------------------------------

            if not company:

                print(
                    "SKIP:"
                    " company name not visible"
                    " on result card"
                )

                continue

            company_key = (
                normalize_company_name(
                    company
                )
            )

            # ------------------------------------------------
            # Check company only ONCE
            # ------------------------------------------------

            if company_key not in company_cache:

                if (
                    MAX_UNIQUE_COMPANIES_TO_CHECK
                    > 0
                    and len(company_cache)
                    >= MAX_UNIQUE_COMPANIES_TO_CHECK
                ):

                    print(
                        "Company-check limit reached."
                    )

                    continue

                qualification = (
                    prefilter_company(
                        company,
                        config
                    )
                )

                company_cache[
                    company_key
                ] = qualification

            else:

                qualification = (
                    company_cache[
                        company_key
                    ]
                )

            # ------------------------------------------------
            # HARD GATE
            # ------------------------------------------------

            if qualification is None:

                # IMPORTANT:
                # Do not open the job page.
                continue

            # ------------------------------------------------
            # Qualified company.
            #
            # NOW we may open the job page.
            # ------------------------------------------------

            if (
                MAX_QUALIFIED_JOBS > 0
                and len(qualified_jobs)
                >= MAX_QUALIFIED_JOBS
            ):

                return

            print(
                "\nPASSED PRE-FILTER:"
                f" {company}"
            )

            try:

                job_data = extract_job_page(
                    page,
                    job_url
                )

            except Exception as error:

                print(
                    "Job-page extraction error:",
                    error
                )

                continue

            if not job_data:

                continue

            # Add discovery information
            job_data[
                "discovery_keyword"
            ] = keyword

            job_data[
                "naukri_card_company"
            ] = company

            job_data[
                "employee_count"
            ] = qualification[
                "employee_count"
            ]

            job_data[
                "finance_people_found"
            ] = qualification[
                "finance_people_found"
            ]

            job_data[
                "company_types"
            ] = "|".join(
                qualification[
                    "company_types"
                ]
            )

            job_data[
                "company_type_match"
            ] = qualification[
                "company_type_match"
            ]

            job_data[
                "industry"
            ] = qualification[
                "industry"
            ]

            job_data[
                "apollo_domain"
            ] = qualification[
                "domain"
            ]

            job_data[
                "apollo_linkedin"
            ] = qualification[
                "linkedin"
            ]

            job_data[
                "apollo_company_name"
            ] = qualification[
                "apollo_name"
            ]

            job_data[
                "apollo_match_score"
            ] = qualification[
                "match_score"
            ]

            qualified_jobs.append(
                job_data
            )

            print(
                "QUALIFIED JOB:",
                job_data[
                    "job_title"
                ]
            )

            time.sleep(
                REQUEST_DELAY
            )

        # ----------------------------------------------------
        # Go to next page
        # ----------------------------------------------------

        if (
            page_number
            >= MAX_PAGES_PER_KEYWORD
        ):

            print(
                "Reached page 20."
            )

            break

        moved = (
            go_to_next_naukri_page(
                page,
                previous_first_job_url
            )
        )

        if not moved:

            print(
                "Could not move to next page."
            )

            print(
                "Stopping this keyword."
            )

            break


# ============================================================
# JSON-LD
# ============================================================

def get_json_ld(page):

    scripts = page.locator(
        'script[type="application/ld+json"]'
    ).all_inner_texts()

    for script in scripts:

        try:

            data = json.loads(
                script
            )

            if isinstance(
                data,
                dict
            ):

                if data.get(
                    "@type"
                ) == "JobPosting":

                    return data

            elif isinstance(
                data,
                list
            ):

                for item in data:

                    if (
                        isinstance(
                            item,
                            dict
                        )
                        and item.get(
                            "@type"
                        )
                        == "JobPosting"
                    ):

                        return item

        except json.JSONDecodeError:

            continue

    return {}


# ============================================================
# JOB PAGE EXTRACTION
#
# ONLY CALLED AFTER PREFILTER PASSES.
# ============================================================

def extract_job_page(
    page,
    url
):

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=30000
    )

    page.wait_for_timeout(
        2500
    )

    data = get_json_ld(
        page
    )

    # --------------------------------------------------------
    # Job title
    # --------------------------------------------------------

    title = data.get(
        "title",
        ""
    )

    if not isinstance(
        title,
        str
    ):

        title = str(
            title
        )

    if not title.strip():

        headings = page.locator(
            "h1"
        ).all_inner_texts()

        if headings:

            title = str(
                headings[0]
            )

    # --------------------------------------------------------
    # Company
    # --------------------------------------------------------

    company = ""

    hiring_org = data.get(
        "hiringOrganization",
        {}
    )

    if isinstance(
        hiring_org,
        dict
    ):

        company = hiring_org.get(
            "name",
            ""
        )

    if not isinstance(
        company,
        str
    ):

        company = str(
            company
        )

    # --------------------------------------------------------
    # Location
    # --------------------------------------------------------

    locations = []

    job_location = data.get(
        "jobLocation",
        {}
    )

    if isinstance(
        job_location,
        list
    ):

        for location_item in (
            job_location
        ):

            if not isinstance(
                location_item,
                dict
            ):

                continue

            address = location_item.get(
                "address",
                {}
            )

            if not isinstance(
                address,
                dict
            ):

                continue

            city = address.get(
                "addressLocality",
                ""
            )

            if isinstance(
                city,
                list
            ):

                locations.extend(
                    str(value)
                    for value in city
                    if value
                )

            elif city:

                locations.append(
                    str(city)
                )

    elif isinstance(
        job_location,
        dict
    ):

        address = job_location.get(
            "address",
            {}
        )

        if isinstance(
            address,
            dict
        ):

            city = address.get(
                "addressLocality",
                ""
            )

            if isinstance(
                city,
                list
            ):

                locations.extend(
                    str(value)
                    for value in city
                    if value
                )

            elif city:

                locations.append(
                    str(city)
                )

    location = ", ".join(
        sorted(
            set(
                str(value).strip()
                for value in locations
                if str(value).strip()
            )
        )
    )

    # --------------------------------------------------------
    # Description
    # --------------------------------------------------------

    description = data.get(
        "description",
        ""
    )

    if not isinstance(
        description,
        str
    ):

        description = str(
            description
        )

    return {

        "job_title":
            title.strip(),

        "company":
            company.strip(),

        "location":
            location,

        "job_url":
            url,

        "job_description":
            description[:5000]
    }


# ============================================================
# AGGREGATE QUALIFIED JOBS BY COMPANY
# ============================================================

def aggregate_companies(
    qualified_jobs
):

    if not qualified_jobs:

        return pd.DataFrame()

    jobs_df = pd.DataFrame(
        qualified_jobs
    )

    if jobs_df.empty:

        return pd.DataFrame()

    # --------------------------------------------------------
    # Normalize company identity
    # --------------------------------------------------------

    jobs_df[
        "company_key"
    ] = jobs_df[
        "company"
    ].apply(
        normalize_company_name
    )

    rows = []

    for company_key, group in (
        jobs_df.groupby(
            "company_key"
        )
    ):

        if not company_key:
            continue

        company = str(
            group.iloc[0][
                "company"
            ]
        ).strip()

        job_titles = sorted(
            set(
                str(value).strip()
                for value in group[
                    "job_title"
                ]
                if pd.notna(value)
                and str(value).strip()
            )
        )

        job_urls = sorted(
            set(
                str(value).strip()
                for value in group[
                    "job_url"
                ]
                if pd.notna(value)
                and str(value).strip()
            )
        )

        discovery_keywords = sorted(
            set(
                str(value).strip()
                for value in group[
                    "discovery_keyword"
                ]
                if pd.notna(value)
                and str(value).strip()
            )
        )

        all_types = set()

        for value in group[
            "company_types"
        ]:

            if pd.isna(value):
                continue

            all_types.update(
                part.strip()
                for part in str(value).split("|")
                if part.strip()
            )

        company_type_match = any(
            str(value).lower()
            in ["true", "1"]
            for value in group[
                "company_type_match"
            ]
        )

        # We take the first because
        # all rows for the company passed
        # the same pre-filter.
        employee_count = (
            group.iloc[0][
                "employee_count"
            ]
        )

        finance_people = (
            group.iloc[0][
                "finance_people_found"
            ]
        )

        industry = str(
            group.iloc[0][
                "industry"
            ]
        )

        apollo_domain = str(
            group.iloc[0][
                "apollo_domain"
            ]
        )

        linkedin = str(
            group.iloc[0][
                "apollo_linkedin"
            ]
        )

        apollo_name = str(
            group.iloc[0][
                "apollo_company_name"
            ]
        )

        # ----------------------------------------------------
        # Priority score
        # ----------------------------------------------------

        score = 0

        # Multiple relevant hiring signals
        job_count = len(
            group
        )

        if job_count >= 5:
            score += 40
        elif job_count >= 3:
            score += 30
        elif job_count >= 2:
            score += 20
        else:
            score += 10

        # Preferred company type
        if company_type_match:
            score += 30

        # Finance-team size above threshold
        if pd.notna(
            finance_people
        ):

            finance_people = int(
                finance_people
            )

            if finance_people >= 25:
                score += 20
            elif finance_people >= 15:
                score += 15
            else:
                score += 10

        rows.append({

            "company":
                company,

            "apollo_company_name":
                apollo_name,

            "employee_count":
                employee_count,

            "finance_people_found":
                finance_people,

            "relevant_job_count":
                job_count,

            "job_titles":
                " | ".join(
                    job_titles
                ),

            "discovery_keywords":
                " | ".join(
                    discovery_keywords
                ),

            "company_types":
                " | ".join(
                    sorted(
                        all_types
                    )
                ),

            "preferred_company_type":
                "YES"
                if company_type_match
                else "NO",

            "industry":
                industry,

            "apollo_domain":
                apollo_domain,

            "linkedin":
                linkedin,

            "icp_score":
                score,

            "job_urls":
                " | ".join(
                    job_urls
                )
        })

    result = pd.DataFrame(
        rows
    )

    if not result.empty:

        result = result.sort_values(
            by=[
                "icp_score",
                "relevant_job_count"
            ],
            ascending=[
                False,
                False
            ]
        )

    return result


# ============================================================
# SAVE ONLY QUALIFIED DATA
# ============================================================

def save_results(
    qualified_jobs,
    companies_df
):

    os.makedirs(
        "output",
        exist_ok=True
    )

    # --------------------------------------------------------
    # Only qualified jobs go into this file.
    # --------------------------------------------------------

    qualified_jobs_file = (
        "output/qualified_jobs.csv"
    )

    pd.DataFrame(
        qualified_jobs
    ).to_csv(
        qualified_jobs_file,
        index=False,
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # Main company list
    # --------------------------------------------------------

    companies_file = (
        "output/icp_companies.csv"
    )

    companies_df.to_csv(
        companies_file,
        index=False,
        encoding="utf-8"
    )

    print(
        "\nSaved qualified jobs:"
    )

    print(
        qualified_jobs_file
    )

    print(
        "\nSaved final ICP company list:"
    )

    print(
        companies_file
    )


# ============================================================
# MAIN
# ============================================================

def main():

    config = load_config()

    keywords = config.get(
        "job_keywords",
        []
    )

    if not keywords:

        raise RuntimeError(
            "No job_keywords found in config.yaml"
        )

    print("\n")
    print(
        "#" * 80
    )

    print(
        "# ICP COMPANY RESEARCH ENGINE"
    )

    print(
        "#" * 80
    )

    print(
        "\nHard filters:"
    )

    print(
        "Employees:",
        config[
            "icp"
        ][
            "min_employees"
        ],
        "-",
        config[
            "icp"
        ][
            "max_employees"
        ]
    )

    print(
        "Finance people:",
        config[
            "icp"
        ][
            "min_finance_employees"
        ],
        "+"
    )

    print(
        "\nNaukri pages per keyword:",
        MAX_PAGES_PER_KEYWORD
    )

    print(
        "\nIMPORTANT:"
    )

    print(
        "Only Naukri result-card data is read"
        " before company filtering."
    )

    print(
        "Full job pages are opened ONLY"
        " after both hard filters pass."
    )

    # --------------------------------------------------------
    # In-memory cache.
    #
    # Same company appearing across multiple keywords/pages
    # is checked only once per run.
    # --------------------------------------------------------

    company_cache = {}

    # --------------------------------------------------------
    # Qualified job records only
    # --------------------------------------------------------

    qualified_jobs = []

    # Prevent opening the same qualified job twice
    seen_job_urls = set()

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=HEADLESS
        )

        context = browser.new_context()

        page = context.new_page()

        # ----------------------------------------------------
        # SEARCH EVERY KEYWORD
        # ----------------------------------------------------

        for keyword in keywords:

            if (
                MAX_QUALIFIED_JOBS > 0
                and len(qualified_jobs)
                >= MAX_QUALIFIED_JOBS
            ):

                print(
                    "\nQualified-job limit reached."
                )

                break

            try:

                search_naukri_keyword(
                    page,
                    keyword,
                    config,
                    company_cache,
                    qualified_jobs,
                    seen_job_urls
                )

            except Exception as error:

                print(
                    "\nERROR for keyword:",
                    keyword
                )

                print(
                    error
                )

        context.close()

        browser.close()

    # --------------------------------------------------------
    # AGGREGATE
    # --------------------------------------------------------

    companies_df = aggregate_companies(
        qualified_jobs
    )

    if companies_df.empty:

        print("\n")
        print(
            "=" * 80
        )

        print(
            "NO COMPANIES PASSED THE HARD FILTERS."
        )

        print(
            "=" * 80
        )

        return

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_results(
        qualified_jobs,
        companies_df
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print("\n")
    print(
        "=" * 80
    )

    print(
        "FINAL ICP RESULTS"
    )

    print(
        "=" * 80
    )

    print(
        "\nUnique companies checked:",
        len(company_cache)
    )

    print(
        "Qualified companies:",
        len(companies_df)
    )

    print(
        "Qualified jobs:",
        len(qualified_jobs)
    )

    preferred_count = len(
        companies_df[
            companies_df[
                "preferred_company_type"
            ] == "YES"
        ]
    )

    print(
        "Qualified + preferred company type:",
        preferred_count
    )

    print(
        "\nTOP QUALIFIED COMPANIES:"
    )

    display_columns = [
        "company",
        "employee_count",
        "finance_people_found",
        "relevant_job_count",
        "company_types",
        "preferred_company_type",
        "icp_score"
    ]

    existing_columns = [
        column
        for column in display_columns
        if column in companies_df.columns
    ]

    print(
        companies_df[
            existing_columns
        ]
        .head(30)
        .to_string(
            index=False
        )
    )

    print("\n")
    print(
        "FINAL FILE:"
    )

    print(
        "output/icp_companies.csv"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()