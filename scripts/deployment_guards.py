import json
import os
import sys


def verify_state(document):
    if not document.get("lineage"):
        raise ValueError("Remote state has no lineage")
    resources = document.get("resources", [])
    required = (
        ("scaleway_baremetal_server", "this", "server"),
        ("scaleway_object_bucket", "backup", "backup bucket"),
    )
    for resource_type, name, label in required:
        if not any(
            resource.get("mode", "managed") == "managed"
            and resource.get("type") == resource_type
            and resource.get("name") == name
            and resource.get("instances")
            for resource in resources
        ):
            raise ValueError(f"Existing {label} missing from remote state")


def verify_plan(document):
    if document.get("complete") is not True or document.get("errored") is not False:
        raise ValueError("Plan is not complete or has errors")
    changes = document.get("resource_changes", [])
    if not isinstance(changes, list):
        raise ValueError("Plan has invalid resource_changes")
    for resource in changes:
        actions = resource.get("change", {}).get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("Plan change has no actions")
        if "delete" in actions:
            raise ValueError("Plan contains a delete action")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("state", "plan"):
        raise SystemExit("Usage: deployment_guards.py state|plan")
    try:
        document = json.load(sys.stdin)
        if sys.argv[1] == "state":
            if not os.environ.get("EXPECTED_STATE_LINEAGE") or document.get("lineage") != os.environ["EXPECTED_STATE_LINEAGE"]:
                raise ValueError("Unexpected state lineage")
        {"state": verify_state, "plan": verify_plan}[sys.argv[1]](document)
    except (ValueError, TypeError, AttributeError):
        raise SystemExit("Deployment guard failed; refusing to apply") from None
    print("State verified" if sys.argv[1] == "state" else "Plan verified")
