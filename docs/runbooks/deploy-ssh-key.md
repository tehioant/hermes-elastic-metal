# Dedicated CD SSH key

Automatic shipping requires a key that remains authorized for `ops` after
every bootstrap. The old bootstrap regenerated `/home/ops/.ssh/authorized_keys`
from root's first-boot keys, removing any separately installed deployment key.
The new bootstrap also reads one explicit key from `/etc/hermes/deploy.pub`.
It must be a single `restrict ssh-ed25519 ...` line; it is not copied into
root's authorized keys. Removing this file and rerunning bootstrap revokes it.

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

After this PR is merged, rerun `scripts/ship.sh ops@<public-ip>` from your
trusted laptop (using the migrated Terraform state and existing restic
password); it installs the new key synchronization logic. Verify with the
private CI key over the tailnet:

```bash
ssh -i ./hermes-cd -o IdentitiesOnly=yes ops@<tailnet-ip> 'sudo -n true'
```

Retain your personal admin key as a fallback. To revoke CI access, delete
`/etc/hermes/deploy.pub`, rerun the bootstrap from the trusted laptop, and
remove `SSH_DEPLOY_PRIVATE_KEY` in GitHub. Rotate immediately if the private
key or any workflow with access to it is compromised.
