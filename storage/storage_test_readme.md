# NVMe/SSD Storage Qualification Test Suite

Comprehensive storage testing framework covering **full disk testing**, **kernel-level (fio)**, **true userspace (SPDK)**, **datacenter applications**, and **detailed benchmarks**.

## Overview

This test suite provides comprehensive NVMe/SSD qualification testing across multiple layers with complete disk coverage:

### **Test Layers:**

1. **Kernel-Level Tests** (using fio with libaio ioengine - standard kernel driver)
   - Full disk sequential read/write (multiple passes)
   - Complete block size sweep (4K to 1MB) for read/write
   - Complete random read/write sweep across all block sizes
   - SMART health monitoring

2. **Userspace Tests** (using SPDK - true kernel bypass via UIO/VFIO)
   - Direct NVMe command submission (no kernel involvement)
   - Poll-mode drivers for ultra-low latency
   - Queue depth scaling tests
   - Demonstrates userspace performance advantages

3. **Datacenter Application Tests**
   - OLTP database workload simulation
   - Log streaming workload
   - Mixed read/write scenarios

4. **Benchmark Tests**
   - Comprehensive queue depth scaling (QD 1-256)
   - Latency percentile distribution
   - Sustained performance testing

## Key Improvements Over Previous Version

### 1. **Full Disk Coverage** ✅
- **Multiple full disk write passes** (1-3 passes depending on mode)
- **Full disk sequential read** test
- **Systematic block size sweep**: Tests ALL block sizes from 4K to 1MB
- **Random I/O sweep**: Tests random read/write across ALL block sizes
- Ensures every part of the drive is tested

### 2. **True Userspace Testing** ✅
- **SPDK Integration**: Real userspace I/O using Storage Performance Development Kit
- **Kernel Bypass**: Uses UIO/VFIO drivers, not kernel NVMe driver
- **Poll-Mode Drivers**: No interrupts, pure polling for lowest latency
- **Direct NVMe Commands**: Submits commands directly to NVMe queues
- Demonstrates the performance difference between kernel and userspace paths

### 3. **Complete Test Coverage** ✅
- **All I/O patterns**: Sequential, random, mixed
- **All block sizes**: 4K, 8K, 16K, 32K, 64K, 128K, 256K, 512K, 1MB
- **All operations**: Read, write, read/write mix
- **All queue depths**: 1, 2, 4, 8, 16, 32, 64, 128, 256
- **Full capacity**: Tests entire drive capacity, not just samples

### 4. **Datacenter Application Tests** ✅
- OLTP (80/20 read/write)
- Streaming writes (logs, backups)
- Mixed workloads (50/50)

### 5. **Comprehensive Benchmarks** ✅
- Queue depth scaling curves
- Latency distribution (50th, 90th, 95th, 99th, 99.9th, 99.99th percentiles)
- Sustained performance over time

## Architecture Comparison

### Kernel-Level (fio with libaio)
```
Application (fio)
      ↓
  libaio (ioengine)
      ↓
Kernel NVMe Driver ← [Kernel Space]
      ↓
NVMe Controller
```
- Uses standard kernel NVMe driver
- Context switches to kernel for I/O
- Interrupt-driven completion
- Good for general purpose testing
- **What we test**: Block device interface, kernel I/O scheduler, driver performance

### Userspace (SPDK)
```
Application (spdk_perf)
      ↓
SPDK NVMe Driver ← [User Space - No Kernel!]
      ↓
UIO/VFIO (passthrough)
      ↓
NVMe Controller
```
- **Kernel bypass**: No kernel involvement in I/O path
- No context switches
- Poll-mode (no interrupts)
- Significantly lower latency (~10-20µs vs 40-80µs)
- **What we test**: True hardware performance, userspace driver efficiency, raw NVMe capabilities

## Test Coverage Matrix

### Kernel-Level Tests (7 tests)

| Test | Coverage | Full Disk? | Block Sizes | Operations |
|------|----------|------------|-------------|------------|
| Full Disk Sequential Write | Entire drive | ✅ YES | 1MB | Write |
| Full Disk Sequential Read | Entire drive | ✅ YES | 1MB | Read |
| Block Size Sweep Read | Configurable GB | Partial | ALL (4K-1MB) | Read |
| Block Size Sweep Write | Configurable GB | Partial | ALL (4K-1MB) | Write |
| Random Read Sweep | Timed | Partial | ALL (4K-1MB) | Random Read |
| Random Write Sweep | Timed | Partial | ALL (4K-1MB) | Random Write |
| SMART Health | N/A | N/A | N/A | Monitoring |

