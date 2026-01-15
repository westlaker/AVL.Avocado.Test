AVL Avocado Test Suite

This is AVL Avocado Test Collection for DIMM/NVMe Memory and Storage Test

File/Directory Structure
```text
├── memory
│   ├── dimm_test_suite.py
│   └── avl-dimm-test.md
├── README.md
└── storage
    ├── configure_hugepages.sh
    ├── __pycache__  [error opening dir]
    ├── spdk_install_script.sh
    ├── spdk_quick_guide.md
    ├── storage_test_readme.md
    └── storage_test_suite.py
```

How to Run the Tests:

Setup avocado env:
```text
 $ python3 -m venv ~/venv-avocado
 $ . ~/venv-avocado/bin/activate
```

Memory Tests:

List All Memory Tests
```text
 cd memory
 export TEST_DEVICE=/dev/nvme0n1
 # to have full disk test (95% coverage)
 export TEST_MODE=full
 sudo -E /home/ubuntu/venv-avocado/bin/avocado run dimm_test_suite.py
 Type                 Test                                                                    Uri                                                                     Resolver             Tag(s)
 avocado-instrumented dimm_test_suite.py:DIMMKernelTests.test_01_memtest_safe_region          dimm_test_suite.py:DIMMKernelTests.test_01_memtest_safe_region          avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMKernelTests.test_02_progressive_coverage         dimm_test_suite.py:DIMMKernelTests.test_02_progressive_coverage         avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMKernelTests.test_03_ecc_errors                   dimm_test_suite.py:DIMMKernelTests.test_03_ecc_errors                   avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMKernelTests.test_04_kernel_memory_info           dimm_test_suite.py:DIMMKernelTests.test_04_kernel_memory_info           avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMUserspaceTests.test_01_cgroup_isolated_test      dimm_test_suite.py:DIMMUserspaceTests.test_01_cgroup_isolated_test      avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMUserspaceTests.test_02_mlock_protected_region    dimm_test_suite.py:DIMMUserspaceTests.test_02_mlock_protected_region    avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMUserspaceTests.test_03_numa_aware_testing        dimm_test_suite.py:DIMMUserspaceTests.test_03_numa_aware_testing        avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMUserspaceTests.test_04_memory_bandwidth_safe     dimm_test_suite.py:DIMMUserspaceTests.test_04_memory_bandwidth_safe     avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMMaxCoverageTest.test_01_multi_pass_coverage      dimm_test_suite.py:DIMMMaxCoverageTest.test_01_multi_pass_coverage      avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMMaxCoverageTest.test_02_memory_map_analysis      dimm_test_suite.py:DIMMMaxCoverageTest.test_02_memory_map_analysis      avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMDatacenterTests.test_01_database_workload_safe   dimm_test_suite.py:DIMMDatacenterTests.test_01_database_workload_safe   avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMDatacenterTests.test_02_hugepage_allocation_safe dimm_test_suite.py:DIMMDatacenterTests.test_02_hugepage_allocation_safe avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMBenchmarkTests.test_01_comprehensive_benchmark   dimm_test_suite.py:DIMMBenchmarkTests.test_01_comprehensive_benchmark   avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMBenchmarkTests.test_02_latency_measurement       dimm_test_suite.py:DIMMBenchmarkTests.test_02_latency_measurement       avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMBenchmarkTests.test_03_stride_patterns           dimm_test_suite.py:DIMMBenchmarkTests.test_03_stride_patterns           avocado-instrumented
 avocado-instrumented dimm_test_suite.py:DIMMBenchmarkTests.test_04_memory_stress_sustained   dimm_test_suite.py:DIMMBenchmarkTests.test_04_memory_stress_sustained   avocado-instrumented

 Resolver Reference Info

 TEST TYPES SUMMARY
 ==================
 avocado-instrumented: 16
```

Run All Memory Tests:
```text
 cd memory
 # to have full disk test (95% coverage)
 export TEST_MODE=full
 sudo -E /home/ubuntu/venv-avocado/bin/avocado run --max-parallel-tasks=1 dimm_test_suite.py
```

Run Specific Memory Test:
```text
 cd memory
 # to have full disk test (95% coverage)
 export TEST_MODE=full
 sudo -E /home/ubuntu/venv-avocado/bin/avocado run --max-parallel-tasks=1  dimm_test_suite.py:DIMMBenchmarkTests.test_04_memory_stress_sustained 
```

