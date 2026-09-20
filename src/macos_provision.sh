#!/bin/sh
# Runs only INSIDE the app-owned Linux VM. It never connects to or enables a hand.
set -eu
export DEBIAN_FRONTEND=noninteractive
if [ ! -x /opt/hand-workbench-venv/bin/python ]; then
    apt-get update
    apt-get install -y --no-install-recommends python3-venv libusb-1.0-0 libgl1 libegl1 libglib2.0-0
    python3 -m venv /opt/hand-workbench-venv
fi
if [ ! -x /usr/local/bin/wuji ]; then
    mkdir -p /tmp/workbench-cli
    tar -xzf /opt/hand-workbench/wuji-cli.tar.gz -C /tmp/workbench-cli
    find /tmp/workbench-cli -type f -name wuji -exec install -m 0755 '{}' /usr/local/bin/wuji \;
    /usr/local/bin/wuji --version
fi
if [ ! -f /opt/hand-workbench-sdk-2026.8.31 ]; then
    /opt/hand-workbench-venv/bin/pip install 'wuji-sdk==2026.8.31' 'eclipse-zenoh==1.9.0' 'mujoco==3.3.7' 'numpy>=2.2,<3' 'Pillow>=11.3,<13' 'scipy>=1.14,<2'
    /opt/hand-workbench-venv/bin/python -c 'from wuji_sdk import SdkManager; import zenoh, mujoco'
    touch /opt/hand-workbench-sdk-2026.8.31
fi
