"""Shared V2 vocabulary; independent of storage, UI, and benchmark labels.

These types describe contracts only. They do not detect issues, assign severity,
calculate confidence, or declare a source to be ground truth.
"""

from enum import StrEnum


class IssueType(StrEnum):
    CONNECTIVITY_BREAK = "CONNECTIVITY_BREAK"
    TURN_RESTRICTION_CONFLICT = "TURN_RESTRICTION_CONFLICT"
    ONEWAY_DIRECTION_CONFLICT = "ONEWAY_DIRECTION_CONFLICT"
    MISSING_OR_CHANGED_ROAD_CANDIDATE = "MISSING_OR_CHANGED_ROAD_CANDIDATE"


class Severity(StrEnum):
    """Business impact level, separate from evidence confidence."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class InputEntity(StrEnum):
    """Detection inputs; benchmark faults and golden answers are excluded."""

    ROAD_SEGMENT = "road_segment"
    ROAD_NODE = "road_node"
    TURN_RESTRICTION = "turn_restriction"
    TRAJECTORY_POINT = "trajectory_point"
    USER_FEEDBACK = "user_feedback"
