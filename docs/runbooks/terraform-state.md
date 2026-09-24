# Move Terraform state to a locked backend

Terraform state is currently local to the provisioning computer. It contains
Scaleway IAM secrets and, after domain registration, may contain registrant
information and a domain transfer code. **Never commit it, attach it to a PR,
or send it through chat.** The deployment workflow must not start until the
existing state has been migrated and verified.

## One-time bucket setup

Create a **dedicated private** Object Storage bucket in `nl-ams`, separate from
the restic backup bucket. Enable versioning and default SSE-ONE encryption at
rest **before** migrating (enabling it later does not encrypt old versions).
Deny non-TLS access with a bucket policy, while preserving the deployer's
required access. Do not enable Object Lock retention on this bucket: Terraform
must be able to delete its temporary `.tflock` object. Record the bucket name
as a repository/environment variable, not a secret. Use separate, narrowly
scoped Object Storage credentials for state access; never use the restic IAM
key. The key needs bucket listing, state read/write, and lockfile
read/write/delete. Scaleway Object Storage supports the conditional writes
needed for Terraform's `use_lockfile` locking.

## Migrate on the computer holding the real local state

Before switching to the new backend, make a private encrypted backup of
`terraform/terraform.tfstate` (and any `.backup` file) outside the repository.
In the **old local-state checkout**, verify the state is not empty and includes
`scaleway_baremetal_server.this`; record its lineage, serial and resource
addresses without printing the state or its secret outputs:

```bash
terraform -chdir=terraform state list
terraform -chdir=terraform state pull | python3 -c 'import json,sys; s=json.load(sys.stdin); print("lineage:",s["lineage"],"serial:",s["serial"],"resources:",len(s["resources"]))'
```

Have your normal `SCW_*` provider credentials and dedicated S3-compatible
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for the state bucket in your
local environment. Do not pass credentials with `-backend-config` or store
them in `.tf` files: Terraform caches backend config locally.

After checking out the commit containing `terraform/backend.tf`, run from the
repository root:

```bash
umask 077
: "${STATE_BUCKET:?set the private state bucket name}"
terraform -chdir=terraform init -migrate-state -backend-config="bucket=${STATE_BUCKET}"
terraform -chdir=terraform state list
terraform -chdir=terraform state pull | python3 -c 'import json,sys; s=json.load(sys.stdin); print("lineage:",s["lineage"],"serial:",s["serial"],"resources:",len(s["resources"]))'
terraform -chdir=terraform plan -lock-timeout=10m -out=tfplan
terraform -chdir=terraform show tfplan
```

Confirm that the lineage and managed resource addresses match the pre-migration
state, `state list` still contains the existing server, bucket and IAM
resources, the S3 bucket contains a versioned encrypted state object at
`hermes-elastic-metal/terraform.tfstate`, and the plan has **only** intended
changes. Do not apply a plan that proposes a replacement or duplicates the
existing server. Store the state backup and plan privately; they can contain
secrets. If migration fails, stop and restore from the protected local backup
before attempting another `init`.

All future local applies must initialize against this same bucket. Terraform
acquires a remote `.tflock` for plans and applies; do not use `-lock=false`.
Before enabling merge-triggered deployment, compare the `STATE_BUCKET` and
state credentials intended for the GitHub `production` environment against
this verified backend. On your trusted laptop, use those credentials and run
a **read-only** check against the migrated state (after the deployment guard
PR is available):

```bash
set -o pipefail
: "${STATE_BUCKET:?set the production state bucket}"
: "${EXPECTED_STATE_LINEAGE:?use the recorded authoritative lineage}"
terraform -chdir=terraform init -reconfigure -backend-config="bucket=${STATE_BUCKET}"
terraform -chdir=terraform state pull |
  EXPECTED_STATE_LINEAGE="${EXPECTED_STATE_LINEAGE}" python3 scripts/deployment_guards.py state
```

The GitHub deploy workflow has **no verification-only mode**. Do not dispatch
it to check state: after a successful guard it can plan, apply and ship.
The first production run repeats this state guard before planning, but it
still requires every host/network/credential prerequisite from the CD guide.
