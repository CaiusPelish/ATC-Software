# backend/app/speech/parser.py

def parse_clearance(text: str):
    return {
        "callsign": None,
        "action": None,
        "destination": None,
        "raw_text": text
    }