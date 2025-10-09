# cartwatch/validators.py
import re

def normalize_indian_phone(phone: str) -> str:
    """Strip spaces/+91/0 etc, keep last 10 digits if valid."""
    if not phone:
        return ""
    digits = re.sub(r"\D+", "", phone)
    # If starts with '91' and length 12, trim to last 10
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[-10:]
    # If starts with 0 and length 11, trim to last 10
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[-10:]
    return digits

def is_valid_10_digit_indian_phone(phone: str) -> bool:
    digits = normalize_indian_phone(phone)
    return len(digits) == 10 and digits[0] in "6789"
