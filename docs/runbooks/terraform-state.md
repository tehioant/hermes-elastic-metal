# Terraform state

State lives in the private Scaleway Object Storage bucket `ante-iac` (`fr-par`)
at `terraform/terraform.tfstate`, locked with a `.tflock` object
(`terraform/backend.tf`). It contains IAM secrets: **never commit it, attach it
to a PR, or share it in chat.**

## Credentials

The S3 backend reads only `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`; `.env.example` sets
them from your Scaleway key:

```bash
export AWS_ACCESS_KEY_ID="$SCW_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$SCW_SECRET_KEY"
```

The key needs bucket listing, state read/write and lockfile
read/write/delete. CI gets the same values from repository secrets.

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

## Bucket setup (once)

Keep the bucket private with versioning and SSE-ONE encryption enabled
**before** uploading state. Do not enable Object Lock: Terraform must delete
its `.tflock`.

## Migrate existing local state

1. Back up `terraform/terraform.tfstate` (and `.backup`) privately, outside
   the repository.
2. Record the local fingerprint:

   ```bash
   terraform -chdir=terraform state pull | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["lineage"], s["serial"], len(s["resources"]))'
   ```

3. If the remote key is empty, migrate; if it already holds state, use
   `-reconfigure` instead and **never** `-migrate-state` over it:

   ```bash
   terraform -chdir=terraform init -migrate-state
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

Always keep locking on: do not use `-lock=false`.
