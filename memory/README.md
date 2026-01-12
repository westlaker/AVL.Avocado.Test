# DIMM Qualification Test Suite

Comprehensive memory testing framework for DIMM qualification on live systems using the Avocado testing framework.

## Overview

This test suite provides comprehensive DIMM (Dual In-line Memory Module) qualification testing across multiple layers:
- **Kernel-level testing**: Low-level memory operations and kernel interface validation
- **Userspace testing**: Memory allocation patterns, bandwidth, and isolation
- **Datacenter application testing**: Real-world workload simulation
- **Benchmark testing**: Performance characterization and validation

The suite is designed to **safely test memory on live production systems** without causing OOM (Out-of-Memory) conditions or system instability.

## Test Coverage

### 1. Kernel-Level Tests (`DIMMKernelTests`)

#### 1.1 Memory Pattern Testing (`test_01_memtest_safe_region`)
- **Purpose**: Validates memory cell integrity using pattern-based testing
- **Coverage**: Tests data retention and bit-level accuracy
- **Method**: Uses `memtester` with multiple patterns (walking ones, walking zeros, etc.)
- **Safety**: Dynamically calculates safe memory size based on system state
- **Detection**: Identifies stuck bits, data retention issues, and addressing problems

#### 1.2 Progressive Coverage (`test_02_progressive_coverage`)
- **Purpose**: Maximizes memory coverage through incremental testing
- **Coverage**: Tests memory in chunks to cover more physical addresses
- **Method**: Multiple 512MB passes with cache dropping between passes
- **Benefit**: Covers different physical pages across multiple test iterations
- **Detection**: Identifies location-specific memory failures

#### 1.3 ECC Error Detection (`test_03_ecc_errors`)
- **Purpose**: Monitors Error Correction Code (ECC) functionality
- **Coverage**: Detects correctable and uncorrectable memory errors
- **Method**: Reads EDAC (Error Detection and Correction) kernel interfaces
- **Monitors**:
  - Correctable errors (CE) - single-bit errors that were fixed
  - Uncorrectable errors (UE) - multi-bit errors that cannot be corrected
- **Detection**: Identifies failing DIMMs before catastrophic failure
- **Threshold**: Warns on >100 correctable errors, fails on any uncorrectable errors

#### 1.4 Kernel Memory Analysis (`test_04_kernel_memory_info`)
- **Purpose**: Analyzes kernel memory usage and fragmentation
- **Coverage**: Validates kernel memory management health
- **Monitors**:
  - Slab allocator cache usage
  - Page table overhead
  - Memory zone information
  - Buddy allocator fragmentation
- **Detection**: Identifies memory fragmentation and kernel memory leaks

### 2. Userspace Tests (`DIMMUserspaceTests`)

#### 2.1 Cgroup Isolated Testing (`test_01_cgroup_isolated_test`)
- **Purpose**: Tests memory under controlled resource limits
- **Coverage**: Validates memory behavior with strict boundaries
- **Method**: Uses cgroups to enforce memory limits during testing
- **Safety**: Prevents test from consuming excessive memory
- **Detection**: Identifies memory allocation failures and limit enforcement issues

#### 2.2 Memory Locking (`test_02_mlock_protected_region`)
- **Purpose**: Tests memory page locking (prevents swapping)
- **Coverage**: Validates mlock() system call and page pinning
- **Method**: Allocates and locks memory, verifies data integrity
- **Detection**: Identifies page locking failures and swap-related issues

#### 2.3 NUMA Awareness (`test_03_numa_aware_testing`)
- **Purpose**: Tests memory on each NUMA (Non-Uniform Memory Access) node
- **Coverage**: Validates memory across all NUMA domains
- **Method**: Binds allocation and execution to specific NUMA nodes
- **Detection**: Identifies NUMA-specific memory issues and cross-node access problems
- **Benefit**: Ensures all physical memory controllers are tested

#### 2.4 Memory Bandwidth (`test_04_memory_bandwidth_safe`)
- **Purpose**: Measures memory subsystem bandwidth
- **Coverage**: Validates memory throughput performance
- **Method**: Uses sysbench to measure read/write bandwidth
- **Metrics**: MB/s throughput, latency characteristics
- **Detection**: Identifies bandwidth degradation indicating failing DIMMs

### 3. Maximum Coverage Tests (`DIMMMaxCoverageTest`)

#### 3.1 Multi-Pass Coverage (`test_01_multi_pass_coverage`)
- **Purpose**: Maximizes total physical memory tested
- **Coverage**: Multiple full passes to test different physical pages
- **Method**: 
  - Runs 2-10 passes depending on test mode
  - Drops page cache between passes
  - Each pass tests different physical memory
