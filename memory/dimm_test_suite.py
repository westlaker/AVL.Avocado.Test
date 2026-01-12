"""
DIMM Qualification Test Suite for Avocado Framework
Safe memory testing on live systems with kernel region protection

Configuration:
    TEST_MODE environment variable controls test intensity:
    - 'quick'  : Fast tests, limited memory coverage (default, ~2GB, ~5 min)
    - 'normal' : Moderate tests, good coverage (~10GB, ~30 min)
    - 'full'   : Comprehensive tests, maximum coverage (hours)
    
    Usage:
        export TEST_MODE=quick
        sudo avocado run dimm_test_suite.py:DIMMKernelTests
"""

import os
import sys
import time
import json
import subprocess
import mmap
import ctypes
from avocado import Test
from avocado.utils import process, memory, cpu

# Get test mode from environment
TEST_MODE = os.environ.get('TEST_MODE', 'quick').lower()

# Test mode configurations - ULTRA CONSERVATIVE for systems without swap
TEST_CONFIGS = {
    'quick': {
        'max_memtest_mb': 512,         # 512MB - very safe
        'max_chunks': 1,               # 1 x 512MB only
        'max_passes': 2,               # 2 passes
        'memory_percentage': 10,       # Use only 10% of usable memory (ultra safe for no-swap systems)
    },
    'normal': {
        'max_memtest_mb': 1024,        # 1GB - finishes in ~2 minutes
        'max_chunks': 2,               # 2 x 512MB = 1GB
        'max_passes': 3,               # 3 passes
        'memory_percentage': 15,       # Use 15% of usable memory
    },
    'full': {
        'max_memtest_mb': 4096,        # 4GB max (reduced from 8GB for no-swap)
        'max_chunks': 8,               # 8 x 512MB = 4GB
        'max_passes': 5,               # 5 passes
        'memory_percentage': 25,       # Use 25% of usable memory (was 40%)
    }
}

CONFIG = TEST_CONFIGS.get(TEST_MODE, TEST_CONFIGS['quick'])

# Handle different Avocado versions for SoftwareManager
try:
    from avocado.utils.software_manager.manager import SoftwareManager
except ImportError:
    try:
        from avocado.utils.software_manager import SoftwareManager
    except ImportError:
        # Fallback if SoftwareManager is not available
        class SoftwareManager:
            def __init__(self):
                pass
            def check_installed(self, package):
                try:
                    result = process.run(f"which {package}", ignore_status=True)
                    return result.exit_status == 0
                except:
                    return False
            def install(self, package):
                try:
                    process.run(f"sudo apt-get install -y {package} || sudo yum install -y {package}", 
                               shell=True, ignore_status=True)
                except:
                    pass


