# SSH firewall cutover for automatic shipping

`bootstrap.sh` now reconciles managed SSH rules without `ufw reset`. It keeps
existing connections while adding the new admin CIDRs and a TCP/22 rule on
`tailscale0`, then removes obsolete *recorded* admin CIDRs. It does not remove
unrelated UFW rules; audit those separately. The `tailscale0` rule is private
interface-only; keep public SSH limited to the existing `admin_cidrs`.

## One-time preflight on the existing host

The old bootstrap configured an active UFW but did not record its rules. The
new script deliberately **fails without changing UFW** until you seed
`/var/lib/hermes-host/ssh-admin-cidrs` with the CIDRs actually allowed by the
old bootstrap. From a trusted SSH session on the server:

```bash
sudo ufw status verbose
sudo install -d -m 0700 /var/lib/hermes-host
sudo sh -c 'umask 077; printf "%s\n" "$1" > /var/lib/hermes-host/ssh-admin-cidrs' _ 'YOUR_CURRENT_ADMIN_CIDRS_COMMA_SEPARATED'
sudo chmod 0600 /var/lib/hermes-host/ssh-admin-cidrs
```

Never create `/etc/hermes` for this: Hermes reads `/etc/hermes/.env`, and a
root-only `/etc/hermes` makes every `hermes` command (gateway included) crash
with `PermissionError`. A file seeded at the old `/etc/hermes/ssh-admin-cidrs`
path is moved automatically on the next bootstrap.

Replace the placeholder with the **exact existing** SSH CIDRs after checking
`ufw status`, not guessed values. Remove unexpected broad SSH or web rules
manually before relying on automation. Confirm the Scaleway console/recovery
path is available and keep a second admin SSH session open for the cutover.

Install Tailscale on the host through its official Linux instructions, tag it
as `tag:metal`, and verify it is online. In your tailnet policy, deny by
default and grant only `tag:ci` → `tag:metal`:22 for deployment. The rule
alone is not an authentication mechanism: the SSH key is checked separately.
Verify SSH to the host's Tailscale address from an authorized device before
shipping through CI. Keep the existing public admin SSH fallback until the
private path has been tested.

After adding Tailscale, run the updated bootstrap once from the trusted
admin session with the same `ADMIN_CIDRS` used by Terraform. Verify
`sudo ufw status verbose` includes only intended public admin rules and
`22/tcp` on `tailscale0`, then test both admin and tailnet SSH in new sessions.
Never broaden `admin_cidrs` to all GitHub runner IPs or `0.0.0.0/0`.

If a deploy fails with “Active UFW has no recorded SSH allowlist,” check live
rules and seed the file as above. If an older CIDR could not be removed, keep
an established SSH session, inspect `ufw status` and reconcile the file with
the actual rules before retrying. Do not use `ufw reset` on a remotely managed
host during automatic deployment.

References: [Tailscale's UFW guide](https://tailscale.com/docs/how-to/secure-ubuntu-server-with-ufw), [GitHub runner security](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners).