**Full Disk Tests Explained:**
- `test_01_full_disk_sequential_write`: Writes to 95% of drive capacity (multiple passes)
- `test_02_full_disk_sequential_read`: Reads entire 95% of drive capacity
- These tests ensure every NAND block is exercised

**Block Size Sweep Explained:**
- Tests: 4K, 8K, 16K, 32K, 64K, 128K, 256K, 512K, 1MB (configurable per mode)
- Each block size tested for both read AND write
- Each block size tested for both sequential AND random
- Produces complete performance curve

### Userspace Tests (3 tests)

| Test | Method | Advantage |
|------|--------|-----------|
| SPDK Sequential Read | Poll-mode, no kernel | Demonstrates max sequential bandwidth |
| SPDK Random 4K Read | Direct queue submission | Shows lowest achievable latency |
| SPDK QD Sweep | Zero kernel overhead | True hardware scaling characteristics |

**Why SPDK Tests Matter:**
- Reveals true drive capabilities (not limited by kernel)
- Shows theoretical best performance
- Useful for applications that can use SPDK (databases, storage systems)
- Demonstrates latency improvements (~2-3x lower than kernel path)

### Datacenter Application Tests (3 tests)

| Test | Workload | Pattern | Typical Use Case |
|------|----------|---------|------------------|
| OLTP Database | 80% read / 20% write, 8K blocks | Random, high QD | PostgreSQL, MySQL transactions |
| Log Streaming | 100% write, 1MB blocks | Sequential | Log servers, backups, video |
| Mixed Workload | 50% read / 50% write, 4K blocks | Random | General purpose applications |

### Benchmark Tests (3 tests)

| Test | Purpose | Output |
|------|---------|--------|
| Queue Depth Scaling | Find saturation point | IOPS vs QD curve |
| Latency Percentiles | Tail latency analysis | p50, p90, p95, p99, p99.9, p99.99 |
| Sustained Performance | Detect throttling | Performance over time |

## Test Modes

### Quick Mode (~10-15 minutes)
```bash
export TEST_MODE=quick
```
- 1 full disk pass
- 10GB per block size test
- 60 second runtime tests
- Block sizes: 4K, 128K
- Queue depths: 1, 32, 128
- **Use case**: Smoke testing, quick validation

### Normal Mode (~1-2 hours)
```bash
export TEST_MODE=normal
```
- 2 full disk passes
- 50GB per block size test
- 300 second runtime tests
- Block sizes: 4K, 16K, 64K, 128K, 1MB
- Queue depths: 1, 4, 16, 32, 64, 128
- **Use case**: Regular qualification, pre-production

### Full Mode (~4-8 hours)
```bash
export TEST_MODE=full
```
- 3 full disk passes
- 200GB per block size test
- 600 second runtime tests
- Block sizes: 4K, 8K, 16K, 32K, 64K, 128K, 256K, 512K, 1MB
- Queue depths: 1, 2, 4, 8, 16, 32, 64, 128, 256
- **Use case**: Complete qualification, production acceptance, burn-in

## Installation

### Prerequisites
```bash
# Install Avocado framework
pip install avocado-framework

# Install fio and tools
sudo apt-get install -y fio smartmontools nvme-cli pciutils

# For RHEL/CentOS
sudo yum install -y fio smartmontools nvme-cli pciutils
```

### SPDK Installation (for Userspace Tests)
```bash
# Clone SPDK
cd /usr/local/src
sudo git clone https://github.com/spdk/spdk
cd spdk
sudo git submodule update --init

# Install dependencies
sudo scripts/pkgdep.sh

# Configure and build
sudo ./configure
sudo make -j$(nproc)

# Verify installation
ls build/examples/perf  # Should exist
```

### Setup Hugepages (required for SPDK)
```bash
# Add to /etc/sysctl.conf
echo "vm.nr_hugepages = 1024" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Verify
cat /proc/meminfo | grep Huge
```

## Usage

### Basic Usage (Kernel Tests Only)
```bash
# Discover devices
lsblk | grep nvme

# Set device and mode
export TEST_DEVICE=/dev/nvme0n1  # ⚠️ DATA WILL BE DESTROYED
export TEST_MODE=quick

# Run kernel-level tests only
sudo -E avocado run storage_test_suite.py:StorageKernelTests

# Run with logs
sudo -E avocado run storage_test_suite.py:StorageKernelTests --show-job-log
```

### Running Userspace (SPDK) Tests
```bash
# Set SPDK path if not default
export SPDK_PATH=/usr/local/src/spdk

# Set device
export TEST_DEVICE=/dev/nvme0n1
export TEST_MODE=quick

# Run SPDK tests (requires root, unbinds from kernel temporarily)
sudo -E avocado run storage_test_suite.py:StorageUserspaceTests

# Device will be rebound to kernel driver after test
```