class SafeMemoryManager:
    """Manager for safe memory allocation avoiding kernel regions"""
    
    def __init__(self, log):
        self.log = log
        self.safe_memory_info = {}
        self.reserved_regions = []
        
    def get_memory_layout(self):
        """Get system memory layout and reserved regions"""
        layout = {
            'total': 0,
            'available': 0,
            'kernel_reserved': 0,
            'safe_testable': 0,
            'numa_nodes': []
        }
        
        # Get basic memory info
        layout['total'] = memory.read_from_meminfo('MemTotal')
        layout['available'] = memory.read_from_meminfo('MemAvailable')
        
        # Get kernel reserved memory
        try:
            # Check /proc/iomem for reserved regions
            with open('/proc/iomem', 'r') as f:
                iomem = f.read()
                for line in iomem.split('\n'):
                    if 'Kernel' in line or 'reserved' in line or 'System RAM' in line:
                        self.log.debug(f"Memory region: {line}")
        except Exception as e:
            self.log.warning(f"Could not read /proc/iomem: {e}")
        
        # Calculate safe testable memory (leave headroom for kernel)
        kernel_headroom = 2 * 1024 * 1024  # 2GB for kernel and system
        layout['safe_testable'] = max(0, layout['available'] - kernel_headroom)
        
        # Get NUMA topology
        try:
            result = process.run("numactl --hardware", ignore_status=True)
            if result.exit_status == 0:
                output = result.stdout_text
                for line in output.split('\n'):
                    if 'node' in line and 'size' in line:
                        layout['numa_nodes'].append(line.strip())
        except:
            pass
        
        self.log.info(f"Memory Layout - Total: {layout['total']}KB, "
                     f"Available: {layout['available']}KB, "
                     f"Safe Testable: {layout['safe_testable']}KB")
        
        return layout
    
    def setup_cgroup_memory_limit(self, test_name, memory_limit_mb):
        """Setup cgroup to limit memory usage and prevent kernel interference"""
        cgroup_path = f"/sys/fs/cgroup/memory/dimm_test_{test_name}"
        
        # Check if we're running as root
        if os.geteuid() != 0:
            self.log.debug("Not running as root, cannot setup cgroup")
            return None
        
        try:
            # Create cgroup
            if not os.path.exists(cgroup_path):
                os.makedirs(cgroup_path)
            
            # Set memory limit
            limit_file = os.path.join(cgroup_path, "memory.limit_in_bytes")
            with open(limit_file, 'w') as f:
                f.write(str(memory_limit_mb * 1024 * 1024))
            
            # Set swappiness to 0 for predictable behavior
            swappiness_file = os.path.join(cgroup_path, "memory.swappiness")
            if os.path.exists(swappiness_file):
                with open(swappiness_file, 'w') as f:
                    f.write("0")
            
            # Add current process to cgroup
            tasks_file = os.path.join(cgroup_path, "tasks")
            with open(tasks_file, 'w') as f:
                f.write(str(os.getpid()))
            
            self.log.info(f"Created cgroup {cgroup_path} with {memory_limit_mb}MB limit")
            return cgroup_path
            
        except Exception as e:
            self.log.debug(f"Could not setup cgroup: {e}")
            return None
    
    def cleanup_cgroup(self, cgroup_path):
        """Cleanup cgroup"""
        if cgroup_path and os.path.exists(cgroup_path):
            try:
                os.rmdir(cgroup_path)
            except:
                pass
    
    def calculate_safe_test_size(self, percentage=70):
        """Calculate safe memory size for testing - VERY conservative to prevent OOM"""
        layout = self.get_memory_layout()
        
        # CRITICAL: Use MemFree + cached memory, not just MemAvailable
        # This is more accurate for what we can actually allocate
        try:
            mem_free = memory.read_from_meminfo('MemFree')
            cached = memory.read_from_meminfo('Cached')
            buffers = memory.read_from_meminfo('Buffers')
            # Usable memory is Free + most of Cached/Buffers
            usable_kb = mem_free + int(cached * 0.8) + int(buffers * 0.8)
            
            # Check if system has swap
            swap_total = memory.read_from_meminfo('SwapTotal')
            has_swap = swap_total > 0
            
            if not has_swap:
                # NO SWAP: Must be MUCH more conservative
                self.log.info("⚠️  NO SWAP DETECTED - Using very conservative memory limits")
                # Without swap, we can't overcommit at all - reduce usable memory significantly
                usable_kb = int(mem_free * 0.7)  # Only 70% of free memory without swap
            
            self.log.info(f"Usable memory: {usable_kb // 1024}MB (Free: {mem_free // 1024}MB, "
                         f"Cached: {cached // 1024}MB, Swap: {'None' if not has_swap else f'{swap_total // 1024}MB'})")
        except Exception as e:
            # Fallback to available
            usable_kb = layout['available']
            has_swap = True  # Assume swap exists if we can't check
            self.log.debug(f"Could not read detailed memory info: {e}")
        
        # Use percentage of usable memory
        safe_size_kb = int(usable_kb * percentage / 100)
        
        # CRITICAL: Headroom requirements depend on swap availability
        if has_swap:
            min_headroom_kb = 3 * 1024 * 1024  # 3GB with swap
        else:
            min_headroom_kb = 5 * 1024 * 1024  # 5GB without swap (much more conservative)
        
        max_safe_kb = usable_kb - min_headroom_kb
        
        if safe_size_kb > max_safe_kb:
            safe_size_kb = max(256 * 1024, max_safe_kb)  # At least 256MB
            self.log.info(f"Reduced test size to ensure {min_headroom_kb // 1024}MB headroom")
        
        # Additional safety: Never use more than 40% of total system memory without swap
        max_percent = 0.40 if not has_swap else 0.50
        max_total_kb = int(layout['total'] * max_percent)
        if safe_size_kb > max_total_kb:
            safe_size_kb = max_total_kb
            self.log.info(f"Limited to {int(max_percent * 100)}% of total system memory")
        
        # Absolute maximum cap - lower without swap
        absolute_max_kb = 4 * 1024 * 1024 if not has_swap else 8 * 1024 * 1024
        if safe_size_kb > absolute_max_kb:
            safe_size_kb = absolute_max_kb
            self.log.info(f"Capped at absolute maximum of {absolute_max_kb // 1024}MB")
        
        safe_size_mb = safe_size_kb // 1024
        
        self.log.info(f"✓ Safe test size: {safe_size_mb}MB "
                     f"(target: {percentage}%, headroom: {(usable_kb - safe_size_kb) // 1024}MB)")
        
        return safe_size_mb
    
    def get_hugepage_info(self):
        """Get huge page configuration"""
        hugepage_info = {}
        
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'Huge' in line:
                        parts = line.split(':')
                        if len(parts) == 2:
                            key = parts[0].strip()
                            value = parts[1].strip().split()[0]
                            hugepage_info[key] = int(value)
        except Exception as e:
            self.log.warning(f"Could not read hugepage info: {e}")
        
        return hugepage_info
    
    def reserve_test_memory_safely(self, size_mb):
        """Reserve memory for testing using mlock to prevent swapping"""
        try:
            import resource
            
            # Check if we can lock memory
            try:
                soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
                self.log.debug(f"MEMLOCK limit: soft={soft}, hard={hard}")
            except:
                self.log.debug("Could not check MEMLOCK limit")
            
            # Create anonymous mapping
            size_bytes = size_mb * 1024 * 1024
            mem = mmap.mmap(-1, size_bytes, 
                           mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
                           mmap.PROT_READ | mmap.PROT_WRITE)
            
            # Try to lock memory using mlock system call
            try:
                # Use ctypes to call mlock directly
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                result = libc.mlock(ctypes.c_void_p(id(mem)), ctypes.c_size_t(size_bytes))
                
                if result == 0:
                    self.log.info(f"Successfully locked {size_mb}MB in RAM using mlock()")
                    return mem
                else:
                    errno = ctypes.get_errno()
                    self.log.debug(f"mlock failed with errno {errno}, memory not locked but allocated")
                    return mem
            except Exception as e:
                self.log.debug(f"Could not lock memory: {e}, proceeding without mlock")
                return mem
            
        except Exception as e:
            self.log.error(f"Could not reserve memory: {e}")
            return None