- **Benefit**: Achieves >200% nominal coverage through multiple passes
- **Detection**: Catches intermittent memory failures

#### 3.2 Memory Map Analysis (`test_02_memory_map_analysis`)
- **Purpose**: Analyzes physical memory layout and testable regions
- **Coverage**: Identifies all accessible memory regions
- **Method**: Parses `/proc/iomem` to map physical memory
- **Benefit**: Provides detailed memory topology information
- **Detection**: Identifies reserved regions and memory holes

### 4. Datacenter Application Tests (`DIMMDatacenterTests`)

#### 4.1 Database Workload (`test_01_database_workload_safe`)
- **Purpose**: Simulates database memory access patterns
- **Coverage**: Read-heavy workload with random access
- **Method**: Multiple workers with read64 pattern
- **Duration**: 5 minutes sustained load
- **Detection**: Identifies failures under realistic database loads

#### 4.2 Huge Page Testing (`test_02_hugepage_allocation_safe`)
- **Purpose**: Tests large page (2MB/1GB) allocation and usage
- **Coverage**: Validates TLB (Translation Lookaside Buffer) and huge page support
- **Method**: Allocates memory that benefits from huge pages
- **Monitors**: Huge page allocation before/after test
- **Detection**: Identifies huge page allocation issues

### 5. Benchmark Tests (`DIMMBenchmarkTests`)

#### 5.1 Comprehensive Bandwidth Benchmark (`test_01_comprehensive_benchmark`)
- **Purpose**: Characterizes memory performance across block sizes
- **Coverage**: Tests 4KB, 64KB, 1MB, and 16MB block sizes
- **Method**: Sysbench memory benchmark with multiple block sizes
- **Metrics**: 
  - Sequential read/write throughput
  - Operations per second
  - Total data transferred
- **Detection**: Identifies performance degradation patterns

## Memory Safety Features

### Dynamic Memory Calculation
The suite automatically calculates safe test sizes based on:
- Current free memory
- Available cached memory
- Swap availability (critical - see below)
- System load
- Test mode configuration

### Swap Detection
**CRITICAL**: The suite detects systems without swap and becomes ultra-conservative:
- **With swap**: Uses up to 50% of available memory, 3GB headroom
- **Without swap**: Uses only 10-25% of free memory, 5GB headroom
- **Rationale**: Without swap, memory overcommit causes immediate OOM

### Multi-Tier Safety Limits
1. **Percentage-based**: Uses configured percentage of available memory
2. **Absolute headroom**: Ensures minimum free memory (3-5GB)
3. **Total memory cap**: Never exceeds 40-50% of total system memory
4. **Mode-specific caps**: Quick=512MB-1GB, Normal=1-2GB, Full=4-8GB
5. **Real-time adjustment**: Checks memory before each test

### OOM Prevention
- Monitors for exit code -9 (SIGKILL from OOM killer)
- Uses single workers for aggressive tests
- Implements timeouts to prevent runaway tests
- Progressive size reduction if memory is constrained

## Test Modes

### Quick Mode (Default)
```bash
export TEST_MODE=quick
sudo -E avocado run dimm_test_suite.py
```
- **Duration**: ~5-10 minutes
- **Coverage**: 512MB-1GB memory tested
- **Use case**: Smoke testing, development validation
- **Memory**: Uses 10-25% of available memory

### Normal Mode
```bash
export TEST_MODE=normal
sudo -E avocado run dimm_test_suite.py
```
- **Duration**: ~20-30 minutes
- **Coverage**: 1-2GB memory tested
- **Use case**: Regular validation, pre-production testing
- **Memory**: Uses 15-35% of available memory

### Full Mode
```bash
export TEST_MODE=full
sudo -E avocado run dimm_test_suite.py
```
- **Duration**: 1-3 hours
- **Coverage**: 4-8GB+ memory tested, multiple passes
- **Use case**: Complete DIMM qualification, production acceptance
- **Memory**: Uses 25-50% of available memory

## Test Matrix

