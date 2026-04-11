import re

def validate_job_id(job_id):
    if not isinstance(job_id, str):
        return False
    if not job_id:
        return False
    if not re.fullmatch(r"[a-zA-Z0-9-]+", job_id):
        return False
    return True

test_cases = [
    ("550e8400-e29b-41d4-a716-446655440000", True),
    ("valid-job-123", True),
    ("Job123", True),
    ("job_123", False),  # Underscore not allowed by my regex [a-zA-Z0-9-]
    ("job 123", False),
    ("../../etc/passwd", False),
    ("job-id; drop table users;", False),
    ("", False),
    (None, False),
    (123, False),
]

for job_id, expected in test_cases:
    result = validate_job_id(job_id)
    assert result == expected, f"Failed for {job_id!r}: expected {expected}, got {result}"

print("All validation test cases passed!")
