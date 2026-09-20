#!/bin/sh
# Dedicated application runtime only. No SSH server, desktop, firmware or motor commands.
set -eu
export DEBIAN_FRONTEND=noninteractive
echo 'WORKBENCH_STAGE:packages'
apt-get update -q
apt-get install -y --no-install-recommends python3 python3-venv ca-certificates curl libstdc++6
echo 'WORKBENCH_STAGE:sdk'
python3 -m venv /opt/hand-workbench-venv
/opt/hand-workbench-venv/bin/python -m pip install --disable-pip-version-check 'wuji-sdk==2026.8.31' 'numpy>=2.2,<3'
echo 'WORKBENCH_STAGE:cli'
curl --fail --location --retry 2 --output /tmp/wuji.tar.gz https://github.com/wuji-technology/wuji-cli/releases/download/v2026.8.31/wuji_2026.8.31_x86_64-unknown-linux-gnu.tar.gz
mkdir -p /tmp/hand-workbench-cli
tar -xzf /tmp/wuji.tar.gz -C /tmp/hand-workbench-cli
find /tmp/hand-workbench-cli -type f -name wuji -exec install -m 0755 '{}' /usr/local/bin/wuji \;
/usr/local/bin/wuji --version
id workbench >/dev/null 2>&1 || useradd --create-home --shell /bin/sh workbench
mkdir -p /opt/hand-workbench
chown -R workbench:workbench /opt/hand-workbench
printf '[user]\ndefault=workbench\n[boot]\nsystemd=false\n' > /etc/wsl.conf
echo 'WORKBENCH_STAGE:verify'
/opt/hand-workbench-venv/bin/python -c 'from wuji_sdk import SdkManager,JointCommand;print("SDK import OK")'
apt-get clean
echo 'WORKBENCH_STAGE:ready'
