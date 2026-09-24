# Dedicated CD SSH key

Automatic shipping requires keys that remain authorized for `ops` after
every bootstrap. The old bootstrap stripped restrictions from root's
`authorized_keys` while copying them to `ops`: a `from=` or `command=`-limited
root key thereby acquired unrestricted `ops` access and passwordless sudo.
The replacement trusts only the **explicit** Terraform `ssh_public_key` for
personal admin access, plus one restricted CI key from `/etc/hermes/deploy.pub`.
The admin key is persisted at `/etc/hermes/ops.pub` for manual reruns; root's
`authorized_keys` is never promoted. Removing the CI key file and rerunning
bootstrap revokes it.

On a trusted computer, generate a **dedicated** unencrypted automation key
(`ssh-keygen -t ed25519 -f ./hermes-cd -C hermes-cd -N ''`). Keep the private
key out of the repo, state, and chat. Put its full private key in the GitHub
`production` environment secret `SSH_DEPLOY_PRIVATE_KEY` using the GitHub UI.
Only reviewed merges to `main` should be allowed to use production secrets:
the CI key can effectively become root via the existing `ops` passwordless
sudo. Restrict the production environment to deployments from `main`.

On the server, from your existing verified admin SSH session, create
`/etc/hermes/deploy.pub` (mode 0600, root-owned) with exactly one line:

```text
restrict ssh-ed25519 YOUR_PUBLIC_KEY_BLOB hermes-cd
```

Do not use the public **server host key** here; this file holds the CI
**client** public key. `restrict` disables forwarding and PTY but permits
noninteractive commands needed by `ship.sh`.

After the deploy-key PR **and** the explicit-admin-key PR are merged, verify
that Terraform's `ssh_public_key` is the trusted personal key you can use to
log in as `ops` (and no key only intended for restricted root access). The
bootstrap replaces `ops` authorized keys with that key and the optional CI
key. Rerun `scripts/ship.sh ops@<public-ip>` from your trusted laptop with the
migrated Terraform state and existing restic password. Keep the current admin
session and provider console available; test a new personal `ops` session
before closing either fallback. Then verify the CI key over the tailnet:

```bash
ssh -i ./hermes-cd -o IdentitiesOnly=yes ops@<tailnet-ip> 'sudo -n true'
```

Retain your personal admin key as a fallback. To revoke CI access, delete
`/etc/hermes/deploy.pub`, rerun the bootstrap from the trusted laptop, and
remove `SSH_DEPLOY_PRIVATE_KEY` in GitHub. Rotate immediately if the private
key or any workflow with access to it is compromised.