### Running Complete Test Suite
```bash
export TEST_DEVICE=/dev/nvme0n1
export TEST_MODE=normal
export SPDK_PATH=/usr/local/src/spdk

# Run everything
sudo -E avocado run storage_test_suite.py

# This will:
# 1. Run kernel tests (full disk + sweeps)
# 2. Unbind device and run SPDK tests
# 3. Rebind to kernel
# 4. Run datacenter tests
# 5. Run benchmarks
```

### Running Specific Test Categories
```bash
# Just kernel tests (safe, no SPDK needed)
sudo -E avocado run storage_test_suite.py:StorageKernelTests

# Just datacenter tests
sudo -E avocado run storage_test_suite.py:StorageDatacenterTests

# Just benchmarks
sudo -E avocado run storage_test_suite.py:StorageBenchmarkTests

# Specific test
sudo -E avocado run storage_test_suite.py:StorageKernelTests.test_01_full_disk_sequential_write
```

## What Each Test Detects

### Full Disk Tests Detect:
- **Bad NAND blocks**: Failures during full capacity write
- **Wear leveling issues**: Uneven wear across drive
- **Over-provisioning problems**: Capacity reporting errors
- **Firmware bugs**: Crashes on large writes
- **Thermal throttling**: Performance drops over sustained writes

### Block Size Sweep Detects:
- **Inefficient block handling**: Poor performance at certain sizes
- **Alignment issues**: 512b vs 4K alignment problems
- **DMA setup overhead**: High overhead for small blocks
- **Controller optimization**: Which block sizes are optimized

### Random I/O Sweep Detects:
- **FTL (Flash Translation Layer) efficiency**: How well drive handles random writes
- **Garbage collection impact**: Performance drops during GC
- **Wear leveling overhead**: Impact of background operations
- **DRAM cache effectiveness**: Cache hit rates

### SPDK Tests Detect:
- **True hardware capability**: Performance without kernel limitations
- **Minimum achievable latency**: Hardware + firmware latency floor
- **Interrupt overhead**: Difference between poll and interrupt modes
- **Driver efficiency**: Kernel driver vs userspace driver comparison

### Datacenter Tests Detect:
- **Real-world performance**: How drive performs under actual workloads
- **QoS consistency**: Performance variance under mixed loads
- **Tail latency problems**: p99/p99.9 latency spikes
- **Workload-specific issues**: Database vs streaming optimization

### Benchmark Tests Detect:
- **Saturation point**: Where adding QD stops improving performance
- **Latency scaling**: How latency increases with load
- **Consistent performance**: Standard deviation across runs
- **Throttling behavior**: Performance drops over time

## Understanding Results

### Kernel vs SPDK Performance

**Typical Performance Comparison:**

| Metric | Kernel (fio) | Userspace (SPDK) | Improvement |
|--------|--------------|------------------|-------------|
| QD1 Latency | 60-100µs | 20-30µs | 2-3x |
| Random 4K IOPS | 400K-600K | 800K-1.2M | 2x |
| CPU Efficiency | ~60% | ~90% | 1.5x |

**Why SPDK is Faster:**
1. **No kernel context switches** (~1-2µs saved per I/O)
2. **No interrupts** (poll-mode is more efficient at high IOPS)
3. **No kernel memory copies** (zero-copy DMA)
4. **Optimized code path** (fewer instructions)

### Expected Performance Ranges

**NVMe Gen3 (PCIe 3.0 x4):**
- Sequential Read: 2000-3500 MB/s
- Sequential Write: 1000-3000 MB/s
- Random 4K Read: 200K-600K IOPS
- Random 4K Write: 100K-400K IOPS
- QD1 Latency: 40-80µs (kernel), 15-30µs (SPDK)

**NVMe Gen4 (PCIe 4.0 x4):**
- Sequential Read: 5000-7000 MB/s
- Sequential Write: 4000-5000 MB/s
- Random 4K Read: 600K-1M IOPS
- Random 4K Write: 400K-800K IOPS
- QD1 Latency: 30-60µs (kernel), 10-20µs (SPDK)

### Full Disk Test Interpretation

**Good Health:**
- All passes complete successfully
- Consistent bandwidth across passes
- No I/O errors in dmesg
- SMART health: PASSED

**Warning Signs:**
- Performance degradation across passes
- Increasing error count
- Temperature spikes >70°C
- Write errors (check dmesg)

**Critical Issues:**
- Any pass fails to complete
- I/O errors reported
- SMART health: FAILED
- Temperature >80°C sustained

