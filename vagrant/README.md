# Deploy REST API on Bare Metal (via Vagrant on EC2)

This deploys the Student API stack (2 API containers, 1 Postgres, 1 Nginx
load balancer) inside a Vagrant box, treating that box as "production." The
Vagrant box runs on an EC2 instance rather than a laptop, so it uses the
**libvirt/KVM** provider instead of VirtualBox — VirtualBox needs direct
hardware VT-x/AMD-V passthrough, which it can't reliably get when nested
inside another VM. KVM works instead, using EC2's nested virtualization
support (Step 1 below).

## Prerequisites

- EC2 instance type must support nested virtualization: `c8i`, `m8i`, `r8i`
  (or their `-flex` variants). This was tested on `m8i.2xlarge` (8 vCPUs:
  4 cores × 2 threads by default).
- **AWS CLI v2.33.21 or newer.** The `--nested-virtualization` flag on
  `modify-instance-cpu-options` is a recent addition — anything older
  (including the commonly pre-installed v2.24.x) will fail with
  `Unknown options: --nested-virtualization`. Check your version:

  ```bash
  aws --version
  ```

  If it's older, update via the official installer
  (https://awscli.amazonaws.com/AWSCLIV2.msi on Windows, or the Linux/Mac
  installer from AWS docs) and reopen your terminal before continuing.

## Step 1 — Enable nested virtualization on the EC2 instance

Nested virt is not on by default even on eligible instance types — it's an
explicit CPU option, and it can only be changed while the instance is
**stopped**.

Check current status:

```bash
aws ec2 describe-instances \
  --instance-ids <your-instance-id> \
  --query "Reservations[].Instances[].{Type:InstanceType,NestedVirt:CpuOptions.NestedVirtualization}" \
  --output table
```

If it shows `None` or `disabled`:

```bash
aws ec2 stop-instances --instance-ids <your-instance-id>
aws ec2 wait instance-stopped --instance-ids <your-instance-id>

aws ec2 modify-instance-cpu-options \
  --instance-id <your-instance-id> \
  --core-count 4 \
  --threads-per-core 2 \
  --nested-virtualization enabled

aws ec2 start-instances --instance-ids <your-instance-id>
```

Note the flag shape: `--nested-virtualization` is its own top-level flag on
`modify-instance-cpu-options`, **not** nested inside a `--cpu-options`
key=value string (that syntax is only for `run-instances`, when launching a
brand-new instance).

For `m8i.2xlarge` specifically, `--core-count 4 --threads-per-core 2` keeps
you at the instance's full 8 vCPUs while just turning nested virt on. Adjust
these two numbers to match your actual instance type's default core/thread
layout if you're on something else.

Confirm it stuck:

```bash
aws ec2 describe-instances \
  --instance-ids <your-instance-id> \
  --query "Reservations[].Instances[].{Type:InstanceType,NestedVirt:CpuOptions.NestedVirtualization}" \
  --output table
```
Should show `enabled`.

Then verify from **inside** the instance (SSH in first) that the CPU flag
is actually visible to the guest OS, not just set at the AWS API level:

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo   # should print > 0
```

## Step 2 — Open the Security Group for Postman access

Postman will hit the API **directly** on the EC2 instance's public IP (no
SSH tunnel), so add an inbound rule on the instance's Security Group:

| Type       | Port | Source                          |
|------------|------|----------------------------------|
| Custom TCP | 8080 | Your IP (or 0.0.0.0/0 for testing) |

```bash
aws ec2 authorize-security-group-ingress \
  --group-id <your-sg-id> \
  --protocol tcp --port 8080 \
  --cidr <your-ip>/32
```

## Step 3 — Install KVM/libvirt + Vagrant on the EC2 host

```bash
sudo apt update
sudo apt install -y qemu-system-x86 libvirt-daemon-system libvirt-clients \
  virtinst bridge-utils build-essential ruby-dev libvirt-dev \
  ruby-libvirt zlib1g-dev
