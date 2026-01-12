"""
NVMe/SSD Storage Qualification Test Suite for Avocado Framework
Comprehensive storage testing across kernel, userspace (SPDK), and datacenter application levels

Configuration:
    TEST_MODE environment variable controls test intensity:
    - 'quick'  : Fast tests, minimal I/O (~10 min)
    - 'normal' : Moderate tests, good coverage (~1-2 hours)
    - 'full'   : Comprehensive tests, maximum coverage (4-8 hours)
    
    TEST_DEVICE environment variable specifies the device to test:
    - Set to block device path: /dev/nvme0n1, /dev/sda, etc.
    - For SPDK tests: PCIe address like 0000:01:00.0
    - WARNING: Write tests will destroy data!
    
    SPDK_PATH environment variable (optional):
    - Path to SPDK installation (default: /usr/local/src/spdk)
    
Usage:
    export TEST_MODE=quick
    export TEST_DEVICE=/dev/nvme0n1
    export SPDK_PATH=/usr/local/src/spdk
    sudo avocado run storage_test_suite.py
"""

import os
import sys
import time
import json
import subprocess
import re
import glob
from avocado import Test
from avocado.utils import process

# Get configuration from environment
TEST_MODE = os.environ.get('TEST_MODE', 'quick').lower()
TEST_DEVICE = os.environ.get('TEST_DEVICE', None)
SPDK_PATH = os.environ.get('SPDK_PATH', '/usr/local/src/spdk')
TEST_PCI_ADDR = os.environ.get('TEST_PCI_ADDR', None)
# If set, enables potentially destructive operations (format/discard/write) beyond read-only
TEST_DESTRUCTIVE = os.environ.get('TEST_DESTRUCTIVE', '0').lower() in ('1', 'true', 'yes')
# Filesystem/app-level tests run under this directory (mount your target device here for best coverage)
TEST_FS_DIR = os.environ.get('TEST_FS_DIR', '/var/tmp/avocado_storage_fs')


# Test mode configurations
TEST_CONFIGS = {
    'quick': {
        'full_disk_passes': 1,          # Number of full disk passes
        'io_size_gb': 10,               # GB to test per workload
        'fio_runtime': 60,              # 60 seconds per fio test
        'num_jobs': 4,                  # Parallel jobs
        'block_sizes': ['4k', '128k'],  # Quick block size sweep
        'queue_depths': [1, 32, 128],   # Quick QD sweep
    },
    'normal': {
        'full_disk_passes': 2,
        'io_size_gb': 50,
        'fio_runtime': 300,
        'num_jobs': 8,
        'block_sizes': ['4k', '16k', '64k', '128k', '1m'],
        'queue_depths': [1, 4, 16, 32, 64, 128],
    },
    'full': {
        'full_disk_passes': 3,
        'io_size_gb': 200,
        'fio_runtime': 600,
        'num_jobs': 16,
        'block_sizes': ['4k', '8k', '16k', '32k', '64k', '128k', '256k', '512k', '1m'],
        'queue_depths': [1, 2, 4, 8, 16, 32, 64, 128, 256],
    }
}

# Avoid nested sudo when already running as root
RUNNING_AS_ROOT = (os.geteuid() == 0)
SUDO = not RUNNING_AS_ROOT

CONFIG = TEST_CONFIGS.get(TEST_MODE, TEST_CONFIGS['quick'])

PCI_BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")


def is_pcie_bdf(value: str) -> bool:
    return bool(value) and bool(PCI_BDF_RE.fullmatch(value.strip()))


def normalize_pcie_bdf(value: str) -> str:
    return value.strip().lower()

import fcntl

def _lock_path_for_device(dev: str) -> str:
    base = os.path.basename(dev).replace("/", "_")
    return f"/tmp/storage_test_suite.lock.{base}"

def acquire_device_lock(log, dev: str):
    path = _lock_path_for_device(dev)
    fd = open(path, "w")
    log.info(f"Acquiring exclusive device lock: {path}")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
    log.info("Device lock acquired")
    return fd

def release_device_lock(log, fd):
    if not fd:
        return
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()
        log.info("Device lock released")

def spdk_reset_if_available(log):
    setup = os.path.join(SPDK_PATH, "scripts", "setup.sh")
    if os.path.exists(setup):
        try:
            process.run(f"{setup} reset", sudo=SUDO, shell=True, timeout=30, ignore_status=True)
            log.info("SPDK reset done (kernel driver restored)")
        except Exception as e:
            log.debug(f"SPDK reset failed (ignored): {e}")

# Software manager
try:
    from avocado.utils.software_manager.manager import SoftwareManager
except ImportError:
    try:
        from avocado.utils.software_manager import SoftwareManager
    except ImportError:
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


class StorageDeviceManager:
    """Manager for storage device discovery and safety checks"""
    
    def __init__(self, log):
        self.log = log
    
    def discover_nvme_devices(self):
        """Discover all NVMe devices"""
        nvme_devices = []
        try:
            result = process.run("lsblk -d -n -o NAME,TYPE", shell=True)
            for line in result.stdout_text.split('\n'):
                parts = line.split()
                if parts and parts[0].startswith('nvme'):
                    device = f"/dev/{parts[0]}"
                    nvme_devices.append(device)
                    self.log.info(f"Found NVMe device: {device}")
        except Exception as e:
            self.log.debug(f"Could not discover devices: {e}")
        return nvme_devices
    
    def get_device_size_gb(self, device):
        """Get device size in GB"""
        try:
            result = process.run(f"blockdev --getsize64 {device}", sudo=SUDO)
            size_bytes = int(result.stdout_text.strip())
            return size_bytes / (1024**3)
        except:
            return 0
    
    def _get_pcie_address(self, device):
        """Get PCIe address for NVMe device (needed for SPDK)"""
        try:
            device_name = os.path.basename(device)
            # Follow symlink to get real device path
            real_path = os.path.realpath(f"/sys/block/{device_name}")
            # Extract PCIe address from path like /sys/devices/pci0000:00/0000:00:01.0/...
            match = re.search(r'/([\da-f]{4}:[\da-f]{2}:[\da-f]{2}\.\d)/', real_path)
            if match:
                return match.group(1)
        except Exception as e:
            self.log.debug(f"Could not get PCIe address: {e}")
        return None
   
    def get_pcie_address(self, device: str):
        """Return PCI BDF (dddd:bb:dd.f) for a local NVMe device.

        Prefer NVMe controller sysfs 'address' (most reliable).
        Fallback to extracting BDFs from resolved sysfs paths.
        """
        dev = os.path.basename(device)
        # 1) Best: map namespace -> controller -> /sys/class/nvme/<ctrl>/address
        try:
            dev_link = f"/sys/class/block/{dev}/device"
            if os.path.exists(dev_link):
                real = os.path.realpath(dev_link)
                # real path usually contains ".../nvme/nvme0/nvme0n1"
                m = re.search(r"/nvme/(nvme\d+)(?:/|$)", real)
                if m:
                    ctrl = m.group(1)
                    for addr_path in (
                        f"/sys/class/nvme/{ctrl}/address",
                        f"/sys/class/nvme/{ctrl}/device/address",
                    ):
                        if os.path.exists(addr_path):
                            with open(addr_path, "r", encoding="utf-8", errors="ignore") as fh:
                                bdf = fh.read().strip().lower()
                            if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", bdf):
                                return bdf
        except Exception:
            pass

        # 2) Fallback: extract any BDFs from resolved sysfs path; pick the last one (endpoint)
        try:
            for cand in (f"/sys/class/block/{dev}/device", f"/sys/block/{dev}"):
                if os.path.exists(cand):
                    real = os.path.realpath(cand)
                    bdfs = re.findall(r"([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])", real)
                    if bdfs:
                        return bdfs[-1].lower()
        except Exception:
            pass

        return None

    def check_device_safety(self, device):
        """Check if device is safe to test.

        Notes:
          - We treat *any* mounted partition on the device as unsafe.
          - We treat the root filesystem device (including its parent disk) as unsafe.
        """
        warnings = []
        is_safe = True

        dev = device

        # 1) Mounted checks (device or any of its children partitions)
        try:
            # lsblk is more reliable than grepping mount output
            result = process.run(f"lsblk -J -o NAME,TYPE,MOUNTPOINT,PKNAME {dev}", shell=True, ignore_status=True)
            if result.exit_status == 0 and result.stdout_text.strip():
                import json as _json
                data = _json.loads(result.stdout_text)
                # If any node in the subtree has a mountpoint, mark unsafe
                def _walk(nodes):
                    for n in nodes:
                        yield n
                        for c in n.get('children', []) or []:
                            yield from _walk([c])
                nodes = data.get('blockdevices', []) or []
                for n in _walk(nodes):
                    mp = n.get('mountpoint')
                    if mp:
                        warnings.append(f"Mounted: {n.get('name')} at {mp}")
                        is_safe = False
                        break
        except Exception:
            # Fallback: coarse check
            try:
                result = process.run(f"mount | grep -F '{dev}'", shell=True, ignore_status=True)
                if result.exit_status == 0:
                    warnings.append("Device appears mounted")
                    is_safe = False
            except Exception:
                pass

        # 2) Root filesystem check
        try:
            result = process.run("findmnt -no SOURCE /", shell=True, ignore_status=True)
            root_source = (result.stdout_text or "").strip()
            # root_source may be /dev/nvme2n1p2; consider its parent disk unsafe too
            if root_source:
                if dev == root_source or dev in root_source or root_source in dev:
                    warnings.append("Device overlaps with root filesystem source")
                    is_safe = False
                else:
                    # Parent disk of root_source
                    parent = process.run(f"lsblk -no PKNAME {root_source}", shell=True, ignore_status=True).stdout_text.strip()
                    if parent and os.path.basename(dev) == parent:
                        warnings.append("Device is parent of root filesystem partition")
                        is_safe = False
        except Exception:
            pass

        return is_safe, warnings



