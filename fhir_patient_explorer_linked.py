"""Small FHIR R4 Patient and Condition explorer.

Read-only public HAPI test-server requests. Never submit real patient data.
Retrieved test-server content is not independently verified as synthetic.
Requires Python 3, requests and pandas.
"""
from datetime import date
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

BASE_URL = "https://hapi.fhir.org/baseR4"
HEADERS = {"Accept": "application/fhir+json"}
PATIENT_LIMIT = 20
MAX_CONDITION_PAGES = 3  # Bound requests; flag incomplete retrieval.
PATIENT_COLUMNS = ["patient_id", "gender", "birth_date", "age", "date_issue"]
CONDITION_COLUMNS = [
    "condition_id", "patient_id", "condition_text", "display",
    "code_system", "condition_code", "clinical_status", "verification_status",
]


def fetch_pages(resource_type, params, max_pages=1, session=None):
    client = session or requests.Session()
    url = f"{BASE_URL}/{resource_type}"
    query = {"_count": 50, **params}
    resources, seen = [], set()
    more = False
    for _ in range(max_pages):
        if url in seen:
            raise ValueError("Repeated pagination URL; stopped to avoid a loop.")
        seen.add(url)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != urlparse(BASE_URL).netloc:
            raise ValueError("Pagination points outside the configured HTTPS server.")
        response = client.get(url, params=query, headers=HEADERS, timeout=30)
        response.raise_for_status()
        bundle = response.json()
        if bundle.get("resourceType") != "Bundle":
            raise ValueError("Expected a FHIR search Bundle.")
        resources.extend(
            entry["resource"] for entry in bundle.get("entry", [])
            if entry.get("resource", {}).get("resourceType") == resource_type
        )
        next_link = next((link.get("url") for link in bundle.get("link", [])
                          if link.get("relation") == "next"), None)
        more = bool(next_link)
        if not next_link:
            break
        url = urljoin(url, next_link)
        query = None  # The next link already contains its search parameters.
    return resources, more


def flatten_patients(patients):
    rows = []
    today = date.today()
    for patient in patients:
        birth, age, issue = patient.get("birthDate"), None, ""
        if not birth:
            issue = "missing"
        elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth):
            issue = "partial or invalid date"
        else:
            try:
                dob = date.fromisoformat(birth)
                if dob > today:
                    issue = "future date"
                else:
                    age = today.year - dob.year - (
                        (today.month, today.day) < (dob.month, dob.day)
                    )
            except ValueError:
                issue = "invalid date"
        rows.append({"patient_id": patient.get("id"),
                     "gender": patient.get("gender") or "unknown",
                     "birth_date": birth, "age": age, "date_issue": issue})
    return pd.DataFrame(rows, columns=PATIENT_COLUMNS)


def patient_id_from_reference(reference):
    """Accept local relative or same-server absolute Patient references only."""
    parsed = urlparse(reference)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != urlparse(BASE_URL).netloc:
            return None
        prefix = urlparse(BASE_URL).path.rstrip("/") + "/"
        if not parsed.path.startswith(prefix):
            return None
        path = parsed.path[len(prefix):]
    else:
        path = parsed.path
    if parsed.query or parsed.fragment:
        return None
    match = re.fullmatch(r"Patient/([A-Za-z0-9.-]+)(?:/_history/[A-Za-z0-9.-]+)?", path)
    return match.group(1) if match else None


def status_code(condition, field):
    coding = condition.get(field, {}).get("coding") or []
    return next((item.get("code") for item in coding if item.get("code")), None)


def flatten_conditions(conditions):
    rows = []
    for condition in conditions:
        code = condition.get("code", {})
        reference = condition.get("subject", {}).get("reference", "")
        for coding in code.get("coding") or [{}]:
            rows.append({
                "condition_id": condition.get("id"),
                "patient_id": patient_id_from_reference(reference),
                "condition_text": code.get("text"),
                "display": coding.get("display"),
                "code_system": coding.get("system"),
                "condition_code": coding.get("code"),
                "clinical_status": status_code(condition, "clinicalStatus"),
                "verification_status": status_code(condition, "verificationStatus"),
            })
    return pd.DataFrame(rows, columns=CONDITION_COLUMNS)


def age_group(age):
    if pd.isna(age):
        return "unknown"
    for upper, label in [(18, "0-17"), (35, "18-34"), (50, "35-49"), (65, "50-64")]:
        if age < upper:
            return label
    return "65+"


