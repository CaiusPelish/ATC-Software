# backend/app/alerts/manager.py

def calculate_alert_severity(
    conflict_severity: str,
    fatigue_score: float
):
    fatigue_score = max(
        0.0, min(fatigue_score, 1.0)
    )

    if conflict_severity == "critical":
        return "critical"

    if (
        conflict_severity == "warning"
        and fatigue_score >= 0.7
    ):
        return "high"

    if conflict_severity == "warning":
        return "medium"

    return "low"