class StorageKernelTests(Test):
    """Kernel-level storage tests using standard kernel driver (fio with ioengine=libaio)"""
    
    def setUp(self):

        """Setup kernel test environment"""
        if not RUNNING_AS_ROOT:
            self.cancel("This test suite must be run as root (use: sudo -E avocado run ...)")

        self.log.info("=== Kernel-Level Tests (Standard Kernel Driver) ===")
        self.results = {}
        self.dev_mgr = StorageDeviceManager(self.log)
        self.sm = SoftwareManager()
        
        # Install required tools
        for tool in ['fio', 'smartmontools', 'nvme-cli']:
            if not self.sm.check_installed(tool):
                self.log.info(f"Installing {tool}")
                self.sm.install(tool)
        
        if TEST_DEVICE:
            self.test_device = TEST_DEVICE
        else:
            devices = self.dev_mgr.discover_nvme_devices()
            if not devices:
                self.cancel("No NVMe devices found. Set TEST_DEVICE=/dev/nvmeXnY")
            self.test_device = devices[0]
        
        self.log.info(f"Testing device: {self.test_device}")
        self.device_size_gb = self.dev_mgr.get_device_size_gb(self.test_device)
        self.log.info(f"Device size: {self.device_size_gb:.1f} GB")
        
        is_safe, warnings = self.dev_mgr.check_device_safety(self.test_device)
        if warnings:
            for warning in warnings:
                self.log.info(f"⚠️  {warning}")
        if not is_safe:
            self.cancel(f"Device not safe to test: {warnings}")
    
    def test_01_full_disk_sequential_write(self):
        """Full disk sequential write test - tests entire capacity"""
        self.log.info("Running FULL DISK sequential write test")
        
        num_passes = CONFIG['full_disk_passes']
        total_size_gb = int(self.device_size_gb * 0.95)  # Use 95% of disk
        if TEST_MODE == 'quick':
            total_size_gb = min(total_size_gb, CONFIG['io_size_gb'])

        self.log.info(f"Will write {total_size_gb}GB across {num_passes} pass(es)")
        
        pass_results = []
        
        for pass_num in range(num_passes):
            self.log.info(f"=== Pass {pass_num + 1}/{num_passes} ===")
            
            fio_cmd = f"""fio --name=full_seq_write_p{pass_num} \
                --filename={self.test_device} --direct=1 \
                --rw=write --bs=1m --ioengine=libaio --iodepth=64 \
                --size={total_size_gb}G --numjobs=1 \
                --output-format=json"""
            
            try:
                start_time = time.time()
                result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=7200)
                duration = time.time() - start_time
                
                fio_output = json.loads(result.stdout_text)
                write_bw = fio_output['jobs'][0]['write']['bw'] / 1024
                
                pass_results.append({
                    'pass': pass_num + 1,
                    'status': 'PASS',
                    'bandwidth_mb_s': write_bw,
                    'duration_sec': duration,
                    'size_gb': total_size_gb
                })
                
                self.log.info(f"✓ Pass {pass_num + 1}: {write_bw:.1f} MB/s, {duration:.0f}s")
                
            except Exception as e:
                pass_results.append({
                    'pass': pass_num + 1,
                    'status': 'FAIL',
                    'error': str(e)
                })
                self.log.error(f"Pass {pass_num + 1} failed: {e}")
        
        self.results['full_disk_sequential_write'] = {
            'passes': pass_results,
            'total_size_gb': total_size_gb,
            'num_passes': num_passes
        }
    
    def test_02_full_disk_sequential_read(self):
        """Full disk sequential read test"""
        self.log.info("Running FULL DISK sequential read test")
        
        total_size_gb = int(self.device_size_gb * 0.95)
        if TEST_MODE == 'quick':
            total_size_gb = min(total_size_gb, CONFIG['io_size_gb'])
        
        fio_cmd = f"""fio --name=full_seq_read \
            --filename={self.test_device} --direct=1 \
            --rw=read --bs=1m --ioengine=libaio --iodepth=64 \
            --size={total_size_gb}G --numjobs=1 \
            --output-format=json"""
        
        try:
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=7200)
            fio_output = json.loads(result.stdout_text)
            
            read_bw = fio_output['jobs'][0]['read']['bw'] / 1024
            
            self.results['full_disk_sequential_read'] = {
                'status': 'PASS',
                'bandwidth_mb_s': read_bw,
                'size_gb': total_size_gb
            }
            
            self.log.info(f"✓ Full disk sequential read: {read_bw:.1f} MB/s")
            
        except Exception as e:
            self.results['full_disk_sequential_read'] = 'FAIL'
            self.fail(f"Full disk read failed: {e}")
    
    def test_03_block_size_sweep_read(self):
        """Sweep all block sizes for read operations"""
        self.log.info("Running block size sweep (READ)")
        
        block_sizes = CONFIG['block_sizes']
        bs_results = {}
        
        for bs in block_sizes:
            self.log.info(f"Testing block size: {bs}")
            
            fio_cmd = f"""fio --name=bs_read_{bs} \
                --filename={self.test_device} --direct=1 \
                --rw=read --bs={bs} --ioengine=libaio --iodepth=32 \
                --size={CONFIG['io_size_gb']}G --numjobs=1 \
                --output-format=json"""
            
            try:
                result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=600)
                fio_output = json.loads(result.stdout_text)
                
                bw = fio_output['jobs'][0]['read']['bw'] / 1024
                iops = fio_output['jobs'][0]['read']['iops']
                
                bs_results[bs] = {
                    'bandwidth_mb_s': bw,
                    'iops': iops
                }
                
                self.log.info(f"  {bs}: {bw:.1f} MB/s, {iops:.0f} IOPS")
                
            except Exception as e:
                bs_results[bs] = 'FAIL'
                self.log.error(f"{bs} failed: {e}")
        
        self.results['block_size_sweep_read'] = bs_results
    
    def test_04_block_size_sweep_write(self):
        """Sweep all block sizes for write operations"""
        self.log.info("Running block size sweep (WRITE)")
        
        block_sizes = CONFIG['block_sizes']
        bs_results = {}
        
        for bs in block_sizes:
            self.log.info(f"Testing block size: {bs}")
            
            fio_cmd = f"""fio --name=bs_write_{bs} \
                --filename={self.test_device} --direct=1 \
                --rw=write --bs={bs} --ioengine=libaio --iodepth=32 \
                --size={CONFIG['io_size_gb']}G --numjobs=1 \
                --output-format=json"""
            
            try:
                result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=600)
                fio_output = json.loads(result.stdout_text)
                
                bw = fio_output['jobs'][0]['write']['bw'] / 1024
                iops = fio_output['jobs'][0]['write']['iops']
                
                bs_results[bs] = {
                    'bandwidth_mb_s': bw,
                    'iops': iops
                }
                
                self.log.info(f"  {bs}: {bw:.1f} MB/s, {iops:.0f} IOPS")
                
            except Exception as e:
                bs_results[bs] = 'FAIL'
                self.log.error(f"{bs} failed: {e}")
        
        self.results['block_size_sweep_write'] = bs_results
    
    def test_05_random_read_sweep(self):
        """Random read test across all block sizes"""
        self.log.info("Running random read sweep")
        
        block_sizes = CONFIG['block_sizes']
        results = {}
        
        for bs in block_sizes:
            self.log.info(f"Random read {bs}")
            
            fio_cmd = f"""fio --name=randread_{bs} \
                --filename={self.test_device} --direct=1 \
                --rw=randread --bs={bs} --ioengine=libaio --iodepth=128 \
                --runtime={CONFIG['fio_runtime']} --numjobs=4 \
                --time_based --group_reporting --output-format=json"""
            
            try:
                result = process.run(fio_cmd, sudo=SUDO, shell=True, 
                                   timeout=CONFIG['fio_runtime'] + 60)
                fio_output = json.loads(result.stdout_text)
                
                iops = fio_output['jobs'][0]['read']['iops']
                bw = fio_output['jobs'][0]['read']['bw'] / 1024
                lat_mean = fio_output['jobs'][0]['read']['lat_ns']['mean'] / 1000
                
                results[bs] = {
                    'iops': iops,
                    'bandwidth_mb_s': bw,
                    'latency_us': lat_mean
                }
                
                self.log.info(f"  {bs}: {iops:.0f} IOPS, {lat_mean:.0f}µs")
                
            except Exception as e:
                results[bs] = 'FAIL'
                self.log.error(f"{bs} failed: {e}")
        
        self.results['random_read_sweep'] = results
    
    def test_06_random_write_sweep(self):
        """Random write test across all block sizes"""
        self.log.info("Running random write sweep")
        
        block_sizes = CONFIG['block_sizes']
        results = {}
        
        for bs in block_sizes:
            self.log.info(f"Random write {bs}")
            
            fio_cmd = f"""fio --name=randwrite_{bs} \
                --filename={self.test_device} --direct=1 \
                --rw=randwrite --bs={bs} --ioengine=libaio --iodepth=128 \
                --runtime={CONFIG['fio_runtime']} --numjobs=4 \
                --time_based --group_reporting --output-format=json"""
            
            try:
                result = process.run(fio_cmd, sudo=SUDO, shell=True, 
                                   timeout=CONFIG['fio_runtime'] + 60)
                fio_output = json.loads(result.stdout_text)
                
                iops = fio_output['jobs'][0]['write']['iops']
                bw = fio_output['jobs'][0]['write']['bw'] / 1024
                lat_mean = fio_output['jobs'][0]['write']['lat_ns']['mean'] / 1000
                
                results[bs] = {
                    'iops': iops,
                    'bandwidth_mb_s': bw,
                    'latency_us': lat_mean
                }
                
                self.log.info(f"  {bs}: {iops:.0f} IOPS, {lat_mean:.0f}µs")
                
            except Exception as e:
                results[bs] = 'FAIL'
                self.log.error(f"{bs} failed: {e}")
        
        self.results['random_write_sweep'] = results
    
    def test_07_smart_health_check(self):
        """SMART health monitoring"""
        self.log.info("Checking SMART health")
        
        try:
            result = process.run(f"smartctl -a {self.test_device}", 
                               sudo=SUDO, ignore_status=True)
            
            health_passed = 'PASSED' in result.stdout_text
            
            self.results['smart_health'] = {
                'status': 'PASSED' if health_passed else 'UNKNOWN'
            }
            
            self.log.info(f"✓ SMART health: {'PASSED' if health_passed else 'UNKNOWN'}")
            
        except Exception as e:
            self.results['smart_health'] = 'UNAVAILABLE'
            self.log.info("SMART not available")
    
    def tearDown(self):

        """Cleanup and report"""
        self.log.info(f"Kernel test results: {json.dumps(self.results, indent=2)}")