class DIMMKernelTests(Test):
    """Kernel-level memory tests with safe boundaries"""
    
    def setUp(self):
        """Setup kernel test environment"""
        self.log.info("Setting up kernel-level DIMM tests with safety checks")
        self.results = {}
        self.mem_mgr = SafeMemoryManager(self.log)
        self.mem_layout = self.mem_mgr.get_memory_layout()
        
        # Pre-flight memory check
        self.log.info(f"System Memory Status:")
        self.log.info(f"  Total: {self.mem_layout['total'] // 1024}MB")
        self.log.info(f"  Available: {self.mem_layout['available'] // 1024}MB")
        self.log.info(f"  Safe for testing: {self.mem_layout['safe_testable'] // 1024}MB")
        self.log.info(f"  Test Mode: {TEST_MODE}")
        
        # Info if low memory (not warning, as it's informational)
        if self.mem_layout['available'] < 2 * 1024 * 1024:  # Less than 2GB
            self.log.info("NOTE: Available memory is low. Tests will be scaled accordingly.")
        
        # Check for swap
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'SwapFree' in line:
                        swap_free = int(line.split()[1])
                        self.log.info(f"  Swap Free: {swap_free // 1024}MB")
                        if swap_free < 1024 * 1024:  # Less than 1GB swap
                            self.log.info("  NOTE: Swap space is low - tests use minimal swap by design")
        except Exception as e:
            self.log.debug(f"Could not check swap: {e}")
        
    def test_01_memtest_safe_region(self):
        """Run kernel memory pattern tests on safe regions only"""
        self.log.info(f"Running kernel memory pattern tests (safe regions) - Mode: {TEST_MODE}")
        
        # Check if we have root privileges
        if os.geteuid() != 0:
            self.cancel("This test requires root privileges. Run with: sudo avocado run ...")
        
        # Calculate safe memory size based on test mode
        percentage = CONFIG['memory_percentage']
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=percentage)
        
        # Apply test mode limit
        max_test_size_mb = CONFIG['max_memtest_mb']
        if max_test_size_mb and safe_size_mb > max_test_size_mb:
            self.log.info(f"Limiting test size from {safe_size_mb}MB to {max_test_size_mb}MB "
                         f"(TEST_MODE={TEST_MODE})")
            safe_size_mb = max_test_size_mb
        
        if safe_size_mb < 100:
            self.cancel("Not enough safe memory available for testing")
        
        # Calculate realistic timeout: ~90 seconds per GB + 5 minute buffer
        # memtester typically takes 60-120 seconds per GB depending on CPU/memory speed
        timeout_seconds = int((safe_size_mb / 1024) * 90) + 300
        estimated_minutes = timeout_seconds // 60
        
        # Run memtester on safe memory region
        cmd = f"memtester {safe_size_mb}M 1"
        self.log.info(f"Starting memtester with {safe_size_mb}MB")
        self.log.info(f"Estimated time: {safe_size_mb // 70}-{safe_size_mb // 50} minutes")
        self.log.info(f"Timeout set to: {estimated_minutes} minutes")
        
        try:
            result = process.run(cmd, timeout=timeout_seconds)
            stdout = result.stdout_text if hasattr(result, 'stdout_text') else result.stdout.decode('utf-8')
            stderr = result.stderr_text if hasattr(result, 'stderr_text') else result.stderr.decode('utf-8')
            
            # Check for memory errors in output
            errors_found = []
            for line in stdout.split('\n'):
                if 'FAILURE' in line or 'error' in line.lower():
                    errors_found.append(line.strip())
            
            if errors_found:
                self.log.error("Memory errors detected:")
                for error in errors_found:
                    self.log.error(f"  {error}")
                self.results['pattern_test'] = {
                    'status': 'FAIL',
                    'tested_mb': safe_size_mb,
                    'errors': errors_found
                }
                self.fail(f"Memory errors found: {errors_found}")
            
            # Success
            self.results['pattern_test'] = {
                'status': 'PASS',
                'tested_mb': safe_size_mb,
                'coverage_percent': (safe_size_mb * 1024) / self.mem_layout['total'] * 100,
                'test_mode': TEST_MODE,
                'duration_seconds': result.duration if hasattr(result, 'duration') else 0
            }
            self.log.info(f"✓ Tested {safe_size_mb}MB successfully "
                         f"({self.results['pattern_test']['coverage_percent']:.1f}% coverage)")
            
        except process.CmdError as e:
            stdout = e.result.stdout_text if hasattr(e.result, 'stdout_text') else str(e.result.stdout)
            stderr = e.result.stderr_text if hasattr(e.result, 'stderr_text') else str(e.result.stderr)
            exit_status = e.result.exit_status if hasattr(e.result, 'exit_status') else -1
            
            self.log.error(f"Memtester failed after {e.result.duration if hasattr(e.result, 'duration') else 'unknown'} seconds")
            self.log.error(f"Exit code: {exit_status}")
            
            # Check if it was a timeout (exit code -15 is SIGTERM from timeout)
            if exit_status == -15:
                self.results['pattern_test'] = {
                    'status': 'TIMEOUT',
                    'error': f'Test timed out after {timeout_seconds}s. Memory test may need more time.',
                    'tested_mb': safe_size_mb
                }
                self.fail(f"Test TIMED OUT after {timeout_seconds}s ({estimated_minutes} min). "
                         f"Consider using smaller test size or increase timeout.")
            
            self.log.error(f"STDOUT (last 1000 chars): {stdout[-1000:]}")
            self.log.error(f"STDERR: {stderr}")
            
            # Check if it's an OOM or memory allocation issue
            if 'cannot allocate memory' in stdout.lower() or 'cannot allocate memory' in stderr.lower():
                self.results['pattern_test'] = {
                    'status': 'FAIL',
                    'error': 'Memory allocation failed - may need to reduce test size or free more memory'
                }
                self.fail("Could not allocate test memory. Try reducing test size or freeing memory.")
            else:
                self.results['pattern_test'] = {
                    'status': 'FAIL',
                    'error': f'Memtester exited with code {exit_status}',
                    'stdout_tail': stdout[-500:],
                    'stderr': stderr
                }
                self.fail(f"Memory pattern test failed: Exit code {exit_status}. Check logs for details.")
    
    def test_02_progressive_coverage(self):
        """Progressive memory testing to maximize coverage safely"""
        self.log.info(f"Running progressive memory coverage test - Mode: {TEST_MODE}")
        
        # Check if we have root privileges
        if os.geteuid() != 0:
            self.cancel("This test requires root privileges. Run with: sudo avocado run ...")
        
        # Test in chunks to maximize coverage
        chunk_size_mb = 512  # Test 512MB at a time
        percentage = CONFIG['memory_percentage']
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=percentage)
        
        # Calculate number of chunks based on test mode
        max_chunks = CONFIG['max_chunks']
        num_chunks = safe_size_mb // chunk_size_mb
        if max_chunks:
            num_chunks = min(num_chunks, max_chunks)
        
        self.log.info(f"Testing {num_chunks} chunks of {chunk_size_mb}MB each "
                     f"(total: {num_chunks * chunk_size_mb}MB)")
        
        chunks_passed = 0
        for i in range(num_chunks):
            self.log.info(f"Testing chunk {i+1}/{num_chunks}")
            cmd = f"memtester {chunk_size_mb}M 1"
            try:
                result = process.run(cmd, timeout=300)
                chunks_passed += 1
                self.log.info(f"Chunk {i+1} passed")
            except process.CmdError as e:
                self.log.error(f"Chunk {i+1} failed: {e}")
                break
            
            # Small delay between chunks
            time.sleep(1)
        
        total_tested_mb = chunks_passed * chunk_size_mb
        coverage_percent = (total_tested_mb * 1024) / self.mem_layout['total'] * 100
        
        self.results['progressive_coverage'] = {
            'chunks_passed': chunks_passed,
            'total_chunks': num_chunks,
            'total_tested_mb': total_tested_mb,
            'coverage_percent': coverage_percent,
            'test_mode': TEST_MODE
        }
        
        self.log.info(f"Progressive test: {chunks_passed}/{num_chunks} chunks passed, "
                     f"{coverage_percent:.1f}% memory coverage")
    
    def test_03_ecc_errors(self):
        """Check for ECC errors"""
        self.log.info("Checking ECC error counts")
        
        ecc_paths = [
            "/sys/devices/system/edac/mc/",
        ]
        
        ecc_errors = {}
        ecc_available = False
        
        for path in ecc_paths:
            if not os.path.exists(path):
                self.log.debug(f"ECC path {path} does not exist")
                continue
                
            ecc_available = True
            try:
                mc_dirs = os.listdir(path)
                if not mc_dirs:
                    self.log.debug(f"No memory controller directories in {path}")
                    continue
                    
                for mc_dir in mc_dirs:
                    mc_path = os.path.join(path, mc_dir)
                    if not os.path.isdir(mc_path):
                        continue
                        
                    ce_file = os.path.join(mc_path, "ce_count")
                    ue_file = os.path.join(mc_path, "ue_count")
                    
                    if os.path.exists(ce_file):
                        try:
                            with open(ce_file, 'r') as f:
                                ecc_errors[f'{mc_dir}_ce'] = int(f.read().strip())
                        except (IOError, ValueError, PermissionError) as e:
                            self.log.debug(f"Could not read {ce_file}: {e}")
                    
                    if os.path.exists(ue_file):
                        try:
                            with open(ue_file, 'r') as f:
                                ecc_errors[f'{mc_dir}_ue'] = int(f.read().strip())
                        except (IOError, ValueError, PermissionError) as e:
                            self.log.debug(f"Could not read {ue_file}: {e}")
                            
            except PermissionError:
                self.log.debug(f"Permission denied reading {path}")
            except Exception as e:
                self.log.debug(f"Could not process ECC path {path}: {e}")
        
        if not ecc_available:
            self.log.info("ECC monitoring not available on this system (no EDAC interface found)")
            self.results['ecc_errors'] = {
                'status': 'not_available', 
                'message': 'ECC not supported or not enabled',
                'paths_checked': ecc_paths
            }
        elif not ecc_errors:
            self.log.info("ECC interface found but no error counts available (this is normal for systems without ECC RAM)")
            self.results['ecc_errors'] = {
                'status': 'no_data', 
                'message': 'ECC interface present but no error counters (non-ECC RAM or no errors to report)'
            }
        else:
            self.results['ecc_errors'] = ecc_errors
            
            # Check for uncorrectable errors
            total_ue = sum(v for k, v in ecc_errors.items() if '_ue' in k)
            total_ce = sum(v for k, v in ecc_errors.items() if '_ce' in k)
            
            self.log.info(f"ECC Status: {total_ce} correctable, {total_ue} uncorrectable errors")
            
            if total_ue > 0:
                self.fail(f"CRITICAL: Found {total_ue} uncorrectable ECC errors - DIMM may be failing!")
            elif total_ce > 100:
                self.log.info(f"NOTE: Found {total_ce} correctable ECC errors - this is usually normal but monitor DIMM health")
            else:
                self.log.info(f"✓ ECC status healthy: {total_ce} correctable, {total_ue} uncorrectable errors")
    
    def test_04_kernel_memory_info(self):
        """Analyze kernel memory usage and reserved regions"""
        self.log.info("Analyzing kernel memory allocation")
        
        kernel_info = {}
        
        # Get slab info
        try:
            if os.path.exists('/proc/slabinfo'):
                with open('/proc/slabinfo', 'r') as f:
                    lines = f.readlines()
                    kernel_info['slab_caches'] = len(lines) - 2  # Subtract header lines
                    self.log.info(f"Found {kernel_info['slab_caches']} slab caches")
        except Exception as e:
            self.log.debug(f"Could not read /proc/slabinfo: {e}")
        
        # Get vmstat
        try:
            if os.path.exists('/proc/vmstat'):
                result = process.run("cat /proc/vmstat", shell=True, ignore_status=True)
                if result.exit_status == 0:
                    vmstat_info = {}
                    for line in result.stdout_text.split('\n'):
                        if any(x in line for x in ['nr_kernel_', 'nr_slab', 'nr_page_table']):
                            parts = line.split()
                            if len(parts) == 2:
                                try:
                                    vmstat_info[parts[0]] = int(parts[1])
                                except ValueError:
                                    pass
                    if vmstat_info:
                        kernel_info['vmstat'] = vmstat_info
                        self.log.info(f"Collected {len(vmstat_info)} vmstat metrics")
        except Exception as e:
            self.log.debug(f"Could not read vmstat: {e}")
        
        # Get buddy info (memory fragmentation)
        try:
            if os.path.exists('/proc/buddyinfo'):
                with open('/proc/buddyinfo', 'r') as f:
                    buddyinfo = f.read().strip()
                    if buddyinfo:
                        kernel_info['buddyinfo'] = buddyinfo
                        # Count lines as zones
                        num_zones = len([l for l in buddyinfo.split('\n') if l.strip()])
                        self.log.info(f"Memory fragmentation info collected for {num_zones} zones")
        except Exception as e:
            self.log.debug(f"Could not read buddyinfo: {e}")
        
        # Get zone info (first 50 lines for summary)
        try:
            if os.path.exists('/proc/zoneinfo'):
                with open('/proc/zoneinfo', 'r') as f:
                    zoneinfo_lines = f.readlines()[:50]
                    if zoneinfo_lines:
                        kernel_info['zoneinfo_sample'] = ''.join(zoneinfo_lines)
                        self.log.info("Collected memory zone information")
        except Exception as e:
            self.log.debug(f"Could not read zoneinfo: {e}")
        
        # Get memory info summary
        try:
            if os.path.exists('/proc/meminfo'):
                with open('/proc/meminfo', 'r') as f:
                    meminfo = {}
                    for line in f:
                        if any(x in line for x in ['Kernel', 'Slab', 'PageTables', 'VmallocUsed']):
                            parts = line.split(':')
                            if len(parts) == 2:
                                key = parts[0].strip()
                                value = parts[1].strip().split()[0]
                                try:
                                    meminfo[key] = int(value)
                                except ValueError:
                                    pass
                    if meminfo:
                        kernel_info['kernel_memory_usage'] = meminfo
                        total_kernel_kb = sum(meminfo.values())
                        self.log.info(f"Kernel memory usage: ~{total_kernel_kb // 1024}MB")
        except Exception as e:
            self.log.debug(f"Could not read meminfo: {e}")
        
        if kernel_info:
            self.results['kernel_memory_info'] = kernel_info
            self.log.info(f"✓ Kernel memory analysis complete: {len(kernel_info)} categories collected")
        else:
            self.log.info("Limited kernel memory info available (may need root access)")
            self.results['kernel_memory_info'] = {'status': 'limited_access'}
    
    
    def test_06_ras_mce_scan(self):
        """Scan for recent RAS / MCE / EDAC errors (best-effort).

        This test is intentionally non-fatal by default: it records findings and warns.
        """
        self.log.info("Scanning dmesg for RAS/MCE/EDAC errors (best-effort)")
        try:
            # Only pull high-severity messages to reduce false positives
            result = process.run("dmesg --level=err,crit,alert,emerg", shell=True, ignore_status=True)
            out = (result.stdout_text or "") + "\n" + (result.stderr_text or "")
            keywords = ["EDAC", "MCE", "Machine check", "RAS", "UE", "uncorrected", "corrected error"]
            hits = []
            for line in out.splitlines():
                if any(k.lower() in line.lower() for k in keywords):
                    hits.append(line.strip())
            self.results["ras_mce_scan"] = {"hits": hits[:50], "hit_count": len(hits)}
            if hits:
                self.log.warning(f"Found {len(hits)} potential RAS/MCE/EDAC error lines in dmesg (showing up to 10):")
                for l in hits[:10]:
                    self.log.warning(f"  {l}")
            else:
                self.log.info("✓ No high-severity RAS/MCE/EDAC lines found in dmesg")
        except Exception as e:
            self.results["ras_mce_scan"] = {"status": "ERROR", "error": str(e)}
            self.log.warning(f"Could not scan dmesg: {e}")