| Test Class | Test Name | Quick | Normal | Full | Detection |
|------------|-----------|-------|--------|------|-----------|
| Kernel | Pattern Testing | 512MB | 1GB | 4GB+ | Bit errors, stuck bits |
| Kernel | Progressive Coverage | 1GB | 2GB | 8GB+ | Address-specific failures |
| Kernel | ECC Errors | ✓ | ✓ | ✓ | ECC failures, DIMM degradation |
| Kernel | Memory Analysis | ✓ | ✓ | ✓ | Fragmentation, kernel issues |
| Userspace | Cgroup Isolation | 512MB | 1GB | 2GB | Resource limit issues |
| Userspace | mlock Testing | 512MB | 512MB | 1GB | Page locking failures |
| Userspace | NUMA Testing | 256MB/node | 512MB/node | 1GB/node | NUMA-specific issues |
| Userspace | Bandwidth | 1GB | 3GB | 5GB | Performance degradation |
| Max Coverage | Multi-Pass | 2 passes | 4 passes | 10 passes | Intermittent failures |
| Max Coverage | Memory Map | ✓ | ✓ | ✓ | Memory topology |
| Datacenter | Database Load | 1GB | 2GB | 4GB | Realistic workload issues |
| Datacenter | Huge Pages | 1GB | 2GB | 4GB | Large page issues |
| Benchmark | Bandwidth Bench | 1GB | 3GB | 5GB | Performance metrics |

## What Each Test Detects

### Hardware Issues
- **Stuck bits**: Bits that always read as 0 or 1
- **Data retention**: Cells that lose data over time
- **Address decode**: Incorrect address mapping
- **Refresh failures**: DRAM refresh circuit issues
- **Row hammer**: Adjacent cell interference
- **Manufacturing defects**: Physical DIMM defects

### System Issues
- **ECC failures**: Memory controller ECC issues
- **NUMA problems**: Cross-node access failures
- **Memory controller**: Controller hardware issues
- **Thermal problems**: Heat-induced failures
- **Power issues**: Voltage-related instabilities

### Performance Issues
- **Bandwidth degradation**: Reduced memory throughput
- **Latency increases**: Slower memory access
- **Channel imbalance**: Uneven channel utilization
- **Fragmentation**: Memory allocation inefficiencies

## Installation

### Prerequisites
```bash
# Install Avocado framework
pip install avocado-framework

# Install required system tools
sudo apt-get install -y memtester stress-ng sysbench numactl

# For RHEL/CentOS
sudo yum install -y memtester stress-ng sysbench numactl
```

### Setup
```bash
# Clone or copy the test suite
mkdir -p ~/dimm_tests
cd ~/dimm_tests
cp dimm_test_suite.py .

# Verify installation
avocado list dimm_test_suite.py
```

## Usage

### Basic Usage
```bash
# Run all tests in quick mode
export TEST_MODE=quick
sudo -E avocado run dimm_test_suite.py

# Run specific test class
sudo -E avocado run dimm_test_suite.py:DIMMKernelTests

# Run specific test
sudo -E avocado run dimm_test_suite.py:DIMMKernelTests.test_01_memtest_safe_region
```

### With Logging
```bash
# Show detailed logs
sudo -E avocado run dimm_test_suite.py --show-job-log

# Save logs to specific location
sudo -E avocado run dimm_test_suite.py --job-results-dir /var/log/dimm_tests
```

### Continuous Testing
```bash
# Run tests in loop for burn-in
for i in {1..100}; do
  echo "=== Pass $i ==="
  sudo -E avocado run dimm_test_suite.py:DIMMKernelTests.test_01_memtest_safe_region
  sleep 60
done
```

## Results Interpretation

### Test Results
- **PASS**: Test completed successfully, no errors detected
- **FAIL**: Test detected memory errors or failures
- **WARN**: Test passed but encountered warnings (non-critical)
- **CANCEL**: Test was skipped (insufficient resources, missing features)
- **ERROR**: Test encountered execution error

### Log Locations
```bash
# Latest test results
ls -lt ~/avocado/job-results/ | head -1

# View specific test log
cat ~/avocado/job-results/job-*/test-results/*/debug.log

# View JSON results
cat ~/avocado/job-results/job-*/results.json
```

### ECC Error Interpretation
- **0 errors**: Excellent, no ECC events
- **1-10 CE**: Normal, occasional correctable errors
- **10-100 CE**: Acceptable, monitor trend
- **>100 CE**: Warning, DIMM may be degrading
- **Any UE**: Critical, DIMM failure imminent

## Best Practices

### For Development/Testing
1. Start with **quick mode** to verify setup
2. Run on test systems first
3. Monitor system resources during tests
4. Review logs for any warnings