class StorageUserspaceTests(Test):
    """True userspace tests using SPDK (kernel bypass via UIO/VFIO)"""
    
    def setUp(self):
        """Setup SPDK userspace environment"""
        self.log.info("=== Userspace Tests (SPDK - Kernel Bypass) ===")
        self.results = {}
        self.dev_mgr = StorageDeviceManager(self.log)
        
        # Check if SPDK is available
        self.log.info(f"Checking for SPDK at: {SPDK_PATH}")
        
        if not os.path.exists(SPDK_PATH):
            self.log.info(f"SPDK directory not found at {SPDK_PATH}")
            self._show_spdk_install_instructions()
            self.cancel(f"SPDK not installed at {SPDK_PATH}")
        
        self.log.info(f"✓ SPDK directory found: {SPDK_PATH}")
        
        # Try multiple possible locations for perf tool
        possible_perf_paths = [
            os.path.join(SPDK_PATH, 'build/bin/spdk_nvme_perf'),  # Most common location
            os.path.join(SPDK_PATH, 'build/examples/perf'),       # Older SPDK versions
            os.path.join(SPDK_PATH, 'examples/nvme/perf/perf'),   # Some builds
            os.path.join(SPDK_PATH, 'app/spdk_nvme_perf/spdk_nvme_perf'),  # Alternative
        ]
        
        self.spdk_perf = None
        for perf_path in possible_perf_paths:
            if os.path.exists(perf_path) and os.access(perf_path, os.X_OK):
                self.spdk_perf = perf_path
                self.log.info(f"✓ Found SPDK perf tool: {perf_path}")
                break
        
        if not self.spdk_perf:
            self.log.info("SPDK perf tool not found. Checked:")
            for path in possible_perf_paths:
                exists = "EXISTS" if os.path.exists(path) else "NOT FOUND"
                executable = "EXECUTABLE" if os.path.exists(path) and os.access(path, os.X_OK) else "NOT EXECUTABLE"
                self.log.info(f"  {path}: {exists}, {executable}")
            
            self.log.info("")
            self.log.info("SPDK directory exists but perf tool is not compiled.")
            self.log.info("To compile SPDK:")
            self.log.info(f"  cd {SPDK_PATH}")
            self.log.info("  sudo ./configure --with-nvme")
            self.log.info("  sudo make -j$(nproc)")
            self.log.info("")
            self.log.info("Or use the installation script:")
            self.log.info("  sudo ./install_spdk.sh")
            self.cancel("SPDK perf tool not found or not compiled")
        
        # Check for setup script
        self.spdk_setup = os.path.join(SPDK_PATH, 'scripts/setup.sh')
        if not os.path.exists(self.spdk_setup):
            self.log.info(f"SPDK setup script not found at: {self.spdk_setup}")
            self.cancel("SPDK setup script missing")
        
        self.log.info(f"✓ SPDK setup script found: {self.spdk_setup}")
        
        # Check for SPDK libraries
        spdk_lib_path = os.path.join(SPDK_PATH, 'build/lib')
        if os.path.exists(spdk_lib_path):
            lib_files = os.listdir(spdk_lib_path)
            spdk_libs = [f for f in lib_files if f.startswith('libspdk') and f.endswith('.so')]
            if spdk_libs:
                self.log.info(f"✓ Found {len(spdk_libs)} SPDK libraries in {spdk_lib_path}")
                self.log.info(f"  Will set LD_LIBRARY_PATH={spdk_lib_path}")
            else:
                self.log.info(f"⚠ No SPDK libraries found in {spdk_lib_path}")
        else:
            self.log.info(f"⚠ SPDK lib directory not found: {spdk_lib_path}")
            self.log.info("  SPDK tests may fail with library loading errors")
        
        # Check hugepages
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
                hugepages_free = 0
                hugepages_total = 0
                for line in meminfo.split('\n'):
                    if 'HugePages_Free' in line:
                        hugepages_free = int(line.split()[1])
                    elif 'HugePages_Total' in line:
                        hugepages_total = int(line.split()[1])
                
                if hugepages_total == 0:
                    self.log.info("⚠ No hugepages allocated")
                    self.log.info("Allocate hugepages with:")
                    self.log.info("  echo 1024 | sudo tee /proc/sys/vm/nr_hugepages")
                    self.cancel("No hugepages allocated")
                
                if hugepages_free < 512:
                    self.log.info(f"⚠ Only {hugepages_free}/{hugepages_total} hugepages free")
                    self.cancel(f"Insufficient free hugepages: {hugepages_free} (need 512+)")
                else:
                    self.log.info(f"✓ Hugepages: {hugepages_free}/{hugepages_total} free")
            
            # Check if hugetlbfs is mounted
            with open('/proc/mounts', 'r') as f:
                mounts = f.read()
                if 'hugetlbfs' not in mounts:
                    self.log.info("⚠ hugetlbfs not mounted")
                    self.log.info("Mounting hugetlbfs...")
                    try:
                        process.run("mkdir -p /mnt/huge", sudo=SUDO, shell=True, ignore_status=True)
                        process.run("mount -t hugetlbfs nodev /mnt/huge", sudo=SUDO, shell=True)
                        self.log.info("✓ Mounted hugetlbfs at /mnt/huge")
                    except Exception as e:
                        self.log.info(f"Could not mount hugetlbfs: {e}")
                        self.log.info("Try manually:")
                        self.log.info("  sudo mkdir -p /mnt/huge")
                        self.log.info("  sudo mount -t hugetlbfs nodev /mnt/huge")
                        self.cancel("hugetlbfs not mounted")
                else:
                    self.log.info("✓ hugetlbfs is mounted")
                    
        except Exception as e:
            self.log.debug(f"Could not check hugepages: {e}")
        
                # Get device information / target selection (SPDK needs a stable identifier)
        self.test_device = None
        self.pcie_addr = None

        # Prefer explicit PCIe address override (most stable for SPDK)
        if TEST_PCI_ADDR and is_pcie_bdf(TEST_PCI_ADDR):
            self.pcie_addr = normalize_pcie_bdf(TEST_PCI_ADDR)

        # TEST_DEVICE can be either /dev/nvmeXnY (recommended) or a PCI BDF for SPDK
        if TEST_DEVICE:
            if is_pcie_bdf(TEST_DEVICE):
                self.pcie_addr = normalize_pcie_bdf(TEST_DEVICE)
            else:
                self.test_device = TEST_DEVICE

        # For safety: never auto-pick a device for SPDK (setup.sh can affect multiple devices)
        if not self.test_device and not self.pcie_addr:
            self.cancel("SPDK tests require explicit target: set TEST_DEVICE=/dev/nvmeXnY or TEST_PCI_ADDR=dddd:bb:dd.f")

        # If we only have PCIe BDF, try to map it back to a local /dev node (best-effort for logging/safety)
        if self.pcie_addr and not self.test_device:
            try:
                for dev in self.dev_mgr.discover_nvme_devices():
                    bdf = self.dev_mgr.get_pcie_address(dev)
                    if bdf and bdf.lower() == self.pcie_addr:
                        self.test_device = dev
                        break
            except Exception:
                pass

        # If we only have /dev node, derive PCIe BDF
        if self.test_device and not self.pcie_addr:
            self.pcie_addr = self.dev_mgr.get_pcie_address(self.test_device)

        # Cache PCIe address so transient sysfs issues don't cancel later tests
        cache_key = os.path.basename(self.test_device) if self.test_device else (self.pcie_addr or "unknown")
        cache_key_safe = re.sub(r'[^0-9a-zA-Z_.-]+', '_', cache_key)
        cache_path = f"/tmp/spdk_pcie_addr.{cache_key_safe}"

        if not self.pcie_addr and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = f.read().strip()
                if cached and is_pcie_bdf(cached):
                    self.pcie_addr = normalize_pcie_bdf(cached)
                    self.log.info(f"Using cached PCIe address {self.pcie_addr} for {cache_key}")
            except Exception:
                pass

        if not self.pcie_addr:
            self.log.info(f"Could not determine PCIe address for {self.test_device or TEST_DEVICE}")
            self.log.info("Try finding it manually:")
            self.log.info("  lspci -Dnn | grep -Ei 'non-volatile|nvme|0108'")
            self.cancel("Could not determine PCIe address")

        # Save for subsequent tests
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(self.pcie_addr)
        except Exception:
            pass

        dev_label = self.test_device if self.test_device else f"PCIe {self.pcie_addr}"
        self.log.info(f"Testing target: {dev_label}")
        self.log.info(f"PCIe address: {self.pcie_addr}")

        # Take a coarse device lock to avoid multiple SPDK tests racing setup.sh
        self._lock_fd = acquire_device_lock(self.log, self.test_device or self.pcie_addr)
        #spdk_reset_if_available(self.log)   # ensure vfio didn’t steal the device
        # Setup SPDK (bind device to UIO/VFIO)
        self.log.info("")
        self.log.info("Setting up SPDK (binding device to UIO/VFIO)...")
        self.log.info("⚠ This will temporarily unbind the device from the kernel driver")
        
        try:
            # Check if device is already bound to SPDK
            result = process.run(f"{self.spdk_setup} status", sudo=SUDO, shell=True, ignore_status=True)
            status_output = result.stdout_text
            
            low = status_output.lower()
            if (self.pcie_addr in low) and (("vfio-pci" in low) or ("uio" in low)):
                self.log.info(f"✓ Device {self.pcie_addr} already bound to UIO/VFIO")
            else:
                # Bind device to SPDK
                self.log.info(f"Binding {self.pcie_addr} to SPDK...")
                result = process.run(f"{self.spdk_setup}", sudo=SUDO, timeout=30, ignore_status=True)
                
                if result.exit_status == 0:
                    self.log.info("✓ SPDK setup complete")
                else:
                    self.log.info(f"SPDK setup exited with code: {result.exit_status}")
                    self.log.debug(f"Output: {result.stdout_text[:500]}")
                
                # Verify device is bound
                result = process.run(f"{self.spdk_setup} status", sudo=SUDO, shell=True, ignore_status=True)
                status_output = result.stdout_text
                
                if self.pcie_addr not in status_output:
                    self.log.info(f"⚠ Device {self.pcie_addr} not found in SPDK status")
                    self.log.info("SPDK status output:")
                    self.log.info(status_output[:500])
                    self.cancel("Device not properly bound to SPDK")
                else:
                    self.log.info(f"✓ Device {self.pcie_addr} bound successfully")
                    
        except Exception as e:
            self.log.info(f"SPDK setup exception: {e}")
            self.log.info("Continuing anyway - test will fail if device not properly bound")
    
    def _show_spdk_install_instructions(self):
        """Show detailed SPDK installation instructions"""
        self.log.info("")
        self.log.info("=" * 70)
        self.log.info("SPDK NOT FOUND - Installation Instructions")
        self.log.info("=" * 70)
        self.log.info("")
        self.log.info("SPDK provides userspace (kernel bypass) NVMe testing.")
        self.log.info("It's OPTIONAL - storage tests work fine without it!")
        self.log.info("")
        self.log.info("To install SPDK:")
        self.log.info("")
        self.log.info("OPTION 1: Use installation script")
        self.log.info("  sudo ./install_spdk.sh")
        self.log.info("")
        self.log.info("OPTION 2: Manual installation")
        self.log.info("  sudo apt-get install -y git gcc g++ make libaio-dev libssl-dev")
        self.log.info("  sudo mkdir -p /usr/local/src && cd /usr/local/src")
        self.log.info("  sudo git clone https://github.com/spdk/spdk.git")
        self.log.info("  cd spdk && sudo git checkout v24.01")
        self.log.info("  sudo git submodule update --init")
        self.log.info("  sudo ./configure --with-nvme && sudo make -j$(nproc)")
        self.log.info("  echo 1024 | sudo tee /proc/sys/vm/nr_hugepages")
        self.log.info("")
        self.log.info("OPTION 3: Use custom path")
        self.log.info("  export SPDK_PATH=/your/custom/path")
        self.log.info("")
        self.log.info("=" * 70)
        self.log.info("")
    
    def test_01_spdk_sequential_read(self):
        """SPDK userspace sequential read"""
        self.log.info("Running SPDK sequential read test")
        
        runtime = CONFIG['fio_runtime']
        
        # Set up environment with SPDK library paths
        env = os.environ.copy()
        spdk_lib_path = os.path.join(SPDK_PATH, 'build/lib')
        if os.path.exists(spdk_lib_path):
            if 'LD_LIBRARY_PATH' in env:
                env['LD_LIBRARY_PATH'] = f"{spdk_lib_path}:{env['LD_LIBRARY_PATH']}"
            else:
                env['LD_LIBRARY_PATH'] = spdk_lib_path
            self.log.info(f"Added to LD_LIBRARY_PATH: {spdk_lib_path}")
        
        cmd = f"""{self.spdk_perf} -q 128 -o 131072 -w read -t {runtime} \
            -c 0x1 -r 'trtype:PCIe traddr:{self.pcie_addr}'"""
        
        try:
            result = process.run(cmd, sudo=SUDO, shell=True, timeout=runtime + 60, env=env)
            output = result.stdout_text
            
            # Parse SPDK output
            bw_match = re.search(r'Total\s+:\s+([\d.]+)\s+MB/s', output)
            iops_match = re.search(r'Total\s+:\s+([\d.]+)\s+IOPS', output)
            
            bw = float(bw_match.group(1)) if bw_match else 0
            iops = float(iops_match.group(1)) if iops_match else 0
            
            self.results['spdk_sequential_read'] = {
                'status': 'PASS',
                'bandwidth_mb_s': bw,
                'iops': iops
            }
            
            self.log.info(f"✓ SPDK seq read: {bw:.1f} MB/s, {iops:.0f} IOPS")
            
        except Exception as e:
            self.results['spdk_sequential_read'] = 'FAIL'
            self.fail(f"SPDK seq read failed: {e}")
    
    def test_02_spdk_random_read_4k(self):
        """SPDK userspace random 4K read"""
        self.log.info("Running SPDK random 4K read")
        
        runtime = CONFIG['fio_runtime']
        
        # Set up environment with SPDK library paths
        env = os.environ.copy()
        spdk_lib_path = os.path.join(SPDK_PATH, 'build/lib')
        if os.path.exists(spdk_lib_path):
            if 'LD_LIBRARY_PATH' in env:
                env['LD_LIBRARY_PATH'] = f"{spdk_lib_path}:{env['LD_LIBRARY_PATH']}"
            else:
                env['LD_LIBRARY_PATH'] = spdk_lib_path
        
        cmd = f"""{self.spdk_perf} -q 128 -o 4096 -w randread -t {runtime} \
            -c 0xF -r 'trtype:PCIe traddr:{self.pcie_addr}'"""
        
        try:
            result = process.run(cmd, sudo=SUDO, shell=True, timeout=runtime + 60, env=env)
            output = result.stdout_text
            
            iops_match = re.search(r'Total\s+:\s+([\d.]+)\s+IOPS', output)
            lat_match = re.search(r'Average\s+:\s+([\d.]+)\s+us', output)
            
            iops = float(iops_match.group(1)) if iops_match else 0
            lat = float(lat_match.group(1)) if lat_match else 0
            
            self.results['spdk_random_4k_read'] = {
                'status': 'PASS',
                'iops': iops,
                'latency_us': lat
            }
            
            self.log.info(f"✓ SPDK 4K random read: {iops:.0f} IOPS, {lat:.1f}µs")
            
        except Exception as e:
            self.results['spdk_random_4k_read'] = 'FAIL'
            self.fail(f"SPDK random read failed: {e}")
    
    def test_03_spdk_queue_depth_sweep(self):
        """SPDK QD sweep to show userspace performance scaling"""
        self.log.info("Running SPDK queue depth sweep")
        
        queue_depths = CONFIG['queue_depths'][:5]  # Limit for quick test
        qd_results = {}
        runtime = 30
        
        # Set up environment with SPDK library paths
        env = os.environ.copy()
        spdk_lib_path = os.path.join(SPDK_PATH, 'build/lib')
        if os.path.exists(spdk_lib_path):
            if 'LD_LIBRARY_PATH' in env:
                env['LD_LIBRARY_PATH'] = f"{spdk_lib_path}:{env['LD_LIBRARY_PATH']}"
            else:
                env['LD_LIBRARY_PATH'] = spdk_lib_path
        
        for qd in queue_depths:
            self.log.info(f"Testing QD={qd}")
            
            cmd = f"""{self.spdk_perf} -q {qd} -o 4096 -w randread -t {runtime} \
                -c 0x1 -r 'trtype:PCIe traddr:{self.pcie_addr}'"""
            
            try:
                result = process.run(cmd, sudo=SUDO, shell=True, timeout=runtime + 30, env=env)
                output = result.stdout_text
                
                iops_match = re.search(r'Total\s+:\s+([\d.]+)\s+IOPS', output)
                lat_match = re.search(r'Average\s+:\s+([\d.]+)\s+us', output)
                
                iops = float(iops_match.group(1)) if iops_match else 0
                lat = float(lat_match.group(1)) if lat_match else 0
                
                qd_results[f'qd{qd}'] = {
                    'iops': iops,
                    'latency_us': lat
                }
                
                self.log.info(f"  QD{qd}: {iops:.0f} IOPS, {lat:.1f}µs")
                
            except Exception as e:
                qd_results[f'qd{qd}'] = 'FAIL'
                self.log.error(f"QD{qd} failed: {e}")
        
        self.results['spdk_qd_sweep'] = qd_results
    
    def tearDown(self):

        """Cleanup SPDK and rebind to kernel"""
        # Only try to cleanup if setup succeeded
        if not hasattr(self, 'spdk_setup'):
            return
        
        self.log.info("Cleaning up SPDK...")
        
        try:
            reset_script = os.path.join(SPDK_PATH, 'scripts/setup.sh')
            if os.path.exists(reset_script):
                process.run(f"{reset_script} reset", sudo=SUDO, timeout=30, ignore_status=True)
                self.log.info("✓ Device returned to kernel driver")
        except Exception as e:
            self.log.debug(f"SPDK cleanup note: {e}")
        
        # Release device lock if held
        try:
            if hasattr(self, "_lock_fd") and self._lock_fd:
                release_device_lock(self.log, self._lock_fd)
        except Exception:
            pass

        self.log.info(f"Userspace test results: {json.dumps(self.results, indent=2)}")