## Best Practices

### Before Testing

```bash
# 1. Verify device is correct
lsblk -o NAME,SIZE,MOUNTPOINT,MODEL
echo "Testing: $TEST_DEVICE"
read -p "Is this correct? Data will be DESTROYED! (yes/no): " confirm

# 2. Unmount if mounted
sudo umount $TEST_DEVICE*

# 3. Check SMART baseline
sudo smartctl -a $TEST_DEVICE > smart_before.txt

# 4. Check temperature
sudo nvme smart-log $TEST_DEVICE | grep temperature
```

### During Testing

```bash
# Monitor in another terminal

# Watch temperature
watch -n 5 'sudo nvme smart-log /dev/nvme0n1 | grep temperature'

# Watch SMART
watch -n 30 'sudo smartctl -H /dev/nvme0n1'

# Watch dmesg for errors
sudo dmesg -w | grep -i nvme
```

### After Testing

```bash
# 1. Compare SMART data
sudo smartctl -a $TEST_DEVICE > smart_after.txt
diff smart_before.txt smart_after.txt

# 2. Check for any errors
sudo dmesg | grep -i -E "error|fail" | grep nvme

# 3. Verify device returned to kernel (after SPDK tests)
lsblk | grep nvme

# 4. Review results
cat ~/avocado/job-results/job-*/test-results/*/storage_benchmark_results.json
```

### For Production Qualification

1. **Run full mode**: `export TEST_MODE=full`
2. **Run multiple times**: At least 3 complete runs
3. **Monitor temperature**: Ensure adequate cooling
4. **Document results**: Save all logs and SMART data
5. **Verify consistency**: Results should be within 10% across runs

## Troubleshooting

### SPDK Tests Fail to Start

**Symptom**: "SPDK not found" or "Could not bind device"

**Solution**:
```bash
# Check SPDK installation
ls -l $SPDK_PATH/build/examples/perf

# Check hugepages
cat /proc/meminfo | grep Huge
# Should show: HugePages_Free > 0

# If no hugepages:
sudo sysctl -w vm.nr_hugepages=1024

# Manually test SPDK setup
cd $SPDK_PATH
sudo ./scripts/setup.sh
# Should show device bound to uio/vfio

# Reset
sudo ./scripts/setup.sh reset
```

### Device Won't Rebind to Kernel

**Symptom**: After SPDK tests, device not in `lsblk`

**Solution**:
```bash
# Manual rebind
cd $SPDK_PATH
sudo ./scripts/setup.sh reset

# Or force rebind
PCIE_ADDR=$(lspci | grep NVMe | awk '{print $1}')
echo $PCIE_ADDR | sudo tee /sys/bus/pci/drivers/nvme/bind

# Verify
lsblk | grep nvme
```

### Full Disk Write Takes Too Long

**Symptom**: Test times out

**Solution**:
- Expected: ~10-30 minutes per pass for 1TB drive
- Check if drive is actually writing: `iostat -x 5`
- If stuck, drive may have failed - check dmesg

### OOM (Out of Memory) During Tests

**Symptom**: Test killed, OOM in logs

**Solution**:
```bash
# SPDK needs hugepages
echo 2048 | sudo tee /proc/sys/vm/nr_hugepages

# Check if allocated
cat /proc/meminfo | grep Huge
```

## Advanced Topics

### Comparing Kernel vs SPDK Performance

```bash
# Run both and compare
export TEST_MODE=quick
export TEST_DEVICE=/dev/nvme0n1

# Kernel (fio) random 4K read
sudo -E avocado run storage_test_suite.py:StorageKernelTests.test_05_random_read_sweep

# SPDK random 4K read
sudo -E avocado run storage_test_suite.py:StorageUserspaceTests.test_02_spdk_random_read_4k

# Compare IOPS and latency in results
```

**Expected**: SPDK should show 1.5-2x higher IOPS and 2-3x lower latency

### Testing with Different I/O Schedulers

```bash
# Check current scheduler
cat /sys/block/nvme0n1/queue/scheduler

# Try different schedulers
echo none | sudo tee /sys/block/nvme0n1/queue/scheduler
# Run test
echo mq-deadline | sudo tee /sys/block/nvme0n1/queue/scheduler
# Run test and compare
```

### Custom Test Configurations

Edit the `TEST_CONFIGS` dictionary in the source to create custom test modes:

```python
TEST_CONFIGS = {
    'custom': {
        'full_disk_passes': 5,
        'io_size_gb': 100,
        'fio_runtime': 300,
        'num_jobs': 16,
        'block_sizes': ['4k', '16k', '128k'],
        'queue_depths': [1, 16, 64, 256],
    }
}
```