def link_tables(patients, conditions):
    # Avoid matching two missing IDs, and retain every patient in the cohort.
    patients = patients.dropna(subset=["patient_id"]).drop_duplicates("patient_id")
    conditions = conditions.dropna(subset=["patient_id"])
    return patients.merge(conditions, on="patient_id", how="left", validate="one_to_many")


def main():
    output = Path(__file__).resolve().parent / "output_linked"
    output.mkdir(exist_ok=True)
    with requests.Session() as session:
        print("Fetching a bounded Condition sample first...", flush=True)
        sample, more_conditions = fetch_pages("Condition", {"_count": 50},
                                             max_pages=2, session=session)
        # Select unique local Patient references from these Condition records.
        ids = list(dict.fromkeys(
            pid for condition in sample
            if (pid := patient_id_from_reference(
                condition.get("subject", {}).get("reference", "")))
        ))[:PATIENT_LIMIT]
        if not ids:
            raise ValueError("No supported local Patient references in this sample.")
        resources, coverage = [], []
        for index, patient_id in enumerate(ids, start=1):
            print(f"Fetching referenced patient {index}/{len(ids)}...", flush=True)
            try:
                response = session.get(f"{BASE_URL}/Patient/{patient_id}",
                                       headers=HEADERS, timeout=30)
                response.raise_for_status()
                patient = response.json()
                if patient.get("resourceType") != "Patient" or patient.get("id") != patient_id:
                    raise ValueError("Returned resource does not match the requested Patient.")
                resources.append(patient)
                coverage.append({"patient_id": patient_id,
                                 "patient_fetch_status": "retrieved", "error": ""})
            except (requests.RequestException, ValueError) as exc:
                coverage.append({"patient_id": patient_id,
                                 "patient_fetch_status": "failed",
                                 "error": type(exc).__name__})
        patients = flatten_patients(resources).drop_duplicates("patient_id")
        patients["age_group"] = patients["age"].map(age_group)
        conditions = flatten_conditions(sample).drop_duplicates()
        cohort = set(patients["patient_id"])
        matched = conditions[conditions["patient_id"].isin(cohort)].copy()
        unmatched = conditions[~conditions["patient_id"].isin(cohort)].copy()
        linked = link_tables(patients, matched)
        linked["condition_scope"] = "bounded Condition sample only"
        coverage_df = pd.DataFrame(coverage)
        coded = matched.dropna(subset=["code_system", "condition_code"])
        summary = coded.groupby(["code_system", "condition_code"]).agg(
            unique_patients=("patient_id", "nunique"),
            condition_records=("condition_id", "nunique"),
            example_display=("display", "first"),
        ).reset_index().sort_values("unique_patients", ascending=False)
        quality = pd.DataFrame({
            "field": ["birth_date", "usable_age", "gender", "coded_condition_rows"],
            "missing_or_unknown": [patients["birth_date"].isna().sum(),
                                   patients["age"].isna().sum(),
                                   patients["gender"].eq("unknown").sum(),
                                   (matched["code_system"].isna() |
                                    matched["condition_code"].isna()).sum()],
        })
        for name, table in [("patients", patients), ("condition_codings", matched),
                            ("patients_with_conditions", linked), ("condition_summary", summary),
                            ("retrieval_coverage", coverage_df), ("data_quality", quality),
                            ("unmatched_condition_codings", unmatched)]:
            table.to_csv(output / f"{name}.csv", index=False)
        print(f"\nCondition resources sampled: {len(sample)}")
        print(f"Referenced Patient IDs selected: {len(ids)}")
        print(f"Patients successfully retrieved: {len(patients)}")
        print(f"Matched Condition resources: {matched['condition_id'].nunique()}")
        print(f"Joined rows: {len(linked)} (multiple conditions/codings can repeat a patient).")
        print("\nPatient retrieval status:\n", coverage_df["patient_fetch_status"].value_counts().to_string())
        print("\nTop code groups in the linked sample:\n", summary.head(10).to_string(index=False))
        print(f"\nCSV files saved in: {output}")
        print(f"More Condition pages available: {more_conditions}")
        print("This sample selects people with recorded conditions; it is not representative.")
        print("These are sampled conditions, not each patient's complete condition history.")
        if patients.empty:
            print("No referenced patients could be retrieved. Review retrieval_coverage.csv.")


if __name__ == "__main__":
    main()