class StorageDatacenterTests(Test):
    """Datacenter application-level tests"""
    
    def setUp(self):
        """Setup datacenter tests"""
        self.log.info("=== Datacenter Application Tests ===")
        self.results = {}
        self.dev_mgr = StorageDeviceManager(self.log)
        self.sm = SoftwareManager()
        
        if not self.sm.check_installed('fio'):
            self.sm.install('fio')
        
        if TEST_DEVICE:
            self.test_device = TEST_DEVICE
        else:
            devices = self.dev_mgr.discover_nvme_devices()
            if not devices:
                self.cancel("No NVMe devices found")
            self.test_device = devices[0]
        
        is_safe, warnings = self.dev_mgr.check_device_safety(self.test_device)
        if not is_safe:
            self.cancel(f"Device not safe: {warnings}")
    
    def test_01_database_oltp(self):
        """OLTP database workload (80/20 read/write mix)"""
        self.log.info("Running OLTP database workload")
        
        runtime = CONFIG['fio_runtime']
        
        fio_cmd = f"""fio --name=oltp --filename={self.test_device} --direct=1 \
            --rw=randrw --rwmixread=80 --bs=8k --ioengine=libaio --iodepth=128 \
            --runtime={runtime} --numjobs=8 --time_based --group_reporting \
            --output-format=json"""
        
        try:
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=runtime + 60)
            fio_output = json.loads(result.stdout_text)
            
            job_data = fio_output['jobs'][0]
            read_iops = job_data['read']['iops']
            write_iops = job_data['write']['iops']
            
            # Try to get p99 latency, but don't fail if not available
            read_lat_99 = 0
            try:
                if 'clat_ns' in job_data['read'] and 'percentile' in job_data['read']['clat_ns']:
                    read_lat_99 = job_data['read']['clat_ns']['percentile'].get('99.000000', 0) / 1000
                elif 'lat_ns' in job_data['read'] and 'percentile' in job_data['read']['lat_ns']:
                    read_lat_99 = job_data['read']['lat_ns']['percentile'].get('99.000000', 0) / 1000
                else:
                    # Use mean if percentile not available
                    if 'lat_ns' in job_data['read']:
                        read_lat_99 = job_data['read']['lat_ns'].get('mean', 0) / 1000
            except:
                pass
            
            self.results['oltp_workload'] = {
                'status': 'PASS',
                'read_iops': read_iops,
                'write_iops': write_iops,
                'read_lat_99th_us': read_lat_99 if read_lat_99 > 0 else 'N/A'
            }
            
            self.log.info(f"✓ OLTP: {read_iops:.0f} read IOPS, {write_iops:.0f} write IOPS")
            if read_lat_99 > 0:
                self.log.info(f"  p99 latency: {read_lat_99:.0f}µs")
            
        except Exception as e:
            self.results['oltp_workload'] = 'FAIL'
            self.fail(f"OLTP test failed: {e}")
    
    def test_02_log_streaming(self):
        """Log/streaming write workload"""
        self.log.info("Running log streaming workload")
        
        runtime = CONFIG['fio_runtime']
        
        fio_cmd = f"""fio --name=streaming --filename={self.test_device} --direct=1 \
            --rw=write --bs=1m --ioengine=libaio --iodepth=16 \
            --runtime={runtime} --numjobs=4 --time_based --group_reporting \
            --output-format=json"""
        
        try:
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=runtime + 60)
            fio_output = json.loads(result.stdout_text)
            
            write_bw = fio_output['jobs'][0]['write']['bw'] / 1024
            
            self.results['log_streaming'] = {
                'status': 'PASS',
                'bandwidth_mb_s': write_bw
            }
            
            self.log.info(f"✓ Log streaming: {write_bw:.1f} MB/s")
            
        except Exception as e:
            self.results['log_streaming'] = 'FAIL'
            self.fail(f"Streaming test failed: {e}")
    
    def test_03_mixed_workload(self):
        """Mixed workload (50/50 read/write)"""
        self.log.info("Running mixed 50/50 workload")
        
        runtime = CONFIG['fio_runtime']
        
        fio_cmd = f"""fio --name=mixed --filename={self.test_device} --direct=1 \
            --rw=randrw --rwmixread=50 --bs=4k --ioengine=libaio --iodepth=64 \
            --runtime={runtime} --numjobs=8 --time_based --group_reporting \
            --output-format=json"""
        
        try:
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=runtime + 60)
            fio_output = json.loads(result.stdout_text)
            
            read_iops = fio_output['jobs'][0]['read']['iops']
            write_iops = fio_output['jobs'][0]['write']['iops']
            
            self.results['mixed_workload'] = {
                'status': 'PASS',
                'read_iops': read_iops,
                'write_iops': write_iops,
                'total_iops': read_iops + write_iops
            }
            
            self.log.info(f"✓ Mixed 50/50: {read_iops:.0f} R + {write_iops:.0f} W = {read_iops + write_iops:.0f} total IOPS")
            
        except Exception as e:
            self.results['mixed_workload'] = 'FAIL'
            self.fail(f"Mixed workload failed: {e}")
    
    def tearDown(self):
        """Cleanup"""
        self.log.info(f"Datacenter test results: {json.dumps(self.results, indent=2)}")


