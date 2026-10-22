#!/usr/bin/env bash
# Runs only on a newly provisioned Ubuntu 24.04 EC2 host.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
set -a
source /etc/classport/host.env
set +a

apt-get update
apt-get install -y ca-certificates curl gnupg unzip util-linux e2fsprogs
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    -o /etc/apt/sources.list.d/caddy-stable.list
chmod a+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
apt-get update
apt-get install -y -o Dpkg::Options::=--force-confold \
    docker-ce docker-ce-cli containerd.io docker-compose-plugin caddy

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o "$scratch/awscli.zip"
unzip -q "$scratch/awscli.zip" -d "$scratch"
"$scratch/aws/install"
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then
    snap install amazon-ssm-agent --classic
fi
systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service

# Attachment happens after instance creation. Identify this exact EBS volume,
# never a device selected by enumeration order (which could be the root disk).
disk="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}"
deadline=$((SECONDS + 300))
until [ -b "$disk" ]; do
    [ "$SECONDS" -lt "$deadline" ] || { echo "Persistent EBS attachment missing" >&2; exit 1; }
    udevadm settle
    sleep 2
done
filesystem=$(blkid -p -s TYPE -o value "$disk" || true)
if [ -z "$filesystem" ]; then
    # A partition table or any other signature is not an empty disk.
    [ -z "$(wipefs --noheadings --output TYPE "$disk")" ] \
        || { echo "Refusing to format a disk with existing signatures" >&2; exit 1; }
    mkfs.ext4 "$disk"
elif [ "$filesystem" != ext4 ]; then
    echo "Persistent volume must contain ext4" >&2
    exit 1
fi
uuid=$(blkid -s UUID -o value "$disk")
install -d -m 0700 /opt/classport
mkdir -p /opt/classport/data
printf 'UUID=%s /opt/classport/data ext4 defaults 0 2\n' "$uuid" >> /etc/fstab
mount /opt/classport/data
mkdir -p /opt/classport/data/courses /opt/classport/certs /opt/classport/incoming
curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /opt/classport/certs/global-bundle.pem
chmod 0644 /opt/classport/certs/global-bundle.pem

# Docker must never restart the application on the root disk if EBS is absent.
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/classport.conf <<'EOF'
[Unit]
RequiresMountsFor=/opt/classport/data
EOF
systemctl daemon-reload
systemctl enable docker caddy
systemctl restart docker caddy
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose version
touch /opt/classport/.host-ready
echo "Host ready. Supply the application secret and publish an ECR image pair before classport-release."