### For Production Qualification
1. **Add swap space** for more comprehensive testing:
   ```bash
   sudo fallocate -l 8G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

2. Run **full mode** for complete qualification:
   ```bash
   export TEST_MODE=full
   sudo -E avocado run dimm_test_suite.py
   ```

3. Run during **low-activity periods**

4. Perform **multiple passes** for burn-in:
   ```bash
   for i in {1..10}; do
     echo "=== Burn-in pass $i/10 ==="
     sudo -E avocado run dimm_test_suite.py
   done
   ```

5. Monitor **ECC errors** over time:
   ```bash
   # Before test
   cat /sys/devices/system/edac/mc/mc*/ce_count
   
   # After test
   cat /sys/devices/system/edac/mc/mc*/ce_count
   ```

### For Continuous Monitoring
```bash
# Daily memory health check
0 2 * * * /usr/bin/avocado run /path/to/dimm_test_suite.py:DIMMKernelTests.test_03_ecc_errors
```

## Troubleshooting

### OOM Killer Triggered
**Symptom**: Process killed with "Out of memory"
**Solution**:
1. Check available memory: `free -h`
2. Add swap space (recommended)
3. Reduce test mode: `export TEST_MODE=quick`
4. Free up memory before testing

### Tests Timing Out
**Symptom**: Tests killed after timeout
**Solution**:
1. This is normal for large memory tests
2. Check if test is actually progressing (CPU usage)
3. Timeouts are calculated dynamically
4. For very slow systems, increase percentage in configs

### Permission Denied Errors
**Symptom**: Cannot access `/sys` or `/proc` files
**Solution**:
1. Always run with `sudo`
2. Use `-E` flag to preserve environment: `sudo -E`
3. Some features require root (cgroups, mlock)

### No ECC Data Available
**Symptom**: ECC test reports "not_available"
**Solution**:
- System may not have ECC memory
- EDAC driver may not be loaded: `sudo modprobe edac_core`
- This is informational, not an error

## Advanced Configuration

### Custom Test Modes
Edit the `TEST_CONFIGS` dictionary in the source:
```python
TEST_CONFIGS = {
    'custom': {
        'max_memtest_mb': 2048,
        'max_chunks': 4,
        'max_passes': 3,
        'memory_percentage': 30,
    }
}
```

Then run:
```bash
export TEST_MODE=custom
sudo -E avocado run dimm_test_suite.py
```

### Adjusting Safety Margins
For systems with ample memory:
- Increase `memory_percentage` (default: 10-50%)
- Reduce `min_headroom_kb` (default: 3-5GB)
- Increase `absolute_max_kb` (default: 4-8GB)

**Warning**: Only adjust if you understand the OOM risks!

## Coverage Gaps and Future Enhancements

### Current Gaps
1. **Row Hammer Testing**: Not explicitly tested (complex attack patterns)
2. **Temperature Variation**: No thermal stress testing
3. **Power Cycling**: No cold/warm boot cycles
4. **Sustained Load**: Limited to 5-10 minute durations
5. **Error Injection**: No intentional error injection testing
6. **Rowhammer Variants**: TRRespass, Half-Double not covered

### Recommended Additional Tests
These tests would enhance coverage but require specialized tools or longer test times:

1. **Intel MLC (Memory Latency Checker)**
   - Detailed latency characterization
   - Bandwidth saturation testing
   - Requires Intel MLC tool

2. **STREAM Benchmark**
   - Industry-standard bandwidth benchmark
   - Requires compilation from source

3. **Thermal Stress**
   - Combine memory test with CPU stress
   - Monitor temperature sensors
   - Requires `stress` + `sensors` tools

4. **Long-Duration Burn-in**
   - 24-72 hour continuous testing
   - Catches time-dependent failures

## System Requirements

### Minimum
- Linux kernel 3.10+ (for cgroups v1)
- 4GB RAM minimum
- Python 3.6+
- Root access

### Recommended
- Linux kernel 4.14+ (for better cgroup support)
- 16GB+ RAM for comprehensive testing
- Swap space enabled (4-8GB)
- ECC memory for full feature support
- NUMA-capable system for NUMA tests

### Performance Notes
- Tests scale with memory size
- ~1-2 minutes per GB for memtester
- NUMA systems tested per-node
- Systems without swap are more restricted

## License and Support

This test suite is designed for DIMM qualification and validation. For issues or enhancements, modify the source code as needed for your specific requirements.

## References

- Avocado Framework: https://avocado-framework.github.io/
- memtester: http://pyropus.ca/software/memtester/
- EDAC: https://www.kernel.org/doc/html/latest/driver-api/edac.html
- STREAM: https://www.cs.virginia.edu/stream/

## Changelog

### Version 1.0 (2026-01-05)
- Initial release
- Comprehensive kernel, userspace, DC, and benchmark tests
- Smart OOM prevention with swap detection
- Multi-mode testing (quick/normal/full)
- Safe testing on live systems
- NUMA-aware testing
- ECC error monitoring
- Dynamic memory safety calculations