Then: `export TEST_MODE=custom`

## Safety Warnings

⚠️ **CRITICAL WARNINGS:**

1. **DATA DESTRUCTION**: All write tests DESTROY DATA on the device
2. **VERIFY DEVICE PATH**: Triple-check `TEST_DEVICE=/dev/nvmeXnY`
3. **UNMOUNT FIRST**: Always unmount before testing
4. **BACKUP DATA**: Backup any important data
5. **TEST DEVICES ONLY**: Never test production drives
6. **SPDK UNBINDS**: SPDK tests temporarily unbind device from kernel

**Read-Only Tests** (Safe on any device):
- `test_02_full_disk_sequential_read`
- `test_03_block_size_sweep_read`
- `test_05_random_read_sweep`
- SPDK read tests

**Destructive Tests** (WILL DESTROY DATA):
- `test_01_full_disk_sequential_write` ⚠️
- `test_04_block_size_sweep_write` ⚠️
- `test_06_random_write_sweep` ⚠️
- All datacenter tests
- All write benchmarks

## Summary

### Tests Run Successfully Without SPDK

**Good news**: The storage test suite runs successfully even without SPDK installed! 

When SPDK is not available:
- ✅ **Kernel-level tests run normally** (full disk testing, block size sweeps, etc.)
- ✅ **Datacenter application tests run normally**
- ✅ **Benchmark tests run normally**
- ⏭️ **Userspace tests are skipped** with clear message

**You get complete storage qualification without SPDK**. The kernel-level tests alone provide comprehensive coverage:
- Full disk read/write testing
- All block sizes (4K-1MB)
- All I/O patterns (sequential, random)
- All queue depths
- SMART health monitoring
- Datacenter workloads
- Performance benchmarks

### When to Install SPDK

SPDK is **optional** and only needed if you want to:
1. **Compare kernel vs userspace performance** (educational/research)
2. **Test applications that use SPDK** (some databases, storage systems)
3. **Achieve lowest possible latency** (2-3x lower than kernel)
4. **Demonstrate true hardware capability** (without kernel overhead)

For **99% of storage qualification use cases**, the kernel-level tests are sufficient!

### Running Without SPDK

```bash
# This works perfectly fine without SPDK
export TEST_DEVICE=/dev/nvme0n1
export TEST_MODE=normal

# Run kernel tests (comprehensive)
sudo -E avocado run storage_test_suite.py:StorageKernelTests

# Run datacenter tests
sudo -E avocado run storage_test_suite.py:StorageDatacenterTests

# Run benchmarks
sudo -E avocado run storage_test_suite.py:StorageBenchmarkTests

# Or run all non-SPDK tests
sudo -E avocado run storage_test_suite.py:StorageKernelTests storage_test_suite.py:StorageDatacenterTests storage_test_suite.py:StorageBenchmarkTests
```

**Result**: Complete storage qualification in 1-4 hours depending on test mode!

## Summary of Improvements

| Feature | Old Version | New Version |
|---------|-------------|-------------|
| Full Disk Testing | ❌ No | ✅ Yes - Multiple passes |
| Block Size Coverage | Limited | ✅ Complete (4K-1MB) |
| Random I/O Coverage | Limited | ✅ All block sizes |
| Userspace Testing | ❌ No (fio is kernel) | ✅ Yes (SPDK - optional) |
| Kernel vs Userspace | Not compared | ✅ Both tested (if SPDK available) |
| Datacenter Apps | ❌ Missing | ✅ 3 workloads |
| Benchmarks | ❌ Incomplete | ✅ Comprehensive |
| Test Modes | 1 | ✅ 3 (quick/normal/full) |
| QD Scaling | Limited | ✅ Up to QD256 |
| Latency Percentiles | No | ✅ Yes (p50-p99.99) |
| Works Without SPDK | N/A | ✅ Yes - fully functional |

## References

- SPDK Documentation: https://spdk.io/doc/
- fio Documentation: https://fio.readthedocs.io/
- NVMe Specification: https://nvmexpress.org/
- Linux NVMe Driver: https://www.kernel.org/doc/html/latest/nvme/

## Changelog

### Version 2.0 (2026-01-06)
- ✅ Added full disk testing (multiple passes)
- ✅ Added complete block size sweeps (4K-1MB)
- ✅ Added SPDK userspace testing (true kernel bypass)
- ✅ Added datacenter application tests
- ✅ Added comprehensive benchmarks
- ✅ Added queue depth scaling (QD 1-256)
- ✅ Added latency percentile analysis
- ✅ Complete test coverage matrix
