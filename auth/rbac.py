"""Central role-to-scope policy used by IAM token issuance."""

ALL_SCOPES = frozenset(
    {
        "meeting.invoke",
        "meeting.read",
        "meeting.write",
        "travel.invoke",
        "travel.write",
        "birthday.invoke",
        "birthday.write",
        "content.invoke",
    }
)

ROLE_SCOPES = {
    "admin": ALL_SCOPES,
    "user": frozenset(
        {
            "meeting.invoke",
            "travel.invoke",
            "birthday.invoke",
            "content.invoke",
            "meeting.read",
        }
    ),
    "viewer": frozenset({"meeting.read"}),
}


def scopes_for_role(role: str) -> frozenset[str]:
    """Return the permitted IAM scopes for a known role."""
    return ROLE_SCOPES[role]


# agent_id -> scope prefix (e.g. travel-planner -> travel.*)
AGENT_PREFIX = {
    "meeting-planner": "meeting",
    "travel-planner": "travel",
    "birthday-planner": "birthday",
    "content-planner": "content",
}

def scopes_for_agent(role_scopes: list[str], agent_id: str) -> list[str]:
    """User ke scopes mein se sirf us agent ke *.invoke/*.write scopes lautata hai.
    Exchange token kabhi RBAC se zyada scope nahi de sakta, isliye yahan filter hota hai.
    'read' scope agent invoke ke liye nahi, is liye exclude."""
    prefix = AGENT_PREFIX.get(agent_id)
    if not prefix:
        return []
    return [s for s in role_scopes
            if s.startswith(prefix + ".") and not s.endswith(".read")]