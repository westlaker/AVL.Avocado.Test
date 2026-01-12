# SPDK Installation Quick Guide

## What is SPDK?

**SPDK (Storage Performance Development Kit)** enables true userspace NVMe testing by bypassing the kernel entirely. This provides:
- ✅ **2-3x lower latency** (20µs vs 60µs)
- ✅ **Higher IOPS** (up to 2x)
- ✅ **Lower CPU usage** (poll-mode vs interrupts)
- ✅ **True hardware performance** measurement

## Do You Need SPDK?

**No, SPDK is OPTIONAL!**

The storage test suite works perfectly without SPDK:
- ✅ Complete disk coverage (full disk read/write)
- ✅ All block sizes (4K-1MB)
- ✅ All I/O patterns (sequential, random)
- ✅ Datacenter workloads
- ✅ Comprehensive benchmarks

**You only need SPDK if:**
- You want to compare kernel vs userspace performance
- You're testing applications that use SPDK
- You need absolute minimum latency measurements
- You want to see true hardware capabilities

**For 99% of storage qualification, kernel tests are sufficient!**

## Installation Options

### Option 1: Use the Installation Script (Recommended)

```bash
# Download the script
cd ~/
curl -O https://your-repo/install_spdk.sh
chmod +x install_spdk.sh

# Run installation (takes 15-30 minutes)
sudo ./install_spdk.sh

# Test installation
export SPDK_PATH=/usr/local/src/spdk
cd $SPDK_PATH
ls -l build/examples/perf  # Should exist
```

### Option 2: Quick Manual Installation

```bash
# Install dependencies (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y \
    git gcc g++ make libaio-dev libssl-dev \
    uuid-dev python3 pkg-config libnuma-dev \
    nasm meson libcunit1-dev

# For RHEL/CentOS
sudo yum install -y \
    git gcc gcc-c++ make libaio-devel openssl-devel \
    libuuid-devel python3 pkgconfig numactl-devel \
    nasm meson CUnit-devel

# Clone SPDK
sudo mkdir -p /usr/local/src
cd /usr/local/src
sudo git clone https://github.com/spdk/spdk.git
cd spdk

# Use LTS version
sudo git checkout v24.01
sudo git submodule update --init --recursive

# Build SPDK (takes 10-30 minutes)
sudo ./configure --with-nvme
sudo make -j$(nproc)

# Configure hugepages (required for SPDK)
echo 1024 | sudo tee /proc/sys/vm/nr_hugepages
echo "vm.nr_hugepages = 1024" | sudo tee -a /etc/sysctl.conf

# Verify
ls -l build/examples/perf
cat /proc/meminfo | grep Huge
```

### Option 3: Custom Installation Path

```bash
# Install to custom location
CUSTOM_PATH=/opt/spdk
sudo mkdir -p $CUSTOM_PATH
cd $CUSTOM_PATH
# ... follow same build steps ...

# Set environment variable
export SPDK_PATH=/opt/spdk

# Run tests
sudo -E avocado run storage_test_suite.py
```

## Verification

### Check SPDK Installation

```bash
# Set SPDK path
export SPDK_PATH=/usr/local/src/spdk

# Verify files exist
ls -l $SPDK_PATH/build/examples/perf
ls -l $SPDK_PATH/scripts/setup.sh

# Check hugepages
cat /proc/meminfo | grep Huge
# Should show: HugePages_Free > 0
```

### Test SPDK Manually

```bash
cd $SPDK_PATH

# Setup SPDK (bind NVMe to userspace)
sudo ./scripts/setup.sh

# Find NVMe PCIe address
lspci | grep NVMe
# Example output: 01:00.0 Non-Volatile memory controller: Samsung...

# Run quick test (replace PCIe address with yours)
sudo ./build/examples/perf \
    -q 128 -o 4096 -w read -t 10 \
    -c 0x1 -r 'trtype:PCIe traddr:0000:01:00.0'

# Reset (return to kernel driver)
sudo ./scripts/setup.sh reset

# Verify device is back
lsblk | grep nvme
```

## Common Issues

### Issue 1: No Hugepages

**Symptom:**
```
SPDK setup failed
HugePages_Free: 0
```

**Solution:**
```bash
# Allocate hugepages
echo 1024 | sudo tee /proc/sys/vm/nr_hugepages

# Make persistent
echo "vm.nr_hugepages = 1024" | sudo tee -a /etc/sysctl.conf

# If still 0, reboot may be needed
sudo reboot
```

### Issue 2: Build Fails

**Symptom:**
```
configure: error: missing dependency
```

**Solution:**
```bash
# Run SPDK's dependency installer
cd $SPDK_PATH
sudo scripts/pkgdep.sh

# Then retry build
sudo ./configure --with-nvme
sudo make -j$(nproc)
```

### Issue 3: Device Won't Bind

**Symptom:**
```
Failed to bind device to uio/vfio
```

**Solution:**
```bash
# Check if device is mounted
lsblk | grep nvme0n1
sudo umount /dev/nvme0n1*

# Check if in use
lsof | grep nvme0n1

# Try vfio instead of uio
cd $SPDK_PATH
sudo DRIVER_OVERRIDE=vfio-pci ./scripts/setup.sh
```

### Issue 4: Tests Timeout

**Symptom:**
```
SPDK perf command times out
```

**Solution:**
```bash
# Start with shorter test
sudo ./build/examples/perf \
    -q 32 -o 4096 -w read -t 5 \
    -c 0x1 -r 'trtype:PCIe traddr:0000:01:00.0'

# Check if device address is correct
lspci -nn | grep NVMe
```

## Running Tests with SPDK

### Complete Test Suite

```bash
export SPDK_PATH=/usr/local/src/spdk
export TEST_DEVICE=/dev/nvme0n1
export TEST_MODE=quick

# Run all tests (kernel + SPDK + datacenter + benchmarks)
sudo -E avocado run storage_test_suite.py
```

### Just SPDK Tests

```bash
export SPDK_PATH=/usr/local/src/spdk
export TEST_DEVICE=/dev/nvme0n1

# Run only userspace (SPDK) tests
sudo -E avocado run storage_test_suite.py:StorageUserspaceTests
```

### Compare Kernel vs SPDK

```bash
# Run kernel random 4K read
sudo -E avocado run storage_test_suite.py:StorageKernelTests.test_05_random_read_sweep

# Run SPDK random 4K read
sudo -E avocado run storage_test_suite.py:StorageUserspaceTests.test_02_spdk_random_read_4k

# Compare IOPS and latency in results
```

## Uninstallation

```bash
# Remove SPDK
sudo rm -rf /usr/local/src/spdk

# Remove hugepages configuration
sudo sed -i '/vm.nr_hugepages/d' /etc/sysctl.conf

# Reset hugepages
echo 0 | sudo tee /proc/sys/vm/nr_hugepages
```

## Summary

| Aspect | Without SPDK | With SPDK |
|--------|--------------|-----------|
| Installation Time | 0 minutes | 15-30 minutes |
| Test Coverage | ✅ Complete | ✅ Complete + userspace |
| Full Disk Testing | ✅ Yes | ✅ Yes |
| Block Size Sweep | ✅ Yes | ✅ Yes |
| Datacenter Tests | ✅ Yes | ✅ Yes |
| Benchmarks | ✅ Yes | ✅ Yes + SPDK |
| Latency Comparison | Kernel only | Kernel vs Userspace |
| Recommended For | 99% of users | Advanced users, research |

**Bottom Line:** Start without SPDK. Add it later if you need userspace testing.
