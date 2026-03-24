#!/usr/bin/env python3
"""
report_usage.py — Queries Firestore and outputs a tab-delimited usage report.

Usage:
    source set_variables.sh
    python report_usage.py
"""

import os
import sys


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        print("Run: source set_variables.sh", file=sys.stderr)
        raise SystemExit(1)
    return value


def main():
    project_id = get_required_env("PROJECT_ID")

    try:
        from google.cloud import firestore
        db = firestore.Client(project=project_id)
        
        # We query all documents. Sorting by created_at requires a composite index
        # on GCP if we also filter, but since we are just pulling all, it's fine
        # to pull and sort locally to avoid index creation requirements.
        docs = list(db.collection("amr_jobs").stream())
        
        from datetime import datetime, timezone
        
        # Sort documents by created_at, putting missing dates at the end
        def get_sort_key(doc):
            data = doc.to_dict()
            return data.get("created_at") or data.get("expire_at") or datetime.min.replace(tzinfo=timezone.utc)
            
        docs.sort(key=get_sort_key, reverse=True)

        # Print header (tab-delimited)
        header = ["date", "status", "ip_address", "nuc_size_bytes", "prot_size_bytes", "gff_size_bytes", "organism", "job_id"]
        print("\t".join(header))

        for doc in docs:
            data = doc.to_dict()
            
            # 1. Date (try created_at, fallback to N/A)
            created_at = data.get("created_at")
            if hasattr(created_at, "tzinfo"):
                date_str = created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            else:
                date_str = "N/A"
                
            # 2. Status
            status = data.get("status", "Unknown")
            
            # 3. IP Address
            ip_address = data.get("ip_address", "N/A")
            
            # 4. File Sizes (fallback to 0 if they don't exist yet on older docs)
            nuc_size = data.get("nuc_file_size_bytes", 0)
            prot_size = data.get("prot_file_size_bytes", 0)
            gff_size = data.get("gff_file_size_bytes", 0)
            
            # 5. Organism
            params = data.get("parameters", {})
            organism = params.get("organism", "None")
            if not organism or organism.strip() == "":
                organism = "None"
                
            row = [
                str(date_str),
                str(status),
                str(ip_address),
                str(nuc_size),
                str(prot_size),
                str(gff_size),
                str(organism),
                str(doc.id)
            ]
            print("\t".join(row))
            
    except Exception as e:
        print(f"Error reading from Firestore: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