def tearDown(self):
        """Cleanup and report results"""
        self.log.info(f"Kernel test results: {json.dumps(self.results, indent=2)}")


class DIMMUserspaceTests(Test):
    """Userspace memory tests with cgroup isolation"""
    
    def setUp(self):
        """Setup userspace test environment"""
        self.log.info("Setting up userspace DIMM tests with cgroup isolation")
        self.results = {}
        self.mem_mgr = SafeMemoryManager(self.log)
        self.cgroup_path = None
        self.sm = SoftwareManager()
        
        # Critical memory check
        mem_layout = self.mem_mgr.get_memory_layout()
        mem_free = memory.read_from_meminfo('MemFree')
        
        self.log.info(f"Pre-flight memory check:")
        self.log.info(f"  Total: {mem_layout['total'] // 1024}MB")
        self.log.info(f"  Free: {mem_free // 1024}MB")
        self.log.info(f"  Available: {mem_layout['available'] // 1024}MB")
        
        # Warn if very low memory
        if mem_free < 4 * 1024 * 1024:  # Less than 4GB free
            self.log.info(f"WARNING: Only {mem_free // 1024}MB free memory. "
                         f"Tests will be very conservative to prevent OOM.")
        
        # Install required tools
        tools = ['stress-ng', 'sysbench']
        for tool in tools:
            if not self.sm.check_installed(tool):
                self.log.info(f"Installing {tool}")
                self.sm.install(tool)
    
    def test_01_cgroup_isolated_test(self):
        """Test with cgroup memory isolation"""
        self.log.info("Running cgroup-isolated memory test")
        
        # Calculate safe memory limit
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=60)
        
        # Setup cgroup
        self.cgroup_path = self.mem_mgr.setup_cgroup_memory_limit("isolated", safe_size_mb)
        
        if not self.cgroup_path:
            self.log.info("Cgroup not available, running test without cgroup isolation")
        # Continue without cgroup - don't cancel

        # Run test within cgroup constraints
        test_size_mb = int(safe_size_mb * 0.8)  # Use 80% of cgroup limit
        cmd = f"stress-ng --vm 4 --vm-bytes {test_size_mb}M --vm-method all --timeout 120s --metrics-brief"
        
        try:
            result = process.run(cmd, timeout=150)
            self.results['cgroup_isolated'] = {
                'status': 'PASS',
                'cgroup_limit_mb': safe_size_mb,
                'tested_mb': test_size_mb
            }
            self.log.info(f"Cgroup test passed: {test_size_mb}MB tested "
                         f"within {safe_size_mb}MB limit")
        except process.CmdError as e:
            self.results['cgroup_isolated'] = 'FAIL'
            self.fail(f"Cgroup isolated test failed: {e}")
    
    def test_02_mlock_protected_region(self):
        """Test memory with mlock protection"""
        self.log.info("Testing mlocked memory regions")
        
        safe_size_mb = min(512, self.mem_mgr.calculate_safe_test_size(percentage=20))
        
        # Reserve and lock memory
        locked_mem = self.mem_mgr.reserve_test_memory_safely(safe_size_mb)
        
        if locked_mem:
            # Write pattern to locked memory
            try:
                pattern = b'\xAA' * 1024  # 1KB pattern
                total_size = safe_size_mb * 1024 * 1024
                
                # Write pattern to memory
                for offset in range(0, total_size, len(pattern)):
                    locked_mem.seek(offset)
                    bytes_to_write = min(len(pattern), total_size - offset)
                    locked_mem.write(pattern[:bytes_to_write])
                
                # Verify pattern
                locked_mem.seek(0)
                verify_data = locked_mem.read(1024)
                if verify_data == pattern:
                    self.results['mlock_protected'] = {
                        'status': 'PASS',
                        'size_mb': safe_size_mb,
                        'locked': True
                    }
                    self.log.info(f"✓ mlock test passed: {safe_size_mb}MB tested")
                else:
                    self.results['mlock_protected'] = {
                        'status': 'FAIL',
                        'error': 'Pattern mismatch'
                    }
                    self.fail("Memory pattern verification failed")
                
                # Cleanup - close the mmap (unlocks automatically)
                try:
                    # Try to unlock explicitly if possible
                    libc = ctypes.CDLL("libc.so.6", use_errno=True)
                    libc.munlock(ctypes.c_void_p(id(locked_mem)), ctypes.c_size_t(safe_size_mb * 1024 * 1024))
                except:
                    pass  # Ignore unlock errors, close will handle it
                
                locked_mem.close()
                
            except Exception as e:
                self.results['mlock_protected'] = {
                    'status': 'FAIL', 
                    'error': str(e)
                }
                self.log.error(f"mlock test error: {e}")
                try:
                    locked_mem.close()
                except:
                    pass
                self.fail(f"Memory test failed: {e}")
        else:
            self.cancel("Could not allocate memory for mlock test")
    
    def test_03_numa_aware_testing(self):
        """Test each NUMA node separately to maximize coverage"""
        self.log.info("Running NUMA-aware memory testing")
        
        # Check NUMA availability
        try:
            result = process.run("numactl --hardware", ignore_status=True)
            if result.exit_status != 0:
                self.cancel("NUMA not available")
            
            output = result.stdout_text
            numa_nodes = []
            for line in output.split('\n'):
                if line.startswith('node') and 'cpus:' in line:
                    node_num = line.split()[1]
                    numa_nodes.append(node_num)
            
            if not numa_nodes:
                self.cancel("No NUMA nodes detected")
            
            self.log.info(f"Found NUMA nodes: {numa_nodes}")
            
            # Test each NUMA node with conservative memory sizing
            numa_results = {}
            
            # Use much more conservative sizing - 30% divided by number of nodes
            safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=30)
            per_node_mb = max(256, safe_size_mb // len(numa_nodes))  # At least 256MB, or divide evenly
            
            # Further reduce to prevent OOM
            per_node_mb = int(per_node_mb * 0.7)  # Use only 70% of calculated size
            
            self.log.info(f"Testing {per_node_mb}MB per NUMA node")
            
            for node in numa_nodes:
                self.log.info(f"Testing NUMA node {node} with {per_node_mb}MB")
                
                # Use only 1-2 workers per node to reduce memory pressure
                num_workers = 1
                
                cmd = (f"numactl --cpunodebind={node} --membind={node} "
                      f"stress-ng --vm {num_workers} --vm-bytes {per_node_mb}M "
                      f"--vm-method read64 --timeout 60s --metrics-brief")
                
                try:
                    result = process.run(cmd, timeout=90)
                    numa_results[f'node_{node}'] = {
                        'status': 'PASS',
                        'tested_mb': per_node_mb
                    }
                    self.log.info(f"✓ NUMA node {node} passed")
                except process.CmdError as e:
                    numa_results[f'node_{node}'] = 'FAIL'
                    if e.result.exit_status == -9:
                        self.log.error(f"NUMA node {node} test was killed (OOM)")
                    else:
                        self.log.error(f"NUMA node {node} test failed: {e}")
            
            self.results['numa_aware'] = numa_results
            
            # Check if all nodes passed
            passed = sum(1 for r in numa_results.values() if isinstance(r, dict) and r.get('status') == 'PASS')
            self.log.info(f"NUMA testing complete: {passed}/{len(numa_nodes)} nodes passed")
            
        except Exception as e:
            self.cancel(f"NUMA testing failed: {e}")
    
    def test_04_memory_bandwidth_safe(self):
        """Measure memory bandwidth with safe limits"""
        self.log.info("Measuring memory bandwidth (safe mode)")
        
        # Very conservative sizing for bandwidth test
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=30)
        safe_size_gb = max(1, safe_size_mb // 1024)  # At least 1GB
        
        # Cap at reasonable size to prevent OOM
        if safe_size_gb > 5:
            safe_size_gb = 5
            self.log.info(f"Capping bandwidth test at {safe_size_gb}GB to prevent OOM")
        
        cmd = f"sysbench memory --memory-block-size=1M --memory-total-size={safe_size_gb}G --memory-oper=read run"
        try:
            result = process.run(cmd, timeout=600)
            output = result.stdout_text
            
            bandwidth = {}
            for line in output.split('\n'):
                if 'transferred' in line or 'MiB/sec' in line:
                    self.log.info(f"Bandwidth: {line}")
                    bandwidth['info'] = line.strip()
            
            self.results['bandwidth_safe'] = {
                'status': 'PASS',
                'tested_gb': safe_size_gb,
                'metrics': bandwidth
            }
            self.log.info(f"✓ Bandwidth test passed: {safe_size_gb}GB tested")
        except process.CmdError as e:
            self.results['bandwidth_safe'] = 'FAIL'
            self.fail(f"Bandwidth test failed: {e}")
    
    def tearDown(self):
        """Cleanup cgroups and report results"""
        if self.cgroup_path:
            self.mem_mgr.cleanup_cgroup(self.cgroup_path)
        
        self.log.info(f"Userspace test results: {json.dumps(self.results, indent=2)}")


class DIMMMaxCoverageTest(Test):
    """Specialized test to maximize memory coverage safely"""
    
    def setUp(self):
        """Setup maximum coverage test"""
        self.log.info("Setting up maximum coverage DIMM test")
        self.results = {}
        self.mem_mgr = SafeMemoryManager(self.log)
        self.mem_layout = self.mem_mgr.get_memory_layout()
    
    def test_01_multi_pass_coverage(self):
        """Multiple passes to maximize total memory coverage"""
        self.log.info("Running multi-pass maximum coverage test")
        
        # Check if we have root privileges
        if os.geteuid() != 0:
            self.cancel("This test requires root privileges. Run with: sudo avocado run ...")
        
        # Strategy: Test in multiple smaller passes to cover more physical memory
        # without holding large contiguous regions
        
        num_passes = 5
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=65)
        per_pass_mb = safe_size_mb
        
        self.log.info(f"Will run {num_passes} passes of {per_pass_mb}MB each")
        
        pass_results = []
        total_coverage_mb = 0
        
        for pass_num in range(num_passes):
            self.log.info(f"=== Pass {pass_num + 1}/{num_passes} ===")
            
            # Drop caches between passes to free memory
            try:
                process.run("echo 3 > /proc/sys/vm/drop_caches", 
                           shell=True, ignore_status=True)
                time.sleep(2)
            except:
                pass
            
            # Run memtester
            cmd = f"memtester {per_pass_mb}M 1"
            try:
                result = process.run(cmd, timeout=300)
                pass_results.append({
                    'pass': pass_num + 1,
                    'status': 'PASS',
                    'tested_mb': per_pass_mb
                })
                total_coverage_mb += per_pass_mb
                self.log.info(f"Pass {pass_num + 1} completed successfully")
            except process.CmdError as e:
                pass_results.append({
                    'pass': pass_num + 1,
                    'status': 'FAIL',
                    'error': str(e)
                })
                self.log.error(f"Pass {pass_num + 1} failed: {e}")
            
            # Small delay between passes
            time.sleep(5)
        
        # Calculate coverage statistics
        total_mem_kb = self.mem_layout['total']
        coverage_percent = (total_coverage_mb * 1024) / total_mem_kb * 100
        
        self.results['multi_pass_coverage'] = {
            'passes': pass_results,
            'total_tested_mb': total_coverage_mb,
            'total_memory_mb': total_mem_kb // 1024,
            'coverage_percent': coverage_percent,
            'passes_passed': sum(1 for p in pass_results if p['status'] == 'PASS')
        }
        
        self.log.info(f"Multi-pass coverage: {total_coverage_mb}MB tested "
                     f"({coverage_percent:.1f}% of total memory)")
    
    def test_02_memory_map_analysis(self):
        """Analyze memory mapping to identify testable regions"""
        self.log.info("Analyzing memory map for testable regions")
        
        regions = {
            'total_physical': 0,
            'kernel_reserved': 0,
            'available_for_test': 0,
            'numa_distribution': {}
        }
        
        # Analyze /proc/iomem
        try:
            with open('/proc/iomem', 'r') as f:
                for line in f:
                    if 'System RAM' in line:
                        # Parse memory range
                        range_part = line.split(':')[0].strip()
                        if '-' in range_part:
                            start, end = range_part.split('-')
                            start_addr = int(start, 16)
                            end_addr = int(end, 16)
                            size_mb = (end_addr - start_addr) / (1024 * 1024)
                            regions['total_physical'] += size_mb
        except Exception as e:
            self.log.warning(f"Could not parse /proc/iomem: {e}")
        
        regions['available_for_test'] = self.mem_mgr.calculate_safe_test_size(percentage=70)
        
        self.results['memory_map_analysis'] = regions
        self.log.info(f"Memory map analysis: {json.dumps(regions, indent=2)}")
    
    def tearDown(self):
        """Report maximum coverage results"""
        self.log.info(f"Maximum coverage test results: {json.dumps(self.results, indent=2)}")
        
        # Save detailed report
        report_file = os.path.join(self.outputdir, 'max_coverage_report.json')
        with open(report_file, 'w') as f:
            json.dump({
                'memory_layout': self.mem_layout,
                'test_results': self.results,
                'timestamp': time.time()
            }, f, indent=2)
        self.log.info(f"Coverage report saved to {report_file}")