```
Use `qemu-system-x86`, not `qemu-kvm` — the latter is a virtual package name
that doesn't resolve on newer Ubuntu releases and apt will refuse to
install it, silently skipping everything else on that same command line
(including `build-essential`, which you need later for the Vagrant plugin
to compile). If in doubt, re-run the install command above and confirm
every package actually installs — don't assume it worked just because apt
returned without a hard error on an earlier attempt.

```bash
sudo usermod -aG libvirt,kvm $USER
# fully log out and reconnect (a new SSH session, not just `exit` in the
# same window) for the group change to take effect - `newgrp` alone is not
# reliably sufficient here
```
`libvirt-daemon-system` must already be installed before this command runs,
otherwise the `libvirt` group won't exist yet and `usermod` will fail with
`group 'libvirt' does not exist`.

```bash
# Vagrant, from HashiCorp's apt repo
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y vagrant

vagrant plugin install vagrant-libvirt
```
This plugin compiles a native Ruby gem extension, which needs `gcc` — that
comes from `build-essential` above. If this step fails partway (e.g.
`gcc: No such file or directory`), confirm `build-essential` is actually
installed, then clean up the half-built gem before retrying:
```bash
rm -rf ~/.vagrant.d/gems/*/gems/racc-* ~/.vagrant.d/gems/*/extensions/*/*/racc-*
vagrant plugin install vagrant-libvirt
```

Sanity check — should list (probably empty) without erroring:
```bash
virsh -c qemu:///system list --all
```

## Step 4 — Point docker-compose.yml at your image

The API image is already built and pushed to a registry, so `docker-compose.yml`
pulls it rather than building it locally. Edit the `image:` line for both
`app1` and `app2`:
```yaml
image: <your-registry>/student-api:latest
```
If it's a private registry, log in on the EC2 host before `vagrant up`,
since the provisioning step runs `docker compose pull`:
```bash
docker login <your-registry>
```

## Step 5 — Bring the box up

```bash
cd vagrant
vagrant up --provider=libvirt
```

This will:
1. Create the box with 4 vCPUs / 4GB RAM (via KVM)
2. Run `scripts/bootstrap.sh` inside it to install Docker + Compose plugin
3. Run `docker compose pull` + `docker compose up -d` inside it, bringing up:
   - `student-postgres` (Postgres 17)
   - `student-api-1`, `student-api-2` (two API replicas, internal-only, no host port)
   - `student-nginx` (load balancer, published on `:8080`)
4. Wait for `app1`'s healthcheck to pass, then run
   `alembic upgrade head` against it once, applying DB migrations.

First boot can genuinely take a few minutes (cloud-init + package installs
inside the guest) even with KVM acceleration working correctly, so don't
assume something's wrong just because it sits on "Waiting for machine to
boot" for a while. If it's still retrying SSH past ~5 minutes, see
Troubleshooting below.

Check it's healthy:

```bash
vagrant ssh -c "docker compose -f /vagrant/docker-compose.yml ps"
vagrant ssh -c "curl -s http://localhost:8080/api/v1/health"
```

## Step 6 — Test with Postman

Point your Postman collection's base URL / environment variable at:

```
http://<EC2-PUBLIC-IP>:8080
```
No SSH tunnel, no port forwarding on your laptop needed — this hits the EC2
instance directly, which forwards straight into the Vagrant box's Nginx,
which load-balances across `app1`/`app2`.
Every request should come back `200`. To confirm the load balancing is
actually alternating between the two API containers, watch logs from both
in separate terminals while the collection runs:

```bash
vagrant ssh -c "docker logs -f student-api-1"
vagrant ssh -c "docker logs -f student-api-2"
```

## Does the Vagrantfile expose all ports, or just 8080?

**Just 8080.** The only `forwarded_port` line in the Vagrantfile is:

```ruby
config.vm.network "forwarded_port", guest: 8080, host: 8080, host_ip: "0.0.0.0"
```

That's the only port bridged from the box out to the EC2 host (and from
there, out to the internet via the Security Group). Postgres (`5432`) and
the two API containers (`8000`) are declared with `expose:` in
`docker-compose.yml`, not `ports:` — `expose` only makes a port reachable
between containers on the compose network, it does not publish it to the
box's host interface at all. So even without the Vagrantfile's port
forwarding, those two would still be unreachable from outside the box.
`host_ip: "0.0.0.0"` on the 8080 line specifically controls which host
interface the forward binds to (all interfaces, vs. the default of
loopback-only) — it doesn't add any additional ports.

There is intentionally **no `private_network` line** in this Vagrantfile.
An earlier version added one with a static IP for host-side debugging
(`192.168.121.x`), but that subnet collides with `vagrant-libvirt`'s own
default management network — the guest ended up with two interfaces on the
same subnet, which broke routing and made SSH (and everything else)
unreachable even though the domain showed as `running`. If you want that
kind of direct-IP access back, use a genuinely distinct subnet (e.g.
`192.168.50.x`), not `192.168.121.x`.

## Nginx config explained (`nginx/nginx.conf`)

```nginx
upstream student_api_upstream {
    server app1:8000 max_fails=3 fail_timeout=10s;
    server app2:8000 max_fails=3 fail_timeout=10s;
}
```
- `upstream` defines the pool of backends Nginx load-balances across.
  `app1`/`app2` resolve via Docker's internal DNS (compose service names),
  hence port `8000` here — the container's internal port, not anything
  published to the host.
- No `weight` is set on either server, so Nginx uses **round-robin** by
  default: alternating requests between `app1` and `app2`.
- `max_fails=3 fail_timeout=10s` — if a backend fails 3 consecutive proxied
  requests, Nginx marks it "down" for 10 seconds and stops routing to it,
  automatically retrying after that window. This is what gives you basic
  failover if one API container dies or is mid-restart.

```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://student_api_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
```
- Nginx listens on `80` **inside its own container** — this is then mapped
  to `8080` on the box's host interface via `ports: ["8080:80"]` in
  `docker-compose.yml`. That's a separate mapping from the Vagrantfile's
  `forwarded_port` — three layers total: EC2 host `8080` → Vagrant box
  `8080` → Nginx container `80`.
- `server_name _;` — catch-all, matches any Host header. Fine here since
  this isn't doing virtual-hosting; there's only one site.
- `proxy_pass http://student_api_upstream;` sends every request to the
  upstream pool defined above, letting Nginx's built-in round-robin decide
  which of `app1`/`app2` handles it.