class StorageBenchmarkTests(Test):
    """Comprehensive storage benchmarks and characterization"""
    
    def setUp(self):
        """Setup benchmark environment"""
        self.log.info("=== Storage Benchmark Tests ===")
        self.results = {}
        self.dev_mgr = StorageDeviceManager(self.log)
        self.sm = SoftwareManager()
        
        if not self.sm.check_installed('fio'):
            self.sm.install('fio')
        
        if TEST_DEVICE:
            self.test_device = TEST_DEVICE
        else:
            devices = self.dev_mgr.discover_nvme_devices()
            if not devices:
                self.cancel("No NVMe devices found")
            self.test_device = devices[0]
        
        is_safe, warnings = self.dev_mgr.check_device_safety(self.test_device)
        if not is_safe:
            self.cancel(f"Device not safe: {warnings}")
    
    def test_01_queue_depth_scaling(self):
        """Comprehensive QD scaling test"""
        self.log.info("Running comprehensive queue depth scaling")
        
        queue_depths = CONFIG['queue_depths']
        qd_results = {}
        runtime = 30
        
        for qd in queue_depths:
            self.log.info(f"Testing QD={qd}")
            
            fio_cmd = f"""fio --name=qd{qd} --filename={self.test_device} --direct=1 \
                --rw=randread --bs=4k --ioengine=libaio --iodepth={qd} \
                --runtime={runtime} --numjobs=1 --time_based --group_reporting \
                --output-format=json"""
            
            try:
                result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=runtime + 30)
                fio_output = json.loads(result.stdout_text)
                
                iops = fio_output['jobs'][0]['read']['iops']
                lat_mean = fio_output['jobs'][0]['read']['lat_ns']['mean'] / 1000
                lat_p99 = fio_output['jobs'][0]['read']['lat_ns']['percentile']['99.000000'] / 1000
                
                qd_results[f'qd{qd}'] = {
                    'iops': iops,
                    'latency_mean_us': lat_mean,
                    'latency_p99_us': lat_p99
                }
                
                self.log.info(f"  QD{qd}: {iops:.0f} IOPS, {lat_mean:.1f}µs avg, {lat_p99:.0f}µs p99")
                
            except Exception as e:
                qd_results[f'qd{qd}'] = 'FAIL'
                self.log.error(f"QD{qd} failed: {e}")
        
        self.results['queue_depth_scaling'] = qd_results
    
    def test_02_latency_percentiles(self):
        """Detailed latency distribution"""
        self.log.info("Measuring latency percentiles")
        
        runtime = CONFIG['fio_runtime']
        
        fio_cmd = f"""fio --name=latency --filename={self.test_device} --direct=1 \
            --rw=randread --bs=4k --ioengine=libaio --iodepth=32 \
            --runtime={runtime} --numjobs=1 --time_based --group_reporting \
            --output-format=json"""
        
        try:
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=runtime + 60)
            fio_output = json.loads(result.stdout_text)
            
            # Check if percentile data exists
            if 'jobs' not in fio_output or len(fio_output['jobs']) == 0:
                self.fail("No fio job data returned")
            
            job_data = fio_output['jobs'][0]
            
            # Try different fio output formats
            percentiles = {}
            
            # Try clat_ns (newer fio versions)
            if 'read' in job_data and 'clat_ns' in job_data['read']:
                lat_pct = job_data['read']['clat_ns'].get('percentile', {})
                for pct_key, pct_val in lat_pct.items():
                    try:
                        pct_num = float(pct_key)
                        if pct_num == 50.0:
                            percentiles['50th_us'] = pct_val / 1000
                        elif pct_num == 90.0:
                            percentiles['90th_us'] = pct_val / 1000
                        elif pct_num == 95.0:
                            percentiles['95th_us'] = pct_val / 1000
                        elif pct_num == 99.0:
                            percentiles['99th_us'] = pct_val / 1000
                        elif pct_num == 99.9:
                            percentiles['99.9th_us'] = pct_val / 1000
                        elif pct_num == 99.99:
                            percentiles['99.99th_us'] = pct_val / 1000
                    except:
                        pass
            
            # Try lat_ns (alternative format)
            elif 'read' in job_data and 'lat_ns' in job_data['read'] and 'percentile' in job_data['read']['lat_ns']:
                lat_pct = job_data['read']['lat_ns']['percentile']
                percentiles = {
                    '50th_us': lat_pct.get('50.000000', 0) / 1000,
                    '90th_us': lat_pct.get('90.000000', 0) / 1000,
                    '95th_us': lat_pct.get('95.000000', 0) / 1000,
                    '99th_us': lat_pct.get('99.000000', 0) / 1000,
                    '99.9th_us': lat_pct.get('99.900000', 0) / 1000,
                    '99.99th_us': lat_pct.get('99.990000', 0) / 1000
                }
            
            # Use mean latency if percentiles not available
            else:
                if 'read' in job_data and 'lat_ns' in job_data['read']:
                    mean_lat = job_data['read']['lat_ns'].get('mean', 0) / 1000
                    percentiles = {
                        'mean_us': mean_lat,
                        'note': 'Percentiles not available in fio output, showing mean only'
                    }
                else:
                    self.fail("Could not extract latency data from fio output")
            
            self.results['latency_percentiles'] = percentiles
            
            self.log.info(f"✓ Latency percentiles (µs):")
            for p, val in percentiles.items():
                if isinstance(val, (int, float)):
                    self.log.info(f"  {p}: {val:.1f}µs")
                else:
                    self.log.info(f"  {p}: {val}")
            
        except json.JSONDecodeError as e:
            self.results['latency_percentiles'] = 'FAIL'
            self.fail(f"Failed to parse fio JSON output: {e}")
        except Exception as e:
            self.results['latency_percentiles'] = 'FAIL'
            self.fail(f"Latency test failed: {e}")
    
    def test_03_sustained_performance(self):
        """Long-duration sustained performance test"""
        self.log.info("Running sustained performance test")
        
        # Use io_size_gb instead of duration_sec for this test
        size_gb = CONFIG['io_size_gb']
        
        fio_cmd = f"""fio --name=sustained --filename={self.test_device} --direct=1 \
            --rw=write --bs=128k --ioengine=libaio --iodepth=32 \
            --size={size_gb}G --numjobs=1 \
            --output-format=json"""
        
        try:
            # Calculate timeout based on size (assume worst case 100 MB/s)
            timeout = int((size_gb * 1024 / 100) + 300)  # Size in MB / 100 MB/s + 5 min buffer
            
            start_time = time.time()
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=timeout)
            actual_duration = time.time() - start_time
            
            fio_output = json.loads(result.stdout_text)
            write_bw = fio_output['jobs'][0]['write']['bw'] / 1024
            
            self.results['sustained_performance'] = {
                'status': 'PASS',
                'bandwidth_mb_s': write_bw,
                'duration_sec': actual_duration,
                'size_gb': size_gb
            }
            
            self.log.info(f"✓ Sustained write: {write_bw:.1f} MB/s for {actual_duration:.0f}s ({size_gb}GB)")
            
        except Exception as e:
            self.results['sustained_performance'] = 'FAIL'
            self.fail(f"Sustained test failed: {e}")
    
    def tearDown(self):
        """Save benchmark results"""
        self.log.info(f"Benchmark results: {json.dumps(self.results, indent=2)}")
        
        results_file = os.path.join(self.outputdir, 'storage_benchmark_results.json')
        with open(results_file, 'w') as f:
            json.dump({
                'device': self.test_device if hasattr(self, 'test_device') else 'unknown',
                'test_mode': TEST_MODE,
                'config': CONFIG,
                'results': self.results,
                'timestamp': time.time()
            }, f, indent=2)
        self.log.info(f"✓ Results saved to {results_file}")


