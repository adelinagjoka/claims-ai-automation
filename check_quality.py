"""
Step 4: Data quality checks on the extracted claims.

Reads output/claims_extracted.csv, applies business validation rules,
and saves output/claims_quality_report.csv with a status and the list
of issues for every claim. No API calls: this step is pure Python.
"""
import re
from datetime import date
from pathlib import Path

import pandas as pd

INPUT_FILE = Path("output") / "claims_extracted.csv"
OUTPUT_FILE = Path("output") / "claims_quality_report.csv"
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 80)

REQUIRED_FIELDS = [
    "policy_number", "customer_name", "incident_date",
    "claim_type", "estimated_amount_eur", "contact_email",
]
POLICY_PATTERN = re.compile(r"^[A-Z]{3}-\d{4}-\d{6}$")
EMAIL_PATTERN = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
POLICY_PREFIX_TO_TYPE = {"AUT": "auto", "HOG": "home", "VIA": "travel", "SAL": "health"}
HIGH_AMOUNT_THRESHOLD = 20000  # claims above this go to manual review


def check_claim(row: pd.Series) -> tuple[str, list[str]]:
    """Return (quality_status, issues) for one claim."""
    if row.get("extraction_status") != "ok":
        return "NOT_EXTRACTED", ["Document not processed yet"]

    blocking, warnings = [], []

    # 1. Completeness: every required field must be present
    for field in REQUIRED_FIELDS:
        if pd.isna(row.get(field)) or str(row.get(field)).strip() == "":
            blocking.append(f"Missing {field}")

    # 2. Policy number: valid format and consistent with the claim type
    policy = row.get("policy_number")
    if pd.notna(policy):
        if not POLICY_PATTERN.match(str(policy)):
            blocking.append(f"Invalid policy format: {policy}")
        else:
            expected = POLICY_PREFIX_TO_TYPE.get(str(policy)[:3])
            if expected and expected != row.get("claim_type"):
                warnings.append(f"Policy type ({expected}) differs from claim type ({row.get('claim_type')})")

    # 3. Email format
    email = row.get("contact_email")
    if pd.notna(email) and not EMAIL_PATTERN.match(str(email)):
        blocking.append(f"Invalid email: {email}")

    # 4. Incident date: must be a real date and not in the future
    incident = row.get("incident_date")
    if pd.notna(incident):
        try:
            if pd.to_datetime(incident).date() > date.today():
                blocking.append(f"Incident date is in the future: {incident}")
        except (ValueError, TypeError):
            blocking.append(f"Unreadable date: {incident}")

    # 5. Amount: positive, and high amounts flagged for manual review
    amount = row.get("estimated_amount_eur")
    if pd.notna(amount):
        if float(amount) <= 0:
            blocking.append(f"Amount must be positive: {amount}")
        elif float(amount) > HIGH_AMOUNT_THRESHOLD:
            warnings.append(f"High amount ({amount:,.0f} EUR), manual review required")

    if blocking:
        return "NEEDS_FIX", blocking + warnings
    if warnings:
        return "REVIEW", warnings
    return "OK", []


def main():
    df = pd.read_csv(INPUT_FILE)
    checks = df.apply(check_claim, axis=1)
    df["quality_status"] = [status for status, _ in checks]
    df["issues"] = ["; ".join(issues) for _, issues in checks]
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print("Quality summary:")
    print(df["quality_status"].value_counts().to_string(), "\n")
    print(df[["source_file", "customer_name", "quality_status", "issues"]].to_string(index=False))
    print(f"\nReport saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
