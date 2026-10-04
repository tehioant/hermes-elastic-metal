#!/usr/bin/python3
"""Private Wazuh installer. Fixture roots only support non-deploying operations."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
MARKER = "hermes-wazuh-v1\n"
FINGERPRINT = "0DCFCA5547B19D2A6099506096B3EE5F29111145"
PROJECT = "hermes-wazuh"
IMAGES = {
    "manager": "wazuh/wazuh-manager:4.14.8@sha256:412f665c77af5497780d29d0d9f069d7a169c5eca45e9a3a47a587d9cfc99b55",
    "indexer": "wazuh/wazuh-indexer:4.14.8@sha256:6a9c1e3827da627a70859213b3e9cfe43193a492e358953de332b94de33b07de",
    "dashboard": "wazuh/wazuh-dashboard:4.14.8@sha256:dd338d22355e8f0cdf8cc97d5b928539d56061b83c3274a79059e60ad5b8ac14",
}


def run(*args, data=None):
    # Capture output: upstream commands may echo credentials. Never print it.
    result = subprocess.run(args, input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args[0]}; inspect local service logs")
    return result.stdout


def put(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"refusing symlink: {path}")
    content = content.encode() if isinstance(content, str) else content
    fd, temporary = tempfile.mkstemp(prefix=".wazuh-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def guard(root):
    config = root / "etc/hermes-wazuh"
    marker = config / ".managed"
    owned = not marker.is_symlink() and marker.is_file() and marker.read_text() == MARKER
    targets = ["etc/hermes-wazuh", "var/lib/hermes-wazuh", "var/ossec",
               "etc/systemd/system/hermes-wazuh.service",
               "etc/systemd/system/hermes-wazuh-retention.service",
               "etc/systemd/system/hermes-wazuh-retention.timer",
               "etc/systemd/system/wazuh-agent.service.d",
               "etc/apt/sources.list.d/hermes-wazuh.list",
               "etc/apt/preferences.d/hermes-wazuh",
               "usr/share/keyrings/hermes-wazuh.gpg"]
    for name in targets:
        p = root / name
        if p.is_symlink() or (not owned and p.exists()):
            raise RuntimeError(f"refusing unmanaged target: {p}")
    if root == Path("/"):
        for package in ("wazuh-agent", "wazuh-manager", "ossec-hids", "ossec-hids-agent", "ossec-hids-server"):
            pkg = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", package], capture_output=True, text=True)
            if pkg.returncode == 0 and pkg.stdout.strip() != "not-installed" and (not owned or package != "wazuh-agent"):
                raise RuntimeError(f"refusing unmanaged {package} package")
        containers = [json.loads(line) for line in run("docker", "ps", "-a", "--format", "json").decode().splitlines()]
        if not owned and any(PROJECT in c.get("Names", "") or "wazuh" in c.get("Image", "") for c in containers):
            raise RuntimeError("refusing unmanaged Wazuh containers")
        volumes = run("docker", "volume", "ls", "--format", "{{.Name}}").decode().splitlines()
        if not owned and any(v.startswith(PROJECT + "_") for v in volumes):
            raise RuntimeError("refusing unmanaged Wazuh volumes")
    return owned


def preflight(root, owned):
    if root != Path("/"):
        return
    for executable in ("docker", "openssl", "apt-get", "gpg", "systemctl"):
        if not shutil.which(executable):
            raise RuntimeError(f"missing prerequisite: {executable}")
    run("docker", "compose", "version")
    run("docker", "info", "--format", "{{.ServerVersion}}")
    if (os.cpu_count() or 0) < 4:
        raise RuntimeError("Wazuh requires at least four logical CPUs")
    mem = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:")))
    if mem < 8 * 1024 * 1024:
        raise RuntimeError("Wazuh requires 8 GiB available RAM")
    if shutil.disk_usage("/var/lib/docker").free < 50 * 1024**3:
        raise RuntimeError("Wazuh requires 50 GiB free Docker storage")
    if int(Path("/proc/sys/vm/max_map_count").read_text()) < 262144:
        raise RuntimeError("set vm.max_map_count >= 262144 explicitly; installer will not change sysctls")
    if not owned:
        for port in (1514, 55000, 9200, 9300, 8443):
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    raise RuntimeError(f"required loopback port in use: {port}") from None


def certificates(config):
    certs = config / "certs"
    certs.mkdir(mode=0o700, exist_ok=True)
    os.chmod(certs, 0o700)
    ca, key = certs / "root-ca.pem", certs / "root-ca.key"
    if ca.exists() != key.exists():
        raise RuntimeError("incomplete CA; recover existing CA rather than rotating identity")
    if not ca.exists():
        run("openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256", "-days", "3650",
            "-subj", "/CN=Hermes Wazuh CA/O=Wazuh/C=US", "-keyout", str(key), "-out", str(ca))
    os.chmod(key, 0o600)
    for name, cn in (("indexer", "wazuh.indexer"), ("manager", "wazuh.manager"), ("dashboard", "wazuh.dashboard"), ("admin", "admin")):
        leaf, private = certs / (name + ".pem"), certs / (name + ".key")
        if not leaf.exists():
            with tempfile.TemporaryDirectory(dir=config) as tmp:
                csr, ext = Path(tmp) / "request.csr", Path(tmp) / "extensions.cnf"
                if not private.exists():
                    run("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(private))
                run("openssl", "req", "-new", "-key", str(private), "-out", str(csr),
                    "-subj", f"/C=US/L=California/O=Wazuh/OU=Wazuh/CN={cn}")
                put(ext, "subjectAltName=IP:127.0.0.1,DNS:localhost,DNS:" + cn + "\n")
                run("openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(key),
                    "-set_serial", str(secrets.randbits(128)), "-out", str(leaf), "-days", "825", "-sha256", "-extfile", str(ext))
        if not private.exists():
            raise RuntimeError(f"missing private key for existing certificate: {name}")
        run("openssl", "verify", "-CAfile", str(ca), "-verify_ip", "127.0.0.1", str(leaf))
        # Only root can traverse host directory; group 0 lets unprivileged container users read mounted keys.
        os.chmod(private, 0o640)
        os.chmod(leaf, 0o644)
    os.chmod(ca, 0o644)


def render(root):
    import bcrypt
    config = root / "etc/hermes-wazuh"
    if (config / ".identity-established").exists():
        required = ["credentials.json", "client.keys", "certs/root-ca.pem", "certs/root-ca.key"]
        required += ["certs/" + name + suffix for name in ("indexer", "manager", "dashboard", "admin") for suffix in (".pem", ".key")]
        if any(not (config / name).is_file() for name in required):
            raise RuntimeError("incomplete established identity; recover credentials/CA/client keys from secure backup")
    config.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(config, 0o700)
    put(config / ".managed", MARKER)
    credentials = config / "credentials.json"
    if not credentials.exists():
        put(credentials, json.dumps({name: "Aa1-" + secrets.token_hex(22) for name in ("indexer", "dashboard", "api")}) + "\n")
    os.chmod(credentials, 0o600)
    passwords = json.loads(credentials.read_text())
    if not (config / "client.keys").exists():
        put(config / "client.keys", "001 hermes-host 127.0.0.1 " + secrets.token_hex(32) + "\n")
    os.chmod(config / "client.keys", 0o600)
    certificates(config)
    put(config / ".identity-established", MARKER)
    for name in ("agent.xml", "manager.xml", "indexer.yml", "dashboard.yml", "api.yml"):
        put(config / name, (REPO / "config/wazuh" / name).read_bytes(), 0o644)
    users = {"_meta": {"type": "internalusers", "config_version": 2}}
    for user, password, role in (("admin", "indexer", "admin"), ("kibanaserver", "dashboard", None)):
        users[user] = {"hash": bcrypt.hashpw(passwords[password].encode(), bcrypt.gensalt(12)).decode(), "reserved": True}
        if role:
            users[user]["backend_roles"] = [role]
    put(config / "internal_users.yml", json.dumps(users, indent=2), 0o640)
    put(config / "wazuh.yml", json.dumps({"hosts": [{"1513629884013": {
        "url": "https://127.0.0.1", "port": 55000, "username": "wazuh-wui", "password": passwords["api"], "run_as": True}}]}), 0o640)
    volumes = {}

    def volume(name, target):
        volumes[name] = {}
        return {"type": "volume", "source": name, "target": target}

    def bind(name, target):
        return {"type": "bind", "source": str(config / name), "target": target, "read_only": True,
                "bind": {"create_host_path": False}}

    manager_mounts = [volume(name, target) for name, target in (
        ("api", "/var/ossec/api/configuration"), ("etc", "/var/ossec/etc"), ("logs", "/var/ossec/logs"),
        ("queue", "/var/ossec/queue"), ("multigroups", "/var/ossec/var/multigroups"),
        ("integrations", "/var/ossec/integrations"), ("active_response", "/var/ossec/active-response/bin"),
        ("agentless", "/var/ossec/agentless"), ("wodles", "/var/ossec/wodles"),
        ("filebeat_etc", "/etc/filebeat"), ("filebeat_var", "/var/lib/filebeat"))]
    manager_mounts += [bind(name, target) for name, target in (
        ("manager.xml", "/wazuh-config-mount/etc/ossec.conf"), ("api.yml", "/wazuh-config-mount/api/configuration/api.yaml"),
        ("client.keys", "/wazuh-config-mount/etc/client.keys"), ("certs/root-ca.pem", "/etc/ssl/root-ca.pem"),
        ("certs/manager.pem", "/etc/ssl/filebeat.pem"), ("certs/manager.key", "/etc/ssl/filebeat.key"))]
    common = {"network_mode": "host", "restart": "always", "cap_drop": ["NET_RAW", "NET_ADMIN"],
              "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}}
    manager = dict(common, image=IMAGES["manager"], hostname="wazuh.manager", mem_limit="2g", pids_limit=512,
                   volumes=manager_mounts, environment={"INDEXER_URL": "https://127.0.0.1:9200", "INDEXER_USERNAME": "admin",
                    "INDEXER_PASSWORD": passwords["indexer"], "FILEBEAT_SSL_VERIFICATION_MODE": "full",
                    "SSL_CERTIFICATE_AUTHORITIES": "/etc/ssl/root-ca.pem", "SSL_CERTIFICATE": "/etc/ssl/filebeat.pem",
                    "SSL_KEY": "/etc/ssl/filebeat.key", "API_USERNAME": "wazuh-wui", "API_PASSWORD": passwords["api"]},
                   ulimits={"nofile": {"soft": 65536, "hard": 65536}})
    indexer = dict(common, image=IMAGES["indexer"], hostname="wazuh.indexer", mem_limit="3g", pids_limit=512,
                   group_add=["0"], environment={"OPENSEARCH_JAVA_OPTS": "-Xms1g -Xmx1g"},
                   ulimits={"memlock": {"soft": -1, "hard": -1}, "nofile": {"soft": 65536, "hard": 65536}},
                   volumes=[volume("indexer", "/var/lib/wazuh-indexer"), bind("indexer.yml", "/usr/share/wazuh-indexer/config/opensearch.yml"),
                            bind("internal_users.yml", "/usr/share/wazuh-indexer/config/opensearch-security/internal_users.yml")])
    for name, target in (("root-ca.pem", "root-ca.pem"), ("indexer.pem", "wazuh.indexer.pem"), ("indexer.key", "wazuh.indexer.key"),
                         ("admin.pem", "admin.pem"), ("admin.key", "admin-key.pem")):
        indexer["volumes"].append(bind("certs/" + name, "/usr/share/wazuh-indexer/config/certs/" + target))
    dashboard = dict(common, image=IMAGES["dashboard"], hostname="wazuh.dashboard", mem_limit="1g", pids_limit=256,
                     group_add=["0"], environment={"DASHBOARD_USERNAME": "kibanaserver", "DASHBOARD_PASSWORD": passwords["dashboard"],
                     "WAZUH_API_URL": "https://127.0.0.1", "API_USERNAME": "wazuh-wui", "API_PASSWORD": passwords["api"]},
                     volumes=[volume("dashboard_config", "/usr/share/wazuh-dashboard/data/wazuh/config"),
                              volume("dashboard_custom", "/usr/share/wazuh-dashboard/plugins/wazuh/public/assets/custom"),
                              bind("dashboard.yml", "/usr/share/wazuh-dashboard/config/opensearch_dashboards.yml"),
                              bind("wazuh.yml", "/usr/share/wazuh-dashboard/data/wazuh/config/wazuh.yml")])
    for name, target in (("root-ca.pem", "root-ca.pem"), ("dashboard.pem", "wazuh-dashboard.pem"), ("dashboard.key", "wazuh-dashboard-key.pem")):
        dashboard["volumes"].append(bind("certs/" + name, "/usr/share/wazuh-dashboard/certs/" + target))
    put(config / "compose.yml", json.dumps({"name": PROJECT, "services": {
        "wazuh.manager": manager, "wazuh.indexer": indexer, "wazuh.dashboard": dashboard}, "volumes": volumes}, indent=2))
    return config


@contextlib.contextmanager
def block_autostart(hook=Path("/usr/sbin/policy-rc.d")):
    """Preserve the existing inode/symlink and restore it even on apt failure."""
    backup = hook.with_name("policy-rc.d.hermes-wazuh-backup")
    if backup.exists() or backup.is_symlink():
        raise RuntimeError("existing policy-rc.d backup; restore manually before retry")
    existed = hook.exists() or hook.is_symlink()
    if existed:
        hook.rename(backup)
    try:
        put(hook, "#!/bin/sh\nexit 101\n", 0o755)
        yield
    finally:
        hook.unlink(missing_ok=True)
        if existed:
            backup.rename(hook)


def compose(config, *args, data=None):
    return run("docker", "compose", "-p", PROJECT, "-f", str(config / "compose.yml"), *args, data=data)


def deploy(config):
    # Marker already persisted by render: partial apt installs remain ours on retry.
    with block_autostart():
        run("apt-get", "update")
        run("apt-get", "install", "-y", "--no-install-recommends", "ca-certificates", "gnupg", "python3-bcrypt")
        key = urllib.request.urlopen("https://packages.wazuh.com/key/GPG-KEY-WAZUH", timeout=30).read()
        fingerprints = run("gpg", "--batch", "--with-colons", "--show-keys", data=key).decode()
        if "fpr:::::::::" + FINGERPRINT + ":" not in fingerprints:
            raise RuntimeError("Wazuh signing-key fingerprint mismatch")
        put(Path("/usr/share/keyrings/hermes-wazuh.gpg"), run("gpg", "--batch", "--dearmor", data=key), 0o644)
        put(Path("/etc/apt/sources.list.d/hermes-wazuh.list"),
            "deb [signed-by=/usr/share/keyrings/hermes-wazuh.gpg] https://packages.wazuh.com/4.x/apt/ stable main\n", 0o644)
        put(Path("/etc/apt/preferences.d/hermes-wazuh"), "Package: wazuh-agent\nPin: version 4.14.8-1\nPin-Priority: 1001\n", 0o644)
        run("apt-get", "update")
        run("apt-get", "install", "-y", "--no-install-recommends", "wazuh-agent=4.14.8-1")
    run("apt-mark", "hold", "wazuh-agent")
    state = Path("/var/lib/hermes-wazuh")
    state.mkdir(mode=0o700, exist_ok=True)
    os.chmod(state, 0o700)
    (state / "fim-test").mkdir(mode=0o700, exist_ok=True)
    import grp
    gid = grp.getgrnam("wazuh").gr_gid
    for source, target in (("agent.xml", "ossec.conf"), ("client.keys", "client.keys")):
        dest = Path("/var/ossec/etc") / target
        put(dest, (config / source).read_bytes(), 0o640)
        os.chown(dest, 0, gid)
    put(Path("/var/ossec/etc/local_internal_options.conf"),
        "logcollector.remote_commands=0\nwazuh_command.remote_commands=0\n", 0o640)
    os.chown("/var/ossec/etc/local_internal_options.conf", 0, gid)
    put(Path("/etc/systemd/system/wazuh-agent.service.d/hermes-wazuh.conf"),
        "[Unit]\nRequires=hermes-wazuh.service\nAfter=hermes-wazuh.service\n[Service]\nMemoryMax=768M\nRestart=on-failure\n", 0o644)
    put(config / "wazuh-retention.py", (REPO / "scripts/wazuh-retention.py").read_bytes(), 0o700)
    for unit in ("hermes-wazuh.service", "hermes-wazuh-retention.service", "hermes-wazuh-retention.timer"):
        put(Path("/etc/systemd/system") / unit, (REPO / "systemd" / unit).read_bytes(), 0o644)
    compose(config, "config", "--quiet")
    compose(config, "pull")
    run("/var/ossec/bin/wazuh-logcollector", "-t")
    run("/var/ossec/bin/wazuh-syscheckd", "-t")
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "hermes-wazuh.service")
    run("systemctl", "restart", "hermes-wazuh.service")
    # Verify TLS/authenticated indexer before enabling the host agent.
    import base64
    import ssl
    password = json.loads((config / "credentials.json").read_text())["indexer"]
    ctx = ssl.create_default_context(cafile=str(config / "certs/root-ca.pem"))
    request = urllib.request.Request("https://127.0.0.1:9200/_cluster/health", headers={
        "Authorization": "Basic " + base64.b64encode(("admin:" + password).encode()).decode()})
    for attempt in range(90):
        try:
            with urllib.request.urlopen(request, context=ctx, timeout=5) as response:
                health = json.load(response)
            if health["status"] in ("yellow", "green"):
                break
        except (OSError, ValueError):
            pass
        time.sleep(2)
    else:
        raise RuntimeError("indexer TLS/auth health check failed; host agent not started")
    credentials = json.loads((config / "credentials.json").read_text())
    api_request = urllib.request.Request("https://127.0.0.1:55000/security/user/authenticate?raw=true", headers={
        "Authorization": "Basic " + base64.b64encode(("wazuh-wui:" + credentials["api"]).encode()).decode()})
    # Upstream API generates its private self-signed certificate; loopback only, no remote trust bypass.
    private_api_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    private_api_context.check_hostname = False
    private_api_context.verify_mode = ssl.CERT_NONE
    for attempt in range(90):
        try:
            with urllib.request.urlopen(api_request, context=private_api_context, timeout=5) as response:
                token = response.read()
            with urllib.request.urlopen("https://127.0.0.1:8443/", context=ctx, timeout=5) as response:
                dashboard_status = response.status
            if token and dashboard_status == 200:
                break
        except (OSError, ValueError):
            pass
        time.sleep(2)
    else:
        raise RuntimeError("private API/dashboard readiness failed; host agent not started")
    run("systemctl", "enable", "--now", "wazuh-agent.service", "hermes-wazuh-retention.timer")
    run("systemctl", "restart", "wazuh-agent.service")
    for unit in ("hermes-wazuh.service", "wazuh-agent.service", "hermes-wazuh-retention.timer"):
        run("systemctl", "is-active", unit)
        run("systemctl", "is-enabled", unit)
    for service in ("wazuh.manager", "wazuh.indexer", "wazuh.dashboard"):
        cid = compose(config, "ps", "-q", service).decode().strip()
        if not cid or run("docker", "inspect", "-f", "{{.State.Running}}", cid).strip() != b"true":
            raise RuntimeError(f"container not running: {service}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"), help="fixture root; never used for deployment")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--render-only", action="store_true", help="generate private config/TLS without services or packages")
    mode.add_argument("--install", action="store_true", help="explicit opt-in: install and start Wazuh")
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.install and (args.root != Path("/") or os.geteuid() != 0):
        raise RuntimeError("--install requires root and the real filesystem")
    if args.root == Path("/") and os.geteuid() != 0:
        raise RuntimeError("real-root preflight/render requires sudo")
    lock = None
    if args.root == Path("/") and (args.render_only or args.install):
        lock = open("/run/lock/hermes-wazuh-install.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    owned = guard(args.root)
    preflight(args.root, owned)
    if args.render_only or args.install:
        config = render(args.root)
        if args.install:
            deploy(config)
        print("Wazuh deployment completed" if args.install else "Wazuh private configuration rendered (no services changed)")
    else:
        print("Wazuh preflight passed (no writes, packages or services changed)")
    if lock:
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, ImportError) as exc:
        print(f"Wazuh: {exc}", file=sys.stderr)
        sys.exit(1)