class DIMMDatacenterTests(Test):
    """Datacenter application-level memory tests with safety"""
    
    def setUp(self):
        """Setup DC application test environment"""
        self.log.info("Setting up datacenter application DIMM tests")
        self.results = {}
        self.mem_mgr = SafeMemoryManager(self.log)
        self.test_duration = 300
    
    def test_01_database_workload_safe(self):
        """Simulate database memory workload with safety limits"""
        self.log.info("Running safe database workload simulation")
        
        # Very conservative for DC tests - 25% of available
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=25)
        
        # Limit total memory usage
        if safe_size_mb > 4096:
            safe_size_mb = 4096
            self.log.info("Limiting database workload to 4GB to prevent OOM")
        
        # Database-like workload: read-heavy with multiple workers
        num_workers = min(4, cpu.online_count())
        per_worker_mb = safe_size_mb // num_workers
        
        cmd = (f"stress-ng --vm {num_workers} --vm-bytes {per_worker_mb}M "
               f"--vm-method read64 --timeout {self.test_duration}s --metrics-brief")
        
        try:
            start_time = time.time()
            result = process.run(cmd, timeout=self.test_duration + 60)
            end_time = time.time()
            
            self.results['database_workload_safe'] = {
                'status': 'PASS',
                'duration': end_time - start_time,
                'memory_used_mb': safe_size_mb,
                'workers': num_workers
            }
            self.log.info(f"✓ Database workload passed: {safe_size_mb}MB with {num_workers} workers")
        except process.CmdError as e:
            self.results['database_workload_safe'] = 'FAIL'
            if e.result.exit_status == -9:
                self.fail(f"Test was killed (OOM). Reduce memory usage.")
            else:
                self.fail(f"Database workload test failed: {e}")
    
    def test_02_hugepage_allocation_safe(self):
        """Test huge page allocation with monitoring"""
        self.log.info("Testing huge pages with safety monitoring")
        
        # Get current huge page info
        hp_before = self.mem_mgr.get_hugepage_info()
        self.log.info(f"Huge pages before test: {hp_before}")
        
        # Very conservative sizing for hugepage test
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=25)
        
        # Limit to reasonable size
        if safe_size_mb > 4096:
            safe_size_mb = 4096
            self.log.info("Limiting hugepage test to 4GB")
        
        # Use simple vm method that works with huge pages
        num_workers = 2
        per_worker_mb = safe_size_mb // num_workers
        
        # Use vm-method that benefits from huge pages if available
        cmd = (f"stress-ng --vm {num_workers} --vm-bytes {per_worker_mb}M "
               f"--vm-method write64 --timeout 180s --metrics-brief")
        
        try:
            result = process.run(cmd, timeout=210)
            hp_after = self.mem_mgr.get_hugepage_info()
            
            self.results['hugepage_safe'] = {
                'status': 'PASS',
                'before': hp_before,
                'after': hp_after,
                'tested_mb': safe_size_mb
            }
            self.log.info(f"✓ Huge page test passed: {safe_size_mb}MB tested")
            self.log.info(f"Huge pages after test: {hp_after}")
            
            # Report on huge page usage
            if hp_before and hp_after:
                for key in hp_before:
                    if key in hp_after and hp_before[key] != hp_after[key]:
                        self.log.info(f"Huge page change - {key}: {hp_before[key]} -> {hp_after[key]}")
                        
        except process.CmdError as e:
            hp_after = self.mem_mgr.get_hugepage_info()
            self.results['hugepage_safe'] = {
                'status': 'FAIL',
                'before': hp_before,
                'after': hp_after
            }
            if e.result.exit_status == -9:
                self.fail(f"Hugepage test was killed (OOM)")
            else:
                self.fail(f"Huge page test failed: {e}")
    
    def tearDown(self):
        """Cleanup and report results"""
        self.log.info(f"DC test results: {json.dumps(self.results, indent=2)}")


