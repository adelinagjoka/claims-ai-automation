"""
Step 3: Automatic extraction of insurance claim data with an LLM (Google Gemini).

Reads every document in sample_claims/, extracts the key fields as
structured data, and saves the results to output/claims_extracted.csv.
Temporary server errors are retried automatically, documents that were
already extracted successfully are skipped on re-runs, and the run stops
early if the daily quota is used up, saving progress so far.
All documents are fictional.
"""
import os
import json
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-3.8-flash"

INPUT_DIR = Path("sample_claims")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
pd.set_option("display.width", 200)  # show the full results table

MAX_RETRIES = 4                  # attempts per document
RETRYABLE_CODES = {500, 503}     # temporary server errors worth retrying
OUTPUT_FILE = OUTPUT_DIR / "claims_extracted.csv"


class QuotaExhausted(Exception):
    """Raised when the API quota is used up, so retrying makes no sense."""

FIELDS = [
    "policy_number",
    "customer_name",
    "incident_date",
    "claim_type",
    "description_summary",
    "estimated_amount_eur",
    "contact_email",
]

SYSTEM_PROMPT = f"""You extract structured data from insurance claim documents.
Return ONLY a valid JSON object with exactly these keys: {", ".join(FIELDS)}.

Rules:
- incident_date in the format YYYY-MM-DD.
- claim_type must be one of: auto, home, health, travel, other.
- estimated_amount_eur as a number, without currency symbols or thousand separators.
- description_summary: one short sentence in English.
- contact_email exactly as written in the document.
- If a value is not in the document, use null. Never invent values."""


def extract_claim(text: str) -> dict:
    """Send one document to the LLM and return the extracted fields."""
    response = client.models.generate_content(
        model=MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",  # forces a JSON answer
            temperature=0,  # consistent, non-creative output
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True  # not needed here; also removes the AFC warning
            ),
        ),
    )
    return json.loads(response.text)


def extract_with_retry(text: str) -> tuple[dict, int]:
    """Call extract_claim, retrying temporary errors with increasing waits.

    Returns the extracted data and the number of attempts used.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return extract_claim(text), attempt
        except errors.APIError as e:
            if e.code == 429:
                raise QuotaExhausted(str(e))
            if e.code in RETRYABLE_CODES and attempt < MAX_RETRIES:
                wait = 10 * attempt  # 10s, 20s, 30s...
                print(f"  Temporary error {e.code}, retrying in {wait}s "
                      f"(attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
            else:
                raise


def load_previous_results() -> pd.DataFrame:
    """Load results from earlier runs, if any."""
    if OUTPUT_FILE.exists():
        return pd.read_csv(OUTPUT_FILE)
    return pd.DataFrame()


def main():
    previous = load_previous_results()
    done = set()
    if not previous.empty:
        done = set(previous.loc[previous["extraction_status"] == "ok", "source_file"])
        previous = previous[previous["source_file"].isin(done)]

    pending = [f for f in sorted(INPUT_DIR.glob("*.txt")) if f.name not in done]
    print(f"{len(done)} already extracted, {len(pending)} to process.\n")

    results = []
    for file in pending:
        print(f"Processing {file.name}...")
        text = file.read_text(encoding="utf-8")
        try:
            data, attempts = extract_with_retry(text)
            data["extraction_status"] = "ok"
            data["attempts"] = attempts
        except QuotaExhausted as e:
            print("  Daily quota used up. Stopping now and saving progress.")
            print("  Run the script again after the quota resets.")
            break
        except Exception as e:
            print(f"  Failed: {e}")
            data = {field: None for field in FIELDS}
            data["extraction_status"] = f"error: {e}"
            data["attempts"] = MAX_RETRIES
        data["source_file"] = file.name
        results.append(data)
        time.sleep(5)  # stay within the free tier's per-minute limit

    df = pd.concat([previous, pd.DataFrame(results)], ignore_index=True)
    # include pending files that were never reached, so nothing is lost
    missing = [f.name for f in sorted(INPUT_DIR.glob("*.txt"))
               if f.name not in set(df.get("source_file", []))]
    if missing:
        df = pd.concat([df, pd.DataFrame(
            [{"source_file": m, "extraction_status": "pending"} for m in missing]
        )], ignore_index=True)
    df = df.sort_values("source_file").reset_index(drop=True)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    ok = (df["extraction_status"] == "ok").sum()
    print(f"\nDone! {ok}/{len(df)} documents extracted successfully.")
    print(f"Results saved to {OUTPUT_FILE}\n")
    print(df[["source_file", "customer_name", "claim_type",
              "estimated_amount_eur", "extraction_status"]])


if __name__ == "__main__":
    main()
