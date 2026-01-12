## Test Coverage:

### 1. **Kernel-Level Tests** (`DIMMKernelTests`)
   - Memory pattern testing (memtester)
   - ECC error detection and counting
   - Kernel page allocation/deallocation
   - DMA memory stress testing

### 2. **Userspace Tests** (`DIMMUserspaceTests`)
   - Sequential read/write operations
   - Random memory access patterns
   - Memory bandwidth measurement (using mbw)
   - Multi-threaded memory stress testing
   - NUMA node affinity testing

### 3. **Datacenter Application Tests** (`DIMMDatacenterTests`)
   - Database workload simulation
   - Cache-intensive operations
   - Memory compaction under load
   - Huge page allocation and usage
   - System behavior under memory pressure

### 4. **Benchmark Tests** (`DIMMBenchmarkTests`)
   - STREAM benchmark (bandwidth)
   - Latency measurements
   - Throughput across different block sizes
   - Sustained load testing (10 minutes)

## Usage:

```bash
# Install required tools first
sudo apt-get install stress-ng sysbench memtester numactl mbw

# Run all tests
avocado run dimm_test_suite.py

# Run specific test class
avocado run dimm_test_suite.py:DIMMKernelTests

# Run specific test
avocado run dimm_test_suite.py:DIMMBenchmarkTests.test_01_stream_benchmark

# Run with verbose output
avocado run dimm_test_suite.py --show-job-log

=========================================================================================
# Run all kernel tests
sudo avocado run dimm_test_suite.py:DIMMKernelTests

# Expected output - all tests should pass now
# Test 1: memtest safe region - PASS
# Test 2: progressive coverage - PASS  
# Test 3: ECC errors - PASS
# Test 4: kernel memory info - PASS

# Run with verbose logging to see details
sudo avocado run dimm_test_suite.py:DIMMKernelTests --show-job-log

# Run all test suites
sudo avocado run dimm_test_suite.py