- The `proxy_set_header` lines forward the original client IP, protocol,
  and host to the backend, since by default the backend would otherwise
  only see Nginx's internal IP as the request source — useful for logging
  and any endpoint that behaves differently over HTTP vs HTTPS.
- `proxy_connect_timeout` / `proxy_read_timeout` bound how long Nginx waits
  on a backend before giving up and (per `max_fails` above) potentially
  marking it down.

```nginx
    location /api/v1/health {
        proxy_pass http://student_api_upstream;
        access_log off;
    }
}
```
- Same upstream, but `access_log off` — health check hits (Docker's own
  healthcheck polls this every 10s per container, so 2 containers = a
  steady trickle of requests) are excluded from Nginx's access log so they
  don't drown out real traffic when you're reading logs during testing.

## Day-to-day commands (no Makefile — plain docker compose / vagrant)

| Action                          | Command                                                        |
|----------------------------------|-----------------------------------------------------------------|
| Pull latest image + restart      | `vagrant ssh -c "cd /vagrant && docker compose pull && docker compose up -d"` |
| Run migrations manually          | `vagrant ssh -c "cd /vagrant && docker compose exec -T app1 alembic upgrade head"` |
| View logs (all services)         | `vagrant ssh -c "cd /vagrant && docker compose logs -f"`       |
| Check container status           | `vagrant ssh -c "cd /vagrant && docker compose ps"`             |
| Stop the stack (keep the box)    | `vagrant ssh -c "cd /vagrant && docker compose down"`           |
| Tear down completely (incl. DB volume) | `vagrant ssh -c "cd /vagrant && docker compose down -v"`  |
| Destroy the Vagrant box entirely | `vagrant destroy -f`                                            |
| Re-sync repo changes into the box | `vagrant rsync`                                                 |