class StorageFilesystemTests(Test):
    """Filesystem-level storage tests (non-raw).

    For best coverage, mount the target storage device at TEST_FS_DIR (or set TEST_FS_DIR accordingly)
    before running these tests.
    """

    def setUp(self):
        self.log.info("=== Storage Filesystem Tests ===")
        self.results = {}
        self.sm = SoftwareManager()
        self.fs_dir = TEST_FS_DIR

        # Ensure directory exists
        try:
            os.makedirs(self.fs_dir, exist_ok=True)
        except Exception as e:
            self.cancel(f"Could not create TEST_FS_DIR={self.fs_dir}: {e}")

        # Optional tools
        for tool in ["fio", "stress-ng"]:
            if not self.sm.check_installed(tool):
                self.log.info(f"Installing {tool}")
                self.sm.install(tool)

    def test_01_fio_file_integrity_verify(self):
        """Write+read verify on a test file (CRC verify).

        Notes:
          * fio's JSON output can occasionally be empty or polluted on stdout (e.g., wrappers/sudo noise).
          * To make parsing robust, we always ask fio to write JSON to an output file and parse that.
        """
        self.log.info("Running fio file verify (CRC)")
        testfile = os.path.join(self.fs_dir, "fio_verify.dat")

        runtime = CONFIG.get("fio_runtime", 60)
        size = "4G" if TEST_MODE != "quick" else "1G"

        # Prefer parsing from an output file (more reliable than stdout).
        out_json = os.path.join(self.fs_dir, f"fio_verify_{int(time.time())}.json")

        fio_cmd = f"""fio --name=verify --filename={testfile} --direct=1 \
            --rw=randwrite --bs=4k --ioengine=libaio --iodepth=32 \
            --size={size} --runtime={runtime} --time_based --numjobs=1 \
            --verify=crc32c --do_verify=1 --verify_fatal=1 --group_reporting \
            --output={out_json} --output-format=json"""

        try:
            result = process.run(fio_cmd, sudo=SUDO, shell=True, timeout=runtime + 120)

            raw = ""
            if os.path.exists(out_json):
                try:
                    raw = open(out_json, "r", encoding="utf-8", errors="replace").read()
                except Exception:
                    raw = ""
            if not raw.strip():
                # Fallback: some fio builds still emit JSON to stdout
                raw = (result.stdout_text or "")

            raw_s = (raw or "").strip()
            if not raw_s:
                raise ValueError(
                    "fio produced empty output (expected JSON). "
                    f"exit_status={getattr(result, 'exit_status', 'n/a')}. "
                    f"stdout_len={len(result.stdout_text or '')}, stderr_len={len(result.stderr_text or '')}."
                )

            try:
                fio_output = json.loads(raw_s)
            except Exception:
                # Best-effort: extract the first JSON object from the captured text
                i = raw_s.find("{")
                j = raw_s.rfind("}")
                if i != -1 and j != -1 and j > i:
                    fio_output = json.loads(raw_s[i : j + 1])
                else:
                    raise

            # fio schema: per-job errors are usually in jobs[i]['error']
            errors = 0
            try:
                errors = int(fio_output.get("jobs", [{}])[0].get("error", 0))
            except Exception:
                errors = 0

            self.results["fio_file_verify"] = {
                "status": "PASS" if errors == 0 else "FAIL",
                "errors": errors,
                "file": testfile,
                "json": out_json,
            }

            if errors != 0:
                self.fail(f"fio verify reported errors: {errors}")

            self.log.info("✓ fio file verify passed")
        except Exception as e:
            # Include some context to make failures actionable.
            ctx = ""
            try:
                ctx = (result.stderr_text or "").strip()[:400]
            except Exception:
                pass
            self.results["fio_file_verify"] = "FAIL"
            if ctx:
                self.fail(f"fio file verify failed: {e}; stderr_head={ctx!r}")
            else:
                self.fail(f"fio file verify failed: {e}")

    def test_02_filesystem_metadata_stress(self):
        """Metadata stress (best-effort): fsstress if available, else stress-ng iomix."""
        self.log.info("Running filesystem metadata stress (best-effort)")
        duration = 120 if TEST_MODE == "quick" else 600
        fsstress = "fsstress"
        try:
            which = process.run(f"which {fsstress}", shell=True, ignore_status=True)
            if which.exit_status == 0:
                cmd = f"""{fsstress} -d {self.fs_dir} -n 5000 -p 10 -f range=0,1024"""
                process.run(cmd, shell=True, timeout=duration + 60, ignore_status=True)
                self.results["fsstress"] = {"status": "DONE", "tool": "fsstress", "dir": self.fs_dir}
                self.log.info("✓ fsstress completed")
                return
        except Exception:
            pass

        # Fallback to stress-ng iomix
        try:
            cmd = f"""stress-ng --iomix 1 --iomix-bytes 1G --timeout {duration}s --temp-path {self.fs_dir} --metrics-brief"""
            process.run(cmd, shell=True, timeout=duration + 60, ignore_status=True)
            self.results["fsstress"] = {"status": "DONE", "tool": "stress-ng iomix", "dir": self.fs_dir}
            self.log.info("✓ stress-ng iomix completed")
        except Exception as e:
            self.results["fsstress"] = "FAIL"
            self.fail(f"Filesystem stress failed: {e}")

    def tearDown(self):
        self.log.info(f"Filesystem test results: {json.dumps(self.results, indent=2)}")