Storage Tests:

List Storage Tests:
```text
 cd storage
 export TEST_DEVICE=/dev/nvme0n1
 # to have full disk test (95% coverage)
 export TEST_MODE=full
 sudo -E /home/ubuntu/venv-avocado/bin/avocado -V list  storage_test_suite.py
 Type                 Test                                                                        Uri                                                                         Resolver             Tag(s)
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_01_full_disk_sequential_write storage_test_suite.py:StorageKernelTests.test_01_full_disk_sequential_write avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_02_full_disk_sequential_read  storage_test_suite.py:StorageKernelTests.test_02_full_disk_sequential_read  avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_03_block_size_sweep_read      storage_test_suite.py:StorageKernelTests.test_03_block_size_sweep_read      avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_04_block_size_sweep_write     storage_test_suite.py:StorageKernelTests.test_04_block_size_sweep_write     avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_05_random_read_sweep          storage_test_suite.py:StorageKernelTests.test_05_random_read_sweep          avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_06_random_write_sweep         storage_test_suite.py:StorageKernelTests.test_06_random_write_sweep         avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageKernelTests.test_07_smart_health_check         storage_test_suite.py:StorageKernelTests.test_07_smart_health_check         avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageUserspaceTests.test_01_spdk_sequential_read    storage_test_suite.py:StorageUserspaceTests.test_01_spdk_sequential_read    avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageUserspaceTests.test_02_spdk_random_read_4k     storage_test_suite.py:StorageUserspaceTests.test_02_spdk_random_read_4k     avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageUserspaceTests.test_03_spdk_queue_depth_sweep  storage_test_suite.py:StorageUserspaceTests.test_03_spdk_queue_depth_sweep  avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageDatacenterTests.test_01_database_oltp          storage_test_suite.py:StorageDatacenterTests.test_01_database_oltp          avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageDatacenterTests.test_02_log_streaming          storage_test_suite.py:StorageDatacenterTests.test_02_log_streaming          avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageDatacenterTests.test_03_mixed_workload         storage_test_suite.py:StorageDatacenterTests.test_03_mixed_workload         avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageBenchmarkTests.test_01_queue_depth_scaling     storage_test_suite.py:StorageBenchmarkTests.test_01_queue_depth_scaling     avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageBenchmarkTests.test_02_latency_percentiles     storage_test_suite.py:StorageBenchmarkTests.test_02_latency_percentiles     avocado-instrumented
 avocado-instrumented storage_test_suite.py:StorageBenchmarkTests.test_03_sustained_performance   storage_test_suite.py:StorageBenchmarkTests.test_03_sustained_performance   avocado-instrumented

 Resolver Reference Info

 TEST TYPES SUMMARY
 ==================
 avocado-instrumented: 16
```

Run All Storage Tests:
```text
 cd storage
 export TEST_DEVICE=/dev/nvme0n1
 # to have full disk test (95% coverage)
 export TEST_MODE=full
 sudo -E /home/ubuntu/venv-avocado/bin/avocado run --max-parallel-tasks=1   storage_test_suite.py
```

Run Specific Storage Test:
```text
 cd storage
 export TEST_DEVICE=/dev/nvme0n1
 # to have full disk test (95% coverage)
 export TEST_MODE=full
 sudo -E /home/ubuntu/venv-avocado/bin/avocado run --max-parallel-tasks=1   storage_test_suite.py:StorageBenchmarkTests.test_03_sustained_performance
```
Report the Test Results

Example usage

```text
1) Generate reports from the last 10 jobs
python3 avocado_report.py --jobs 10 --out-dir ./reports --debug
python3 avocado_report.py --jobs 2 --latest-per-suite --out-dir ./reports --debug
python3 avocado_report.py --job-root /home/ubuntu/avocado/job-results --out-dir ./reports

2) Point at a specific job-results root for job-id e.g. job-2026-01-14T19.54-4b8f791
python3 avocado_report_metrics_v12.py   --job-dir /home/ubuntu/avocado/job-results/job-2026-01-14T19.54-4b8f791   --min-tests 1   --out-dir ./reports   --debug
```

Output files

```text
final_reports/storage_report.csv
final_reports/storage_report.txt
final_reports/storage_report.pdf (if reportlab installed)
final_reports/memory_report.csv
final_reports/memory_report.txt
final_reports/memory_report.pdf
```


```