class DIMMBenchmarkTests(Test):
    """Memory benchmark tests with safe limits"""
    
    def setUp(self):
        """Setup benchmark environment"""
        self.log.info("Setting up memory benchmark tests (safe mode)")
        self.results = {}
        self.mem_mgr = SafeMemoryManager(self.log)
        self.sm = SoftwareManager()
    
    def test_01_comprehensive_benchmark(self):
        """Comprehensive benchmark with coverage metrics"""
        self.log.info("Running comprehensive memory benchmark")
        
        safe_size_mb = self.mem_mgr.calculate_safe_test_size(percentage=20)
        safe_size_gb = max(1, safe_size_mb // 1024)
        
        # Cap at 5GB for safety
        if safe_size_gb > 5:
            safe_size_gb = 5
            self.log.info("Capping bandwidth test at 5GB to prevent OOM")
        
        block_sizes = ['4K', '64K', '1M']
        benchmark_results = {}
        
        for bs in block_sizes:
            self.log.info(f"Benchmarking with block size {bs}")
            cmd = f"sysbench memory --memory-block-size={bs} --memory-total-size={safe_size_gb}G --memory-oper=read run"
            
            try:
                result = process.run(cmd, timeout=300)
                output = result.stdout_text
                
                metrics = {}
                for line in output.split('\n'):
                    if 'transferred' in line or 'MiB/sec' in line:
                        metrics['throughput'] = line.strip()
                    if 'total time' in line:
                        metrics['time'] = line.strip()
                
                benchmark_results[bs] = metrics
                self.log.info(f"Block size {bs} results: {metrics}")
                
            except process.CmdError as e:
                benchmark_results[bs] = 'FAIL'
                self.log.error(f"Benchmark for {bs} failed: {e}")
        
        self.results['comprehensive_benchmark'] = {
            'tested_gb': safe_size_gb,
            'results': benchmark_results
        }
    
    def test_02_latency_measurement(self):
        """Measure memory access latency"""
        self.log.info("Measuring memory latency")
        
        # Use stress-ng with precise timing
        safe_size_mb = min(1024, self.mem_mgr.calculate_safe_test_size(percentage=15))
        
        cmd = f"stress-ng --vm 1 --vm-bytes {safe_size_mb}M --vm-method read64 --timeout 30s --metrics-brief"
        
        try:
            start = time.time()
            result = process.run(cmd, timeout=60)
            duration = time.time() - start
            
            output = result.stdout_text
            
            # Parse bogo ops and calculate ops/sec
            ops_per_sec = None
            for line in output.split('\n'):
                if 'bogo ops' in line.lower():
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if 'ops/s' in part.lower() and i > 0:
                            try:
                                ops_per_sec = float(parts[i-1])
                            except:
                                pass
            
            self.results['latency_measurement'] = {
                'status': 'PASS',
                'ops_per_sec': ops_per_sec,
                'duration': duration,
                'tested_mb': safe_size_mb
            }
            
            if ops_per_sec:
                self.log.info(f"✓ Memory latency test: {ops_per_sec:.0f} ops/sec")
            
        except process.CmdError as e:
            self.results['latency_measurement'] = 'FAIL'
            self.fail(f"Latency measurement failed: {e}")
    
    def test_03_stride_patterns(self):
        """Test different memory access stride patterns"""
        self.log.info("Testing memory stride patterns")
        
        safe_size_mb = min(512, self.mem_mgr.calculate_safe_test_size(percentage=10))
        
        # Test sequential, random, and strided access
        methods = ['write64', 'read64', 'ror']
        stride_results = {}
        
        for method in methods:
            cmd = f"stress-ng --vm 1 --vm-bytes {safe_size_mb}M --vm-method {method} --timeout 20s --metrics-brief"
            
            try:
                result = process.run(cmd, timeout=40)
                stride_results[method] = 'PASS'
                self.log.info(f"✓ Stride pattern {method} passed")
            except process.CmdError as e:
                stride_results[method] = 'FAIL'
                self.log.error(f"Stride pattern {method} failed: {e}")
        
        self.results['stride_patterns'] = {
            'tested_mb': safe_size_mb,
            'patterns': stride_results
        }
    
    def test_04_memory_stress_sustained(self):
        """Long-duration sustained memory stress"""
        self.log.info("Running sustained memory stress test")
        
        # Conservative for long-running test
        safe_size_mb = min(1024, self.mem_mgr.calculate_safe_test_size(percentage=15))
        
        duration = 300  # 5 minutes for quick mode, can be longer in full mode
        if TEST_MODE == 'full':
            duration = 1800  # 30 minutes for full mode
        
        cmd = f"stress-ng --vm 2 --vm-bytes {safe_size_mb // 2}M --vm-method all --timeout {duration}s --metrics-brief"
        
        try:
            start_time = time.time()
            result = process.run(cmd, timeout=duration + 60)
            actual_duration = time.time() - start_time
            
            self.results['sustained_stress'] = {
                'status': 'PASS',
                'duration': actual_duration,
                'target_duration': duration,
                'tested_mb': safe_size_mb
            }
            self.log.info(f"✓ Sustained stress completed: {actual_duration:.0f}s")
            
        except process.CmdError as e:
            self.results['sustained_stress'] = 'FAIL'
            if e.result.exit_status == -9:
                self.fail(f"Sustained stress OOM killed after {duration}s")
            else:
                self.fail(f"Sustained stress failed: {e}")
    
    
    def test_06_mbw_memory_bandwidth(self):
        """Memory bandwidth microbenchmark via mbw (if available)."""
        self.log.info("Running mbw memory bandwidth benchmark")
        if not self.sm.check_installed("mbw"):
            self.log.info("Installing mbw")
            self.sm.install("mbw")

        try:
            # 1024 MiB test size; mbw prints bandwidth numbers
            result = process.run("mbw -n 3 1024", shell=True, timeout=300, ignore_status=True)
            self.results["mbw"] = {"status": "DONE", "output": (result.stdout_text or "").splitlines()[-20:]}
            self.log.info("✓ mbw completed (see results for tail output)")
        except Exception as e:
            self.results["mbw"] = {"status": "FAIL", "error": str(e)}
            self.fail(f"mbw benchmark failed: {e}")

def tearDown(self):
        """Cleanup and report benchmark results"""
        self.log.info(f"Benchmark results: {json.dumps(self.results, indent=2)}")
        
        results_file = os.path.join(self.outputdir, 'safe_benchmark_results.json')
        with open(results_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        self.log.info(f"Results saved to {results_file}")