class StorageApplicationTests(Test):
    """Lightweight application-level tests (DB-like)."""

    def setUp(self):
        self.log.info("=== Storage Application Tests ===")
        self.results = {}
        self.fs_dir = TEST_FS_DIR
        try:
            os.makedirs(self.fs_dir, exist_ok=True)
        except Exception as e:
            self.cancel(f"Could not create TEST_FS_DIR={self.fs_dir}: {e}")

    def test_01_sqlite_insert_select(self):
        """SQLite insert/select workload on TEST_FS_DIR."""
        import sqlite3
        import time as _time

        db_path = os.path.join(self.fs_dir, "app_sqlite.db")
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass

        n_rows = 20000 if TEST_MODE == "quick" else (200000 if TEST_MODE == "normal" else 500000)
        self.log.info(f"SQLite workload: {n_rows} rows at {db_path}")

        t0 = _time.time()
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=FULL;")
        cur.execute("CREATE TABLE t(k INTEGER PRIMARY KEY, v TEXT);")

        try:
            cur.execute("BEGIN;")
            for i in range(n_rows):
                cur.execute("INSERT INTO t(k, v) VALUES(?, ?);", (i, "x" * 200))
            conn.commit()

            t1 = _time.time()
            cur.execute("SELECT COUNT(*) FROM t;")
            count = cur.fetchone()[0]
            t2 = _time.time()

            if count != n_rows:
                self.results["sqlite"] = {"status": "FAIL", "count": count, "expected": n_rows}
                self.fail(f"SQLite count mismatch: got {count}, expected {n_rows}")

            ins_rate = n_rows / max(1e-6, (t1 - t0))
            sel_rate = 1 / max(1e-6, (t2 - t1))
            self.results["sqlite"] = {
                "status": "PASS",
                "rows": n_rows,
                "insert_rows_per_sec": ins_rate,
                "select_ops_per_sec": sel_rate,
                "db_path": db_path,
            }
            self.log.info(f"✓ SQLite insert rate: {ins_rate:.0f} rows/s, select: {sel_rate:.2f} ops/s")
        finally:
            conn.close()

    def tearDown(self):
        self.log.info(f"Application test results: {json.dumps(self.results, indent=2)}")