Note on `synced_folder`: libvirt doesn't support VirtualBox-style shared
folders, so this Vagrantfile uses `rsync` (one-way, host → guest). It syncs
automatically on `vagrant up`/`vagrant reload`, or manually via
`vagrant rsync`. If you edit files (e.g. `docker-compose.yml`,
`nginx/nginx.conf`) on the EC2 host after the box is already up, run
`vagrant rsync` before re-running `docker compose up -d`.

## Troubleshooting

- **`Unknown options: --nested-virtualization`** on
  `modify-instance-cpu-options` — your AWS CLI is too old. See
  Prerequisites above; update to v2.33.21+.
- **`Package 'qemu-kvm' has no installation candidate`** — use
  `qemu-system-x86` instead (Step 3). If this failed silently earlier in a
  multi-package `apt install` line, re-run the full install command and
  confirm every package landed, since apt skips the rest of that line on a
  hard failure like this.
- **`usermod: group 'libvirt' does not exist`** — `libvirt-daemon-system`
  didn't actually get installed (usually a symptom of the `qemu-kvm`
  failure above). Reinstall the full package list from Step 3, then retry
  `usermod`.
- **`vagrant plugin install vagrant-libvirt` fails with `gcc: No such file
  or directory`** — `build-essential` is missing, same root cause as
  above. Install it, clean the half-built gem, and retry (commands in
  Step 3).
- **`Could not open /dev/kvm: Permission denied`** — your user isn't
  actually in the `kvm`/`libvirt` groups yet in this shell session. Fully
  log out and back in (not just `newgrp`), then retry.
- **`vagrant up` hangs on "Waiting for domain to get an IP address" or
  keeps retrying "Connection refused" on SSH for 5+ minutes** — check, in
  this order:
  1. Is nested virt genuinely active at the CPU level (not just the AWS
     flag)? `egrep -c '(vmx|svm)' /proc/cpuinfo` on the EC2 host.
  2. Is the domain actually using KVM, not falling back to emulation?
     `virsh -c qemu:///system dumpxml vagrant_default | grep "<domain type"`
     — should say `type='kvm'`, not `type='qemu'`.
  3. Any custom `private_network` static IP in the Vagrantfile colliding
     with `192.168.121.0/24` (libvirt's default management network)? This
     was the actual root cause hit during this setup — see the note in
     "Does the Vagrantfile expose all ports" above. Fix: remove that line,
     or use a genuinely distinct subnet.
  4. Watch the live boot console for stuck/erroring units:
     `virsh -c qemu:///system console vagrant_default` (`Ctrl+]` to exit).
- **`vagrant status` shows `not created` after a failed `vagrant up`** —
  Vagrant tears down the half-created domain on certain failure paths
  (e.g. SSH retry timeout). This is expected; just fix the underlying
  cause and run `vagrant up --provider=libvirt` again.
- **Postman gets connection refused / timeout from your laptop** — check
  the Security Group rule (Step 2) is actually applied, and that the
  Vagrantfile's forwarded port has `host_ip: "0.0.0.0"` (default is
  loopback-only, which would only be reachable via SSH tunnel).
- **`docker compose pull` fails with unauthorized** — you're pulling from a
  private registry and aren't logged in on the box; see Step 4.
- **One container looks unhealthy in `docker compose ps`** — check its
  logs specifically: `docker compose logs app1` (or `app2`, `postgres`).
- **Migration step fails / `alembic: command not found`** — confirm
  `alembic` is on `PATH` inside your image and that `alembic.ini` sits at
  the container's working directory. If it's elsewhere, run it with an
  explicit working directory:
  `docker compose exec -T -w /path/to/app app1 alembic upgrade head`.