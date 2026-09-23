ip := `terraform -chdir=terraform output -raw ipv4`

# SSH into the hermes host as ops (subsequent runs; root login is disabled after bootstrap)
# `exec` replaces the just process with ssh so Warp can detect and Warpify the session.
ssh:
    exec ssh ops@{{ip}}